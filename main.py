import os
import threading
import time
import uuid
import sys
import gc
import requests
from flask import Flask, request, send_file, jsonify, make_response
from flask_cors import CORS
import yt_dlp
from supabase import create_client, Client
import boto3

import random

# Log a message when the script starts
print("[WORKER STARTUP] Python worker script is initializing with R2 support...")
sys.stdout.flush()

app = Flask(__name__)
CORS(app)

# Supabase Config
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase environment variables!")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# R2 Config
R2_ENDPOINT = os.environ.get("S3_ENDPOINT")
R2_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY_ID")
R2_SECRET_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("S3_BUCKET_NAME")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL")

# Initialize S3 client only if variables are present
s3 = None
if all([R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY]):
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY
    )
else:
    print("[WARNING] R2 Storage environment variables are incomplete. S3 client not initialized.")

# Concurrency Control
download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_PATH = "/tmp/cookies.txt"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    print(f"[AUDIO-WORKER] {message}", flush=True)

def sanitize_filename(name):
    """Creates a URL-safe, human-readable filename."""
    if not name:
        return "track"
    return "".join([c if c.isalnum() else "_" for c in name]).lower().strip("_")

def download_cookies_from_supabase():
    """Fetches the latest cookies.txt from Supabase Storage to handle auth blocks."""
    try:
        log("Checking Supabase 'cookies' bucket for fresh auth file...")
        res = supabase.storage.from_("cookies").download("cookies.txt")
        if res:
            with open(COOKIE_PATH, "wb") as f:
                f.write(res)
            file_size = os.path.getsize(COOKIE_PATH)
            log(f"SUCCESS: cookies.txt synchronized ({file_size} bytes).")
            return True
    except Exception as e:
        log(f"Vault Sync Note: No cookies.txt found or accessible ({e}). Proceeding with PO_TOKEN only.")
    return False

def process_queued_song(song):
    song_id = song.get('id')
    video_url = song.get('youtube_url')
    user_id = song.get('user_id')
    title = song.get('title', 'Unknown Title')
    artist = song.get('artist', 'Unknown Artist')

    if not s3:
        log(f"ABORT: S3 client not initialized. Cannot process {title}")
        return

    po_token = os.environ.get("YOUTUBE_PO_TOKEN")
    visitor_data = os.environ.get("YOUTUBE_VISITOR_DATA")
    data_sync_id = os.environ.get("YOUTUBE_DATA_SYNC_ID")

    with download_semaphore:
        file_id = str(uuid.uuid4())
        try:
            log(f">>> STARTING R2 PROCESSING: {title} (ID: {song_id})")
            download_cookies_from_supabase()

            supabase.table("repertoire").update({
                "extraction_status": "processing", 
                "last_sync_log": "Starting high-fidelity audio extraction for R2..."
            }).eq("id", song_id).execute()

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
                'cookiefile': COOKIE_PATH if os.path.exists(COOKIE_PATH) else None,
                'po_token': f"web+none:{po_token}" if po_token else None,
                'extractor_args': {'youtube': {
                    'data_sync_id': [data_sync_id],
                    'player_client': ['tv', 'web'],
                    'player_skip': ['webpage']
                }} if data_sync_id else {'youtube': {
                    'player_client': ['tv', 'web'],
                    'player_skip': ['webpage']
                }},
                'headers': {
                    'X-Goog-Visitor-Id': visitor_data,
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Referer': 'https://www.google.com/',
                } if visitor_data else {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                    'Referer': 'https://www.google.com/',
                },
                'nocheckcertificate': True,
                'geo_bypass': True,
                'quiet': True,
                'no_warnings': False,
                'source_address': '0.0.0.0',
                'retries': 5,
                'fragment_retries': 10,
                'socket_timeout': 30,
            }

            log(f"Downloading audio for '{title}' from {video_url}...")
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
            except Exception as download_error:
                error_str = str(download_error).lower()
                # If it likely failed due to bad cookies or auth issues, try one last time WITHOUT cookies
                if any(phrase in error_str for phrase in ["cookies", "unavailable", "403", "sign in"]):
                    log(f"Primary attempt failed, retrying WITHOUT cookies for '{title}'...")
                    ydl_opts_no_cookies = ydl_opts.copy()
                    ydl_opts_no_cookies['cookiefile'] = None
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts_no_cookies) as ydl_retry:
                            ydl_retry.download([video_url])
                        log(f"Retry WITHOUT cookies SUCCEEDED for '{title}'!")
                    except Exception as retry_error:
                        log(f"Retry without cookies failed for '{title}': {retry_error}")
                        raise download_error 
                else:
                    raise download_error
            
            mp3_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            
            if os.path.exists(mp3_path):
                log(f"Download successful. Starting upload to Cloudflare R2 for {title}.")
                
                clean_artist = sanitize_filename(artist)
                clean_title = sanitize_filename(title)
                file_name = f"{clean_artist}_{clean_title}_audio.mp3"
                descriptive_folder = f"{song_id}_{clean_artist}_{clean_title}"
                storage_path = f"{user_id}/{descriptive_folder}/{file_name}"
                
                with open(mp3_path, 'rb') as f:
                    s3.put_object(
                        Bucket=R2_BUCKET,
                        Key=storage_path,
                        Body=f.read(),
                        ContentType='audio/mpeg'
                    )
                
                public_url = f"{R2_PUBLIC_URL.rstrip('/')}/{storage_path}"
                
                log(f"Upload complete. Updating Supabase record for '{title}'")
                supabase.table("repertoire").update({
                    "audio_url": public_url,
                    "preview_url": public_url,
                    "extraction_status": "completed",
                    "extraction_error": None,
                    "last_extracted_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    "last_sync_log": "Master audio linked successfully to R2."
                }).eq("id", song_id).execute()
                
                log(f"SUCCESS: Finished processing '{title}'")
                os.remove(mp3_path)
            else:
                raise Exception("Post-processing failed: MP3 conversion yielded no output.")
            
        except Exception as e:
            error_msg = str(e)
            
            # Map technical errors to user-friendly messages
            friendly_error = error_msg
            if "cookies are no longer valid" in error_msg:
                friendly_error = "YouTube cookies expired. Please upload a new cookies.txt to the 'cookies' bucket in Supabase."
            elif "Video unavailable" in error_msg:
                friendly_error = "Video unavailable. This may be due to region restrictions, age-rating, or YouTube blocking the worker IP. Try updating cookies.txt."
            elif "Sign in to confirm your age" in error_msg:
                friendly_error = "Age-restricted video. Valid cookies.txt required to download."
            elif "Incomplete YouTube PO Token" in error_msg or "po_token" in error_msg.lower():
                friendly_error = "YouTube PO Token issue. The worker's environment variables may need updating."
            elif "403: Forbidden" in error_msg:
                friendly_error = "YouTube Access Denied (403). The server IP might be temporarily throttled."

            log(f"FAILED: '{title}' | Error: {error_msg}")
            try:
                supabase.table("repertoire").update({
                    "extraction_status": "failed",
                    "extraction_error": friendly_error[:250],
                    "last_sync_log": f"R2 Worker Error: {friendly_error[:100]}"
                }).eq("id", song_id).execute()
            except Exception as db_e:
                log(f"Status update failed: {db_e}")
        finally:
            # Cleanup leftover files in case of crash/partial download
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(file_id):
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except:
                        pass
            gc.collect()

