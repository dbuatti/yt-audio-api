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
DATA_SYNC_ID = os.environ.get("YOUTUBE_DATA_SYNC_ID")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase Environment Variables!", flush=True)
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Safety Controls: Strict 1 task at a time for 512MB RAM
download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [WORKER] {message}", flush=True)

def process_queued_song(song):
    song_id = song.get('id')
    video_url = song.get('youtube_url')
    user_id = song.get('user_id')
    title = song.get('title', 'Unknown Title')

    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    has_cookies = os.path.exists(cookie_path)
    
    with download_semaphore:
        try:
            # 2026 ANTI-BOT DELAY
            wait_time = random.uniform(4, 8)
            log(f"Cooldown: {wait_time:.2f}s | Title: {title}")
            time.sleep(wait_time)

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
                'cookiefile': cookie_path if has_cookies else None,
                
                # JS Solver via Deno (Configured in Dockerfile)
                'js_runtimes': {'deno': {}},
                
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web_safari', 'ios', 'android'],
                        'skip': ['hls', 'dash'],
                        # Crucial for 2026: Validates your cookie session
                        'data_sync_id': DATA_SYNC_ID if DATA_SYNC_ID else None
                    }
                },
                
                # PO Token is now video-bound; this acts as a backup
                'po_token': f'web+{PO_TOKEN}' if PO_TOKEN else None,
                
                'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
                'nocheckcertificate': True,
                'retries': 5,
            }

            log(f"Starting download for {video_url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
                
            mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            
            if os.path.exists(mp3_path):
                storage_path = f"{user_id}/{song_id}/{int(time.time())}.mp3"
                with open(mp3_path, 'rb') as f:
                    supabase.storage.from_("public_audio").upload(
                        path=storage_path, 
                        file=f,
                        file_options={"content-type": "audio/mpeg", "x-upsert": "true"}
                    )
                
                public_url = supabase.storage.from_("public_audio").get_public_url(storage_path)
                supabase.table("repertoire").update({
                    "audio_url": public_url,
                    "preview_url": public_url,
                    "extraction_status": "completed",
                    "extraction_error": None
                }).eq("id", song_id).execute()
                
                log(f"SUCCESS: {title}")
            else:
                raise Exception("Conversion to MP3 failed.")
            
        except Exception as e:
            error_msg = str(e)
            log(f"FAILED: {error_msg}")
            supabase.table("repertoire").update({
                "extraction_status": "failed",
                "extraction_error": error_msg[:250]
            }).eq("id", song_id).execute()

        finally:
            if 'file_id' in locals():
                for f in os.listdir(DOWNLOAD_DIR):
                    if file_id in f:
                        try: os.remove(os.path.join(DOWNLOAD_DIR, f))
                        except: pass
            gc.collect()

def job_poller():
    while True:
        try:
            res = supabase.table("repertoire")\
                .select("id, youtube_url, user_id, title")\
                .eq("extraction_status", "queued")\
                .limit(1)\
                .execute()
            
            if res.data and len(res.data) > 0:
                process_queued_song(res.data[0])
            else:
                time.sleep(15)
        except Exception as e:
            log(f"Poller Error: {e}")
            time.sleep(30)

worker_thread = threading.Thread(target=job_poller, daemon=True)
worker_thread.start()

@app.route('/')
def status():
    return jsonify({"status": "active", "deno": "ready", "sync_id": bool(DATA_SYNC_ID)}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
