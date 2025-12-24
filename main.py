"""
main.py
YouTube Audio Converter API - Fixed for Render & CORS
"""

import os
import secrets
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from uuid import uuid4
from pathlib import Path
import yt_dlp
import access_manager
from constants import *

app = Flask(__name__)

# --- FIX 1: EXPLICIT CORS ORIGINS ---
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

COOKIES_FILE_PATH = Path("/tmp/cookies.txt")

def download_cookies_from_url():
    cookies_url = os.getenv("COOKIES_URL")
    if not cookies_url:
        if COOKIES_FILE_PATH.exists(): COOKIES_FILE_PATH.unlink()
        return True
    try:
        response = requests.get(cookies_url, timeout=15)
        response.raise_for_status()
        with open(COOKIES_FILE_PATH, "wb") as f:
            f.write(response.content)
        return True
    except Exception as e:
        app.logger.error(f"Cookie Sync Failed: {str(e)}")
        return False

download_cookies_from_url()

@app.route("/", methods=["GET"])
def handle_audio_request():
    video_url = request.args.get("url")
    if not video_url:
        return jsonify(error="Missing 'url' parameter."), 400

    download_cookies_from_url()
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
        'quiet': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    if COOKIES_FILE_PATH.exists():
        ydl_opts['cookiefile'] = str(COOKIES_FILE_PATH)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        return jsonify(error="Download failed", detail=str(e)), 500

    return _generate_token_response(filename)

@app.route("/download", methods=["GET"])
def download_audio():
    token = request.args.get("token")
    if not token or not access_manager.has_access(token):
        return jsonify(error="Invalid token"), 401
    
    if not access_manager.is_valid(token):
        return jsonify(error="Expired token"), 408

    try:
        filename = access_manager.get_audio_file(token)
        access_manager.invalidate_token(token)
        
        # --- FIX 2: MODERN FLASK SYNTAX (path= instead of filename=) ---
        return send_from_directory(
            ABS_DOWNLOADS_PATH, 
            path=filename, 
            as_attachment=True, 
            mimetype='audio/mpeg'
        )
    except Exception as e:
        app.logger.error(f"Serving error: {str(e)}")
        return jsonify(error="File not found or server error"), 500

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
