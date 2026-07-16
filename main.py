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

# Log a message when the script starts
print("[WORKER STARTUP] Python worker script is initializing with R2 support...")
sys.stdout.flush()

app = Flask(__name__)
CORS(app)

# Supabase Config
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("[CRITICAL] Missing Supabase environment variables!")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# R2 Config
R2_ENDPOINT = os.environ.get("S3_ENDPOINT")
R2_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY_ID")
R2_SECRET_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("S3_BUCKET_NAME")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL")

# Initialize S3 client
s3 = None
if all([R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY]):
    s3 = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY
    )
else:
    print("[WARNING] R2 Storage environment variables are incomplete.")

# Concurrency Control
download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_PATH = "/tmp/cookies.txt"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    print(f"[AUDIO-WORKER] {message}", flush=True)

def sanitize_filename(name):
    if not name:
        return "track"
    return "".join([c if c.isalnum() else "_" for c in name]).lower().strip("_")

def download_cookies_from_supabase():
    """Fetches the latest cookies.txt from Supabase Storage"""
    try:
        log("Checking Supabase 'cookies' bucket for fresh auth file...")
        
        # Use service role for reliable download
        res = supabase.storage.from_("cookies").download("cookies.txt")
        
        if res:
            with open(COOKIE_PATH, "wb") as f:
                f.write(res)
            log("SUCCESS: cookies.txt synchronized from Cloud Vault.")
            return True
    except Exception as e:
        log(f"Vault Sync Note: No cookies.txt found or accessible ({e}). Proceeding with PO_TOKEN only.")
    return False

# ... (rest of your code remains the same)

def process_queued_song(song):
    # ... (your existing function - no change needed here)
    # It already calls download_cookies_from_supabase()
    pass

def job_poller():
    log("Job Poller initialized for R2. Starting initial cookie sync.")
    download_cookies_from_supabase()
   
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
                log(f"Found queued job: {song_data.get('title')}. Starting processing.")
                process_queued_song(song_data)
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
