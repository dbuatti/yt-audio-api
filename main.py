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

app = Flask(__name__)
CORS(app)

# --- Configuration ---
# Ensure these are set in Render Dashboard -> Settings -> Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase Environment Variables!")
    sys.stdout.flush()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Safety Controls
download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    # Includes timestamp for easier debugging in Render
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [WORKER] {message}")
    sys.stdout.flush()

# --- Extraction Logic ---

def process_queued_song(song):
    song_id = song.get('id')
    video_url = song.get('youtube_url')
    user_id = song.get('user_id')
    title = song.get('title', 'Unknown Title')

    # Ensure we have the minimum requirements
    if not song_id or not video_url or not user_id:
        log(f"Skipping song {title}: Missing ID, URL, or UserID")
        supabase.table("repertoire").update({
            "extraction_status": "failed",
            "extraction_error": "Missing metadata (ID, URL, or UserID)"
        }).eq("id", song_id).execute()
        return

    with download_semaphore:
        try:
            log(f">>> STARTING: {title} ({song_id})")
            
            # 1. Update status to 'processing'
            supabase.table("repertoire").update({"extraction_status": "processing"}).eq("id", song_id).execute()

            file_id = str(uuid.uuid4())
            output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")
            
            ydl_opts = {
                'format': 'wa',
                'noplaylist': True,
                'outtmpl': output_template,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }],
                'nocheckcertificate': True,
                'quiet': True,
                'no_warnings': True
            }

            # 2. Extract from YouTube
            log(f"Downloading from YouTube...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            
            if os.path.exists(mp3_path):
                log(f"Upload starting for {title}...")
                storage_path = f"{user_id}/{song_id}/{int(time.time())}.mp3"
                
                # 3. Upload to Storage
                with open(mp3_path, 'rb') as f:
                    supabase.storage.from_("public_audio").upload(
                        path=storage_path, 
                        file=f,
                        file_options={"content-type": "audio/mpeg"}
                    )
                
                # 4. Get Public URL and Finalize
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
            supabase.table("repertoire").update({
                "extraction_status": "failed",
                "extraction_error": error_msg
            }).eq("id", song_id).execute()
        finally:
            gc.collect()

def job_poller():
    """Background loop that checks for 'queued' songs."""
    log("Job Poller initialized and hunting for work...")
    while True:
        try:
            # 1. Heartbeat log so you know the script hasn't frozen
            log("Heartbeat: Checking Supabase for 'queued' rows...")
            
            # 2. Look for work
            res = supabase.table("repertoire")\
                .select("id, youtube_url, user_id, title")\
                .eq("extraction_status", "queued")\
                .limit(1)\
                .execute()
            
            # 3. If work exists, process it
            if res.data and len(res.data) > 0:
                log(f"Found {len(res.data)} job(s). Picking up: {res.data[0].get('title')}")
                process_queued_song(res.data[0])
            else:
                # No work, sleep for 20s
                time.sleep(20)
                
        except Exception as e:
            log(f"Poller Loop Error: {e}")
            time.sleep(30)

# Start poller thread immediately
worker_thread = threading.Thread(target=job_poller, daemon=True)
worker_thread.start()

# --- Web Routes (For Health Checks) ---

@app.route('/')
def status():
    # Show if the thread is actually running
    is_alive = worker_thread.is_alive()
    return jsonify({
        "status": "online",
        "worker_thread_active": is_alive,
        "mode": "background_worker"
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
