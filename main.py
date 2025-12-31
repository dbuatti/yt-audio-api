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

# --- IMMEDIATE STARTUP LOG ---
# This forces a log to appear the moment Render starts the process
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
    """Custom log function with immediate flushing for Render logs."""
    timestamp = time.strftime("%H:%M:%S")
    # flush=True is the key to seeing logs in real-time on Render
    print(f"[{timestamp}] [WORKER] {message}", flush=True)

# --- Extraction Logic ---

def process_queued_song(song):
    song_id = song.get('id')
    video_url = song.get('youtube_url')
    user_id = song.get('user_id')
    title = song.get('title', 'Unknown Title')

    # Auth Credentials for YouTube Bot Protection
    po_token = os.environ.get("YOUTUBE_PO_TOKEN")
    visitor_data = os.environ.get("YOUTUBE_VISITOR_DATA")
    cookie_path = './cookies.txt' if os.path.exists('./cookies.txt') else None

    with download_semaphore:
        try:
            log(f">>> STARTING EXTRACTION: {title}")
            
            # 1. Update status to 'processing' in Supabase
            supabase.table("repertoire").update({"extraction_status": "processing"}).eq("id", song_id).execute()

            file_id = str(uuid.uuid4())
            output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")
            
            ydl_opts = {
                'format': 'bestaudio/best', 
                'noplaylist': True,
                'outtmpl': output_template,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }],
                'cookiefile': cookie_path,
                'po_token': f"web+none:{po_token}" if po_token else None,
                'headers': {'X-Goog-Visitor-Id': visitor_data} if visitor_data else {},
                'nocheckcertificate': True,
                'quiet': True,
                'no_warnings': True
            }

            log(f"Downloading from YouTube: {title}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            
            if os.path.exists(mp3_path):
                log(f"Upload starting: {title}")
                storage_path = f"{user_id}/{song_id}/{int(time.time())}.mp3"
                
                # 3. Upload to Supabase Storage
                with open(mp3_path, 'rb') as f:
                    supabase.storage.from_("public_audio").upload(
                        path=storage_path, 
                        file=f,
                        file_options={"content-type": "audio/mpeg"}
                    )
                
                # 4. Finalize Database Record
                public_url = supabase.storage.from_("public_audio").get_public_url(storage_path)
                
                supabase.table("repertoire").update({
                    "audio_url": public_url,
                    "extraction_status": "completed",
                    "extraction_error": None
                }).eq("id", song_id).execute()
                
                log(f"SUCCESS: Finished {title}")
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            else:
                raise Exception("FFmpeg failed to produce MP3 file")
            
        except Exception as e:
            error_msg = str(e)
            log(f"FAILED: {title} | Error: {error_msg}")
            
            try:
                supabase.table("repertoire").update({
                    "extraction_status": "failed",
                    "extraction_error": error_msg[:255]
                }).eq("id", song_id).execute()
            except Exception as db_err:
                log(f"Supabase update failed: {db_err}")
        finally:
            gc.collect()

def job_poller():
    """Background loop that checks Supabase every 20 seconds."""
    log("Job Poller initialization complete. Scanning for work...")
    while True:
        try:
            # Heartbeat log to prove the worker is still alive
            log("Heartbeat: Checking Supabase for 'queued' jobs...")
            
            res = supabase.table("repertoire")\
                .select("id, youtube_url, user_id, title")\
                .eq("extraction_status", "queued")\
                .limit(1)\
                .execute()
            
            if res.data and len(res.data) > 0:
                log(f"Job found! Processing: {res.data[0].get('title')}")
                process_queued_song(res.data[0])
            else:
                time.sleep(20)
                
        except Exception as e:
            log(f"Poller Loop Exception: {e}")
            time.sleep(30)

# Start poller thread
worker_thread = threading.Thread(target=job_poller, daemon=True)
worker_thread.start()

# --- Routes for Health Monitoring ---

@app.route('/')
def status():
    return jsonify({
        "status": "online",
        "worker_active": worker_thread.is_alive(),
        "mode": "background_worker",
        "heartbeat": time.strftime("%H:%M:%S")
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
