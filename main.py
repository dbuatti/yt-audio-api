import os
import threading
import time
import uuid
import sys
import gc
from flask import Flask, jsonify
from flask_cors import CORS
import yt_dlp
from supabase import create_client, Client

# --- Startup Log ---
print("[SYSTEM] >>> PYTHON WORKER SCRIPT INITIALIZING <<<", flush=True)

app = Flask(__name__)
CORS(app)

# --- Configuration ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase Environment Variables!", flush=True)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Safety Controls: 1 task at a time for 512MB RAM stability
download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    """Custom log function with immediate flushing for real-time Render logs."""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [WORKER] {message}", flush=True)

# --- Extraction Logic ---

def process_queued_song(song):
    song_id = song.get('id')
    video_url = song.get('youtube_url')
    user_id = song.get('user_id')
    title = song.get('title', 'Unknown Title')

    # Path logic: Look for cookies.txt in the root directory
    cookie_path = os.path.join(os.getcwd(), 'cookies.txt')
    has_cookies = os.path.exists(cookie_path)
    
    if has_cookies:
        log(f"Using cookies.txt bypass for {title}")
    else:
        log(f"WARNING: No cookies.txt found at {cookie_path}. High risk of 403/429 errors.")

    with download_semaphore:
        try:
            log(f">>> STARTING EXTRACTION: {title}")
            
            # 1. Mark as processing in DB
            supabase.table("repertoire").update({"extraction_status": "processing"}).eq("id", song_id).execute()

            file_id = str(uuid.uuid4())
            output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")
            
            # MODERN BYPASS OPTIONS (FEB 2026 READY)
            ydl_opts = {
                'format': 'bestaudio/best', 
                'noplaylist': True,
                'outtmpl': output_template,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }],
                'cookiefile': cookie_path if has_cookies else None,
                'extractor_args': {
                    'youtube': {
                        # 'web_safari' is currently the most resilient client
                        # Removed 'android' as it ignores cookies and causes 403s
                        'player_client': ['web_safari', 'web', 'mweb'],
                        'skip': ['hls', 'dash'],
                    }
                },
                # Tells yt-dlp to use the Node/Deno runtimes we installed in Docker
                'allow_unplayable_formats': True,
                'external_downloader_args': ['--remote-components', 'ejs:github'],
                
                # High-quality User Agent to match cookies
                'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
                'nocheckcertificate': True,
                'quiet': False,
                'no_warnings': False,
                'retries': 5,
                'fragment_retries': 10,
            }

            # 2. Download from YouTube
            log(f"Downloading from YouTube: {video_url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
                
                # Path to the processed MP3
                mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            
            # Verification check
            if os.path.exists(mp3_path):
                file_size = os.path.getsize(mp3_path)
                if file_size == 0:
                    raise Exception("Downloaded file is 0 bytes. Signature solving likely failed.")
                
                log(f"Upload starting for {title} ({file_size} bytes)...")
                
                # Storage path
                storage_path = f"{user_id}/{song_id}/{int(time.time())}.mp3"
                
                # 3. Upload file to Supabase Storage
                with open(mp3_path, 'rb') as f:
                    supabase.storage.from_("public_audio").upload(
                        path=storage_path, 
                        file=f,
                        file_options={"content-type": "audio/mpeg", "x-upsert": "true"}
                    )
                
                # 4. Get the Public URL
                public_url = supabase.storage.from_("public_audio").get_public_url(storage_path)
                
                # 5. Update DB
                supabase.table("repertoire").update({
                    "audio_url": public_url,
                    "preview_url": public_url,
                    "extraction_status": "completed",
                    "extraction_error": None
                }).eq("id", song_id).execute()
                
                log(f"SUCCESS: Finished {title}")
                
            else:
                raise Exception("FFmpeg/yt-dlp failed to produce MP3 file.")
            
        except Exception as e:
            error_msg = str(e)
            log(f"FAILED: {title} | Error: {error_msg}")
            
            try:
                # Update status to failed so user sees why
                supabase.table("repertoire").update({
                    "extraction_status": "failed",
                    "extraction_error": error_msg[:250]
                }).eq("id", song_id).execute()
            except Exception as db_err:
                log(f"Supabase update failed: {db_err}")
        finally:
            # Cleanup all temporary files matching this file_id
            if 'file_id' in locals():
                for f in os.listdir(DOWNLOAD_DIR):
                    if file_id in f:
                        try:
                            os.remove(os.path.join(DOWNLOAD_DIR, f))
                        except:
                            pass
            gc.collect()

def job_poller():
    """Background loop that checks Supabase every 20 seconds."""
    log("Job Poller initialization complete. Scanning for work...")
    while True:
        try:
            # Look for jobs marked as 'queued'
            res = supabase.table("repertoire")\
                .select("id, youtube_url, user_id, title")\
                .eq("extraction_status", "queued")\
                .limit(1)\
                .execute()
            
            if res.data and len(res.data) > 0:
                log(f"Job found! Processing: {res.data[0].get('title')}")
                process_queued_song(res.data[0])
            else:
                # Idle wait
                time.sleep(20)
                
        except Exception as e:
            log(f"Poller Loop Exception: {e}")
            time.sleep(30)

# Start poller thread
worker_thread = threading.Thread(target=job_poller, daemon=True)
worker_thread.start()

# --- Health Check Route ---
@app.route('/')
def status():
    return jsonify({
        "status": "online",
        "worker_active": worker_thread.is_alive(),
        "mode": "background_worker",
        "timestamp": time.strftime("%H:%M:%S")
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
