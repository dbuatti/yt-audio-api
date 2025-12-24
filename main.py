"""
main.py
YouTube Audio Converter API - 2025 Final Hardened Edition
Fixes: Path debugging, CORS, Flask 500 errors, and YouTube Bot Detection
"""

import os
import secrets
import threading
import requests
import sys
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from uuid import uuid4
from pathlib import Path
import yt_dlp
import access_manager
from constants import *

app = Flask(__name__)

# --- CORS CONFIGURATION ---
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:32141",          
            "http://localhost:5173",           
            "https://gig-studio-pro.vercel.app" 
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Disposition"]
    }
})

# --- PATH DEBUGGING & COOKIES ---
BASE_DIR = Path(__file__).resolve().parent
REPO_COOKIES_PATH = BASE_DIR / "cookies.txt"

# Force check into Render Logs at startup
print(f"--- STARTUP: Checking for cookies at {REPO_COOKIES_PATH} ---", flush=True)
if REPO_COOKIES_PATH.exists():
    print(f"--- SUCCESS: cookies.txt found (Size: {REPO_COOKIES_PATH.stat().st_size} bytes) ---", flush=True)
else:
    print("--- WARNING: cookies.txt NOT FOUND in repository root ---", flush=True)

@app.route("/", methods=["GET"])
def handle_audio_request():
    video_url = request.args.get("url")
    if not video_url:
        return jsonify(error="Missing URL parameter"), 400

    # Retrieve Tokens from Render Environment
    po_token = os.getenv("YOUTUBE_PO_TOKEN")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA")
    
    # Debugging Tokens in logs (Partial mask for security)
    if po_token:
        print(f"--- Using PO_TOKEN: {po_token[:5]}... ---", flush=True)
    else:
        print("--- WARNING: YOUTUBE_PO_TOKEN env var is missing ---", flush=True)

    filename = f"{uuid4()}.mp3"
    output_path = Path(ABS_DOWNLOADS_PATH) / filename

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path.with_suffix('')),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        # --- 2025 BROWSER IMPERSONATION ---
        'impersonate': 'chrome', 
        'quiet': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['mweb', 'web', 'ios', 'android'],
                'po_token': [f'web+{po_token}'] if po_token else [],
                'visitor_data': visitor_data if visitor_data else ""
            }
        },
        'http_headers': {
            # Matches your specific Android/Chrome Mobile session found in logs
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
        return _generate_token_response(filename)
    except Exception as e:
        app.logger.error(f"yt-dlp Error: {str(e)}")
        return jsonify(error="YouTube block", detail=str(e)), 500

@app.route("/download", methods=["GET"])
def download_audio():
    token = request.args.get("token")
    if not token or not access_manager.has_access(token):
        return jsonify(error="Invalid or missing token"), 401
    
    try:
        filename = access_manager.get_audio_file(token)
        access_manager.invalidate_token(token)
        
        # path= filename fixes the Flask 500 error found in logs
        return send_from_directory(
            ABS_DOWNLOADS_PATH, 
            path=filename, 
            as_attachment=True, 
            mimetype='audio/mpeg'
        )
    except Exception as e:
        app.logger.error(f"Download route error: {e}")
        return jsonify(error="File error"), 404

def _generate_token_response(filename: str):
    token = secrets.token_urlsafe(TOKEN_LENGTH)
    access_manager.add_token(token, filename)
    return jsonify(token=token), 200

def start_token_cleaner():
    threading.Thread(target=access_manager.manage_tokens, daemon=True).start()

with app.app_context():
    start_token_cleaner()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
