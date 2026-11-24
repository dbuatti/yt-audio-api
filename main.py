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

print("[WORKER STARTUP] Python worker script is initializing with R2 support...")
sys.stdout.flush()

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase environment variables!")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

R2_ENDPOINT = os.environ.get("S3_ENDPOINT")
R2_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY_ID")
R2_SECRET_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("S3_BUCKET_NAME")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL")

if not all([R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET, R2_PUBLIC_URL]):
    print("[CRITICAL] Missing R2/S3 environment variables!")
    sys.exit(1)

s3 = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)

download_semaphore = threading.BoundedSemaphore(value=1)
DOWNLOAD_DIR = "/tmp/downloads"
COOKIE_PATH = "/tmp/cookies.txt"
BGUTIL_SERVER_HOME = os.environ.get("BGUTIL_SERVER_HOME", "/root/bgutil-ytdlp-pot-provider/server")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def log(message):
    print(f"[AUDIO-WORKER] {message}", flush=True)

def sanitize_filename(name):
    if not name:
        return "track"
    return "".join([c if c.isalnum() else "_" for c in name]).lower().strip("_")

def download_cookies_from_supabase():
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
    # Remove stale file so yt-dlp doesn't use rotten cookies
    try:
        if os.path.exists(COOKIE_PATH):
            os.remove(COOKIE_PATH)
    except Exception:
        pass
    return False

def _cookie_file_if_valid():
    return COOKIE_PATH if os.path.exists(COOKIE_PATH) else None

def process_queued_song(song):
    song_id = song.get('id')
    video_url = song.get('youtube_url')
    user_id = song.get('user_id')
    title = song.get('title', 'Unknown Title')
    artist = song.get('artist', 'Unknown Artist')

    if not user_id:
        log(f"ABORT: missing user_id for '{title}'")
        _mark_failed(song_id, "Missing user_id")
        return

    data_sync_id = os.environ.get("YOUTUBE_DATA_SYNC_ID")

    # PROXY_URL is currently broken (407 auth); disable until fixed
    proxy_url = None

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

            def base_opts(cookiefile, use_proxy=True):
                yt = {
                    'player_client': ['web'],
                }
                if data_sync_id:
                    yt['data_sync_id'] = [data_sync_id]
                opts = {
                    'format': 'bestaudio/best',
                    'noplaylist': True,
                    'outtmpl': output_template,
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '128',
                    }],
                    'cookiefile': cookiefile,
                    'proxy': (proxy_url if (proxy_url and use_proxy) else None),
                    'extractor_args': {
                        'youtube': yt,
                        'youtubepot-bgutilscript': {
                            'server_home': BGUTIL_SERVER_HOME,
                        },
                    },
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Referer': 'https://www.google.com/',
                    },
                    'nocheckcertificate': True,
                    'geo_bypass': True,
                    'quiet': True,
                    'no_warnings': False,
                    'retries': 5,
                    'fragment_retries': 10,
                    'socket_timeout': 30,
                }
                return opts

            def is_proxy_error(err):
                e = str(err).lower()
                return ("proxyerror" in e or "407" in e or "tunnel connection failed" in e
                        or "unable to connect to proxy" in e or "proxy" in e and "auth" in e)

            strategies = []
            cf = _cookie_file_if_valid()
            if cf:
                strategies.append(("web with cookies", base_opts(cf)))
            strategies.append(("web no cookies", base_opts(None)))
            strategies.append(("mweb no auth", lambda o=base_opts(None): {**o, 'extractor_args': {**o['extractor_args'], 'youtube': {**o['extractor_args']['youtube'], 'player_client': ['mweb']}}}))
            strategies.append(("tv no cookies", lambda o=base_opts(None): {**o, 'extractor_args': {**o['extractor_args'], 'youtube': {**o['extractor_args']['youtube'], 'player_client': ['tv'], 'player_skip': ['webpage']}}}))
            strategies.append(("web_embedded no auth", lambda o=base_opts(None): {**o, 'extractor_args': {**o['extractor_args'], 'youtube': {**o['extractor_args']['youtube'], 'player_client': ['web_embedded'], 'player_skip': ['webpage']}}}))

            last_error = None
            proxy_disabled = False
            for attempt_name, attempt_opts in strategies:
                try:
                    if callable(attempt_opts):
                        opts = attempt_opts()
                    else:
                        opts = dict(attempt_opts)
                    if proxy_disabled:
                        opts["proxy"] = None
                    log(f"Trying strategy '{attempt_name}' for '{title}'...")
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([video_url])
                    last_error = None
                    if attempt_name not in ("web with cookies", "web no cookies"):
                        log(f"SUCCEEDED with '{attempt_name}' for '{title}'!")
                    break
                except Exception as e:
                    last_error = e
                    err = str(e)
                    log(f"'{attempt_name}' failed for '{title}': {err[:200]}")
                    if "cookies are no longer valid" in err or "cookies are no longer" in err:
                        try:
                            if os.path.exists(COOKIE_PATH):
                                os.remove(COOKIE_PATH)
                        except Exception:
                            pass
                    # If the proxy itself is broken, disable it for remaining attempts
                    if proxy_url and ("proxy" in err.lower() or "407" in err or "tunnel connection failed" in err.lower() or "unable to connect to proxy" in err.lower()):
                        if not proxy_disabled:
                            log("Proxy is broken; disabling proxy for remaining attempts.")
                        proxy_disabled = True
                        # Retry this same attempt immediately without proxy
                        try:
                            opts2 = dict(opts)
                            opts2["proxy"] = None
                            log(f"Retrying '{attempt_name}' without proxy...")
                            with yt_dlp.YoutubeDL(opts2) as ydl:
                                ydl.download([video_url])
                            last_error = None
                            break
                        except Exception as e2:
                            last_error = e2
                            err = str(e2)
                            log(f"'{attempt_name}' (no proxy) failed: {err[:200]}")
                    if "429" in err or "Too Many Requests" in err or "403" in err:
                        log("Rate limit / IP block detected; backing off 10s...")
                        time.sleep(10)
                    else:
                        time.sleep(3)

            if last_error:
                raise last_error

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
            elif "Sign in to confirm you" in error_msg:
                friendly_error = "YouTube bot detection. Refresh cookies.txt and ensure bgutil PO provider is working."

            log(f"FAILED: '{title}' | Error: {error_msg}")
            _mark_failed(song_id, friendly_error)
        finally:
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(file_id):
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except Exception:
                        pass
            gc.collect()

