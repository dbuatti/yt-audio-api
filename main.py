import os
import threading
import time
import uuid
import sys
import gc
import random
from flask import Flask, jsonify
from flask_cors import CORS
import yt_dlp
from supabase import create_client, Client

# --- Startup Log ---
print("[SYSTEM] >>> 2026 PRODUCTION WORKER INITIALIZING <<<", flush=True)

app = Flask(__name__)
CORS(app)

# --- Configuration ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
PO_TOKEN = os.environ.get("YOUTUBE_PO_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase Environment Variables!", flush=True)
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Safety Controls: 1 task at a time for 512MB RAM stability
download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    """Custom log function with immediate flushing for Render."""
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [WORKER] {message}", flush=True)

# --- Extraction Logic ---

def process_queued_song(song):
    song_id = song.get('id')
    video_url = song.get('youtube_url')
    user_id = song.get('user_id')
    title = song.get('title', 'Unknown Title')

    # Path logic: Ensure absolute path for the cookie file
    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    has_cookies = os.path.exists(cookie_path)
    
    if not has_cookies:
        log(f"WARNING: No cookies.txt found. YouTube will likely block this request.")

    with download_semaphore:
        try:
            # ANTI-BOT COOLDOWN: Wait 3-7 seconds before starting
            wait_time = random.uniform(3, 7)
            log(f"Cooldown active: waiting {wait_time:.2f}s to avoid 429 errors...")
            time.sleep(wait_time)

            log(f">>> STARTING EXTRACTION: {title}")
            
            # 1. Update Status
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
                
                # Use Deno (installed via Docker) to solve JS challenges
                'js_runtimes': {'deno': {}},
                'remote_components': ['ejs:npm', 'ejs:github'],
                
                'extractor_args': {
                    'youtube': {
                        # iOS/Android are currently more resilient against 403s on Cloud IPs
                        'player_client': ['ios', 'android', 'web_safari'],
                        'skip': ['hls', 'dash'],
                    }
                },
                
                # Injection of PO Token if you have it in Render Env
                'po_token': f'web+{PO_TOKEN}' if PO_TOKEN and PO_TOKEN.strip() else None,
                
                'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
                'nocheckcertificate': True,
                'quiet': False,
                'no_warnings': False,
                'retries': 5,
                'fragment_retries': 10,
            }

            # 2. Download from YouTube
            log(f"Attempting download: {video_url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
                
            mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            
            # 3. Verification & Upload
            if os.path.exists(mp3_path):
                file_size = os.path.getsize(mp3_path)
                log(f"Success! Uploading {file_size:,} bytes to Supabase...")
                
                storage_path = f"{user_id}/{song_id}/{int(time.time())}.mp3"
                
                with open(mp3_path, 'rb') as f:
                    supabase.storage.from_("public_audio").upload(
                        path=storage_path, 
                        file=f,
                        file_options={"content-type": "audio/mpeg", "x-upsert": "true"}
                    )
                
                public_url = supabase.storage.from_("public_audio").get_public_url(storage_path)
                
                # 4. Update Database
                supabase.table("repertoire").update({
                    "audio_url": public_url,
                    "preview_url": public_url,
                    "extraction_status": "completed",
                    "extraction_error": None
                }).eq("id", song_id).execute()
                
                log(f"FINISHED: {title}")
                
            else:
                raise Exception("Yt-dlp finished but MP3 file was not created.")
            
        except Exception as e:
            error_msg = str(e)
            log(f"FAILED: {title} | Error: {error_msg}")
            
            # Mark failure in DB so it doesn't loop forever
            try:
                supabase.table("repertoire").update({
                    "extraction_status": "failed",
                    "extraction_error": error_msg[:250]
                }).eq("id", song_id).execute()
            except Exception as db_err:
                log(f"Final DB Update failed: {db_err}")

        finally:
            # Cleanup temp files
            if 'file_id' in locals():
                for f in os.listdir(DOWNLOAD_DIR):
                    if file_id in f:
                        try: os.remove(os.path.join(DOWNLOAD_DIR, f))
                        except: pass
            gc.collect()

def job_poller():
    """Background loop checking for 'queued' songs."""
    log("Worker scanning for queued jobs...")
    while True:
        try:
            res = supabase.table("repertoire")\
                .select("id, youtube_url, user_id, title")\
                .eq("extraction_status", "queued")\
                .limit(1)\
                .execute()
            
            if res.data and len(res.data) > 0:
                log(f"Job identified: {res.data[0].get('title')}")
                process_queued_song(res.data[0])
            else:
                time.sleep(15) # Idle poll frequency
                
        except Exception as e:
            log(f"Poller Loop Error: {e}")
            time.sleep(30)

# Initialize background thread
worker_thread = threading.Thread(target=job_poller, daemon=True)
worker_thread.start()

# --- Web Routes ---
@app.route('/')
def status():
    return jsonify({
        "status": "online",
        "worker_active": worker_thread.is_alive(),
        "deno_ready": True
    }), 200

if __name__ == "__main__":
    # Render provides PORT env var
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