def job_poller():
    log("Job Poller initialized for R2. Starting initial cookie sync.")
    download_cookies_from_supabase()
    
    consecutive_failures = {}
    
    while True:
        try:
            res = supabase.table("repertoire")\
                .select("id, youtube_url, user_id, title, artist")\
                .eq("extraction_status", "queued")\
                .order('created_at', desc=False)\
                .limit(1)\
                .execute()
            
            if res.data and len(res.data) > 0:
                song_data = res.data[0]
                song_id = song_data.get('id')
                title = song_data.get('title', 'Unknown')
                
                process_queued_song(song_data)
                
                time.sleep(5)
                
                after_status = supabase.table("repertoire")\
                    .select("extraction_status")\
                    .eq("id", song_id)\
                    .execute()
                
                after_val = after_status.data[0].get('extraction_status') if after_status.data else None
                
                if after_val == 'queued':
                    consecutive_failures[song_id] = consecutive_failures.get(song_id, 0) + 1
                    log(f"Song '{title}' still queued after attempt {consecutive_failures[song_id]}.")
                    
                    if consecutive_failures[song_id] >= 3:
                        log(f"Auto-failing '{title}' after {consecutive_failures[song_id]} failed attempts.")
                        try:
                            supabase.table("repertoire").update({
                                "extraction_status": "failed",
                                "extraction_error": "Auto-failed: exceeded retry limit",
                                "last_sync_log": "Worker auto-failed after 3 consecutive failed attempts."
                            }).eq("id", song_id).execute()
                        except Exception as e:
                            log(f"Failed to update status to failed: {e}")
                        consecutive_failures[song_id] = 0
                else:
                    consecutive_failures.pop(song_id, None)
            else:
                time.sleep(20)
        except Exception as e:
            log(f"Poller Error: {e}")
            time.sleep(30)

threading.Thread(target=job_poller, daemon=True).start()

@app.route('/')
def health():
    return "R2 Worker is alive and polling Supabase...", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