def _mark_failed(song_id, friendly_error):
    try:
        supabase.table("repertoire").update({
            "extraction_status": "failed",
            "extraction_error": (friendly_error or "")[:250],
            "last_sync_log": f"R2 Worker Error: {(friendly_error or '')[:100]}"
        }).eq("id", song_id).execute()
    except Exception as db_e:
        log(f"Status update failed: {db_e}")

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

                after_status = supabase.table("repertoire")\
                    .select("extraction_status")\
                    .eq("id", song_id)\
                    .execute()

                after_val = after_status.data[0].get('extraction_status') if after_status.data else None

                if after_val == 'failed':
                    log("Job failed; cooling down 15s before next attempt.")
                    time.sleep(15)
                elif after_val == 'queued':
                    consecutive_failures[song_id] = consecutive_failures.get(song_id, 0) + 1
                    log(f"Song '{title}' still queued after attempt {consecutive_failures[song_id]}.")

                    if consecutive_failures[song_id] >= 3:
                        log(f"Auto-failing '{title}' after {consecutive_failures[song_id]} failed attempts.")
                        _mark_failed(song_id, "Auto-failed: exceeded retry limit")
                        consecutive_failures[song_id] = 0
                else:
                    consecutive_failures.pop(song_id, None)
            else:
                time.sleep(20)
        except Exception as e:
            log(f"Poller Error: {e}")
            time.sleep(30)

threading.Thread(target=job_poller, daemon=True).start()

# Optional keep-alive self-ping to mitigate Render free-tier spin-down.
def _keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        try:
            requests.get(url.rstrip("/") + "/", timeout=10)
        except Exception:
            pass
        time.sleep(600)

threading.Thread(target=_keep_alive, daemon=True).start()

@app.route('/')
def health():
    return "R2 Worker is alive and polling Supabase...", 200

@app.route('/health')
def health2():
    return jsonify(status="ok", time=time.time()), 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))