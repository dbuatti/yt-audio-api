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
PO_TOKEN = os.environ.get("YOUTUBE_PO_TOKEN")  # from Render env var

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase Environment Variables!", flush=True)
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

    cookie_path = os.path.join(os.getcwd(), 'cookies.txt')
    has_cookies = os.path.exists(cookie_path)

    with download_semaphore:
        try:
            log(f">>> STARTING EXTRACTION: {title}")
            supabase.table("repertoire").update({"extraction_status": "processing"}).eq("id", song_id).execute()

            file_id = str(uuid.uuid4())
            output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

            # ── Modern 2026 yt-dlp options ────────────────────────────────────────
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

                # Explicitly prefer Deno (recommended runtime)
                'js_runtimes': 'deno',

                # Use bundled EJS if available, fallback to github
                'remote_components': ['ejs:npm', 'ejs:github'],

                # PO Token – only add if you actually have a valid one
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'web_safari', 'android', 'ios'],
                    }
                },

                # Add PO token only if it exists (prevents invalid token errors)
                # Format: web+TOKEN  or  web.gvs+TOKEN  etc.
                'po_token': f'web+{PO_TOKEN}' if PO_TOKEN and PO_TOKEN.strip() else None,

                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'nocheckcertificate': True,
                'quiet': False,
                'no_warnings': False,
                'retries': 10,
                'continuedl': True,
                'fragment_retries': 10,
            }

            # If you have a valid PO token, you can also add it more precisely:
            if PO_TOKEN and PO_TOKEN.strip():
                ydl_opts['extractor_args']['youtube']['po_token'] = [f'web+{PO_TOKEN}']

            log(f"Downloading from YouTube: {video_url}")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")

            if os.path.exists(mp3_path):
                file_size = os.path.getsize(mp3_path)
                log(f"Upload starting for {title}... ({file_size:,} bytes)")

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

                log(f"SUCCESS: Finished {title}")
            else:
                raise Exception("Conversion failed - MP3 file not found after download.")

        except Exception as e:
            error_msg = str(e)
            log(f"FAILED: {title} | Error: {error_msg}")
            supabase.table("repertoire").update({
                "extraction_status": "failed",
                "extraction_error": error_msg[:250]
            }).eq("id", song_id).execute()

        finally:
            # Cleanup
            if 'file_id' in locals():
                for f in os.listdir(DOWNLOAD_DIR):
                    if file_id in f:
                        try:
                            os.remove(os.path.join(DOWNLOAD_DIR, f))
                        except:
                            pass
            gc.collect()


def job_poller():
    log("Job Poller initialization complete. Scanning for work...")
    while True:
        try:
            res = supabase.table("repertoire") \
                .select("id, youtube_url, user_id, title") \
                .eq("extraction_status", "queued") \
                .limit(1) \
                .execute()

            if res.data and len(res.data) > 0:
                process_queued_song(res.data[0])
            else:
                time.sleep(15)   # slightly faster polling

        except Exception as e:
            log(f"Poller Loop Exception: {e}")
            time.sleep(30)


# Start background worker
worker_thread = threading.Thread(target=job_poller, daemon=True)
worker_thread.start()


@app.route('/')
def status():
    return jsonify({
        "status": "online",
        "worker_active": worker_thread.is_alive(),
        "deno_available": "deno" in yt_dlp.utils.which("deno")
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
