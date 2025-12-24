"""
main.py
Final Production Version: Fixes CORS + YouTube Block + Proxies
"""
import os, secrets, threading, time, uuid, requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pathlib import Path
import yt_dlp

app = Flask(__name__)

# --- FIX: BROAD CORS FOR DEVELOPMENT ---
CORS(app, resources={
    r"/*": {
        "origins": "*",  # Allows local dev and production
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
REPO_COOKIES_PATH = Path(__file__).resolve().parent / "cookies.txt"

active_tokens = {}
TOKEN_EXPIRY = 600

# --- BACKGROUND CLEANUP ---
def cleanup_expired_files():
    while True:
        now = time.time()
        expired = [t for t, d in active_tokens.items() if now > d["expiry"]]
        for t in expired:
            data = active_tokens.pop(t, None)
            if data and data["file"] and (DOWNLOAD_DIR / data["file"]).exists():
                (DOWNLOAD_DIR / data["file"]).unlink()
        time.sleep(60)

threading.Thread(target=cleanup_expired_files, daemon=True).start()

# --- CORE DOWNLOAD LOGIC ---
def run_yt_dlp(video_url, token):
    po_token = os.getenv("YOUTUBE_PO_TOKEN")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA")
    proxy_url = os.getenv("PROXY_URL") 
    
    filename = f"{token}.mp3"
    output_path = DOWNLOAD_DIR / filename

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path.with_suffix('')),
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'impersonate': 'chrome', # CRITICAL: Mimics browser TLS
        'proxy': proxy_url if proxy_url else None, # CRITICAL: Bypasses Data Center Block
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'ios', 'android'],
                'po_token': [f'web+{po_token}'] if po_token else [],
                'visitor_data': visitor_data if visitor_data else ""
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36',
            'Accept-Language': 'en-GB,en;q=0.9',
            'Referer': 'https://m.youtube.com/'
        }
    }

    if REPO_COOKIES_PATH.exists():
        ydl_opts['cookiefile'] = str(REPO_COOKIES_PATH)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        if token in active_tokens:
            active_tokens[token].update({"status": "ready", "file": filename})
    except Exception as e:
        print(f"yt-dlp Error: {e}", flush=True)
        if token in active_tokens:
            active_tokens[token].update({"status": "error", "error_msg": str(e)})

# --- ROUTES ---
@app.route("/", methods=["GET"])
def start_request():
    url = request.args.get("url")
    if not url: return jsonify(error="Missing URL"), 400

    job_token = str(uuid.uuid4())
    active_tokens[job_token] = {
        "file": None,
        "status": "processing",
        "expiry": time.time() + TOKEN_EXPIRY,
        "error_msg": None
    }

    threading.Thread(target=run_yt_dlp, args=(url, job_token), daemon=True).start()
    return jsonify({"token": job_token})

@app.route("/download", methods=["GET"])
def check_or_download():
    token = request.args.get("token")
    data = active_tokens.get(token)

    if not data: return jsonify(error="Invalid Token"), 404
    
    if data["status"] == "processing":
        return jsonify(status="processing"), 202
    
    if data["status"] == "error":
        return jsonify(error="YouTube Block", detail=data["error_msg"]), 500

    return send_from_directory(DOWNLOAD_DIR, path=data["file"], as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
