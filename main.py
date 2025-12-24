"""
main.py
YouTube Audio Converter API - 2025 Production Edition
Includes: Proxies, Mobile Identity, Background Processing, and JS Runtime Support
"""

import os
import secrets
import threading
import time
import uuid
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pathlib import Path
import yt_dlp

app = Flask(__name__)

# --- CONFIGURATION ---
CORS(app, resources={
    r"/*": {
        "origins": ["http://localhost:5173", "https://gig-studio-pro.vercel.app"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Path to the cookies file committed to your GitHub repository root
REPO_COOKIES_PATH = Path(__file__).resolve().parent / "cookies.txt"

# Stores status of current jobs: {"token": {"file": str, "status": str, "expiry": float, "error": str}}
active_tokens = {}
TOKEN_EXPIRY = 600  # 10 minutes

# --- BACKGROUND CLEANUP ---
def cleanup_expired_files():
    """Background thread to delete old MP3s and clear memory."""
    while True:
        now = time.time()
        expired = [t for t, d in active_tokens.items() if now > d["expiry"]]
        for t in expired:
            data = active_tokens.pop(t)
            if data["file"] and (DOWNLOAD_DIR / data["file"]).exists():
                (DOWNLOAD_DIR / data["file"]).unlink()
                print(f"--- Cleanup: Deleted {data['file']} ---", flush=True)
        time.sleep(60)

threading.Thread(target=cleanup_expired_files, daemon=True).start()

# --- CORE DOWNLOAD LOGIC ---
def run_yt_dlp(video_url, token):
    """Executes the hardened yt-dlp download using proxies and PO tokens."""
    po_token = os.getenv("YOUTUBE_PO_TOKEN")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA")
    proxy_url = os.getenv("PROXY_URL") # Format: http://user:pass@host:port
    
    filename = f"{token}.mp3"
    output_path = DOWNLOAD_DIR / filename

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path.with_suffix('')),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }],
        # Mimics a real browser's TLS signature
        'impersonate': 'chrome',
        'proxy': proxy_url if proxy_url else None, # Use residential proxy
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'ios', 'android'], # Mobile identity
                'po_token': [f'web+{po_token}'] if po_token else [],
                'visitor_data': visitor_data if visitor_data else ""
            }
        },
        'http_headers': {
            # Matches the session used to generate the PO Token
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
        
        # Update job status as ready
        if token in active_tokens:
            active_tokens[token].update({"status": "ready", "file": filename})
            print(f"--- SUCCESS: {filename} is ready ---", flush=True)

    except Exception as e:
        print(f"--- yt-dlp Error: {str(e)} ---", flush=True)
        if token in active_tokens:
            active_tokens[token].update({"status": "error", "error_msg": str(e)})

# --- ROUTES ---
@app.route("/", methods=["GET"])
def start_request():
    """Initial request returns a token and starts background process."""
    url = request.args.get("url")
    if not url:
        return jsonify(error="Missing URL"), 400

    job_token = str(uuid.uuid4())
    active_tokens[job_token] = {
        "file": None,
        "status": "processing",
        "expiry": time.time() + TOKEN_EXPIRY,
        "error_msg": None
    }

    # Start download in a separate thread to prevent Render timeout
    threading.Thread(target=run_yt_dlp, args=(url, job_token), daemon=True).start()
    
    return jsonify({"token": job_token})

@app.route("/download", methods=["GET"])
def check_or_download():
    """Checks processing status or serves the file if ready."""
    token = request.args.get("token")
    data = active_tokens.get(token)

    if not data:
        return jsonify(error="Invalid or Expired Token"), 404
    
    if data["status"] == "processing":
        return jsonify(status="processing"), 202
    
    if data["status"] == "error":
        return jsonify(error="YouTube Blocked this request", detail=data["error_msg"]), 500

    # Serve the file
    try:
        response = send_from_directory(
            DOWNLOAD_DIR, 
            path=data["file"], 
            as_attachment=True, 
            mimetype='audio/mpeg'
        )
        # Optional: Delete from memory/disk immediately after serving
        # del active_tokens[token] 
        return response
    except Exception as e:
        return jsonify(error="File not found on server", detail=str(e)), 404

if __name__ == "__main__":
    # Log cookie status on startup
    if REPO_COOKIES_PATH.exists():
        print(f"--- SUCCESS: Found cookies.txt at {REPO_COOKIES_PATH} ---", flush=True)
    else:
        print("--- WARNING: No cookies.txt found in repository ---", flush=True)
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
