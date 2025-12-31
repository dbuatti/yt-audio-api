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
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Only 1 extraction at a time to stay under 512MB RAM
download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    print(f"[WORKER] {message}")
    sys.stdout.flush()

# --- Core Logic ---

def process_queued_song(song):
    """Downloads, converts, uploads, and updates Supabase row."""
    song_id = song['id']
    video_url = song['youtube_url']
    user_id = song['user_id']

    with download_semaphore:
        try:
            log(f"Processing: {song.get('title', song_id)}")
            
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
            }

            # 2. Extract from YouTube
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            
            if os.path.exists(mp3_path):
                # 3. Upload to Storage
                # Path: userId/songId/timestamp.mp3
                storage_path = f"{user_id}/{song_id}/{int(time.time())}.mp3"
                
                with open(mp3_path, 'rb') as f:
                    supabase.storage.from_("public_audio").upload(
                        path=storage_path, 
                        file=f,
                        file_options={"content-type": "audio/mpeg"}
                    )
                
                # 4. Get Public URL and Update Record
                public_url = supabase.storage.from_("public_audio").get_public_url(storage_path)
                
                supabase.table("repertoire").update({
                    "audio_url": public_url,
                    "extraction_status": "completed",
                    "extraction_error": None
                }).eq("id", song_id).execute()
                
                log(f"DONE: {song_id}")
                os.remove(mp3_path) # Cleanup local disk
            
        except Exception as e:
            error_msg = str(e)
            log(f"FAILED: {error_msg}")
            supabase.table("repertoire").update({
                "extraction_status": "failed",
                "extraction_error": error_msg
            }).eq("id", song_id).execute()
        finally:
            gc.collect()

def job_poller():
    """Background loop that checks for 'queued' songs every 20 seconds."""
    log("Job Poller initialized.")
    while True:
        try:
            # Query for 1 song that needs processing
            res = supabase.table("repertoire")\
                .select("id, youtube_url, user_id, title")\
                .eq("extraction_status", "queued")\
                .limit(1)\
                .execute()
            
            if res.data and len(res.data) > 0:
                process_queued_song(res.data[0])
            else:
                # No work found, sleep before checking again
                time.sleep(20)
        except Exception as e:
            log(f"Poller Error: {e}")
            time.sleep(30)

# Start poller thread
threading.Thread(target=job_poller, daemon=True).start()

# --- Web Routes ---

@app.route('/')
def status():
    return jsonify({
        "status": "online",
        "mode": "background_worker",
        "concurrency_limit": 1
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
