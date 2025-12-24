"""
main.py
YouTube Audio Converter API - Fully optimized for Render.com (Docker)
Features: Fixed CORS for Local Dev, dynamic cookies, robust error handling
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

# Initialize Flask app
app = Flask(__name__)

# --- UPDATED CORS CONFIGURATION ---
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:32141",          # Local Dev
            "http://localhost:5173",           # Standard Vite Dev
            "https://gig-studio-pro.vercel.app" # Production
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Disposition"] # Useful if you need to read the filename in frontend
    }
})

# Path for temporary cookies file
COOKIES_FILE_PATH = Path("/tmp/cookies.txt")

def download_cookies_from_url():
    """Download fresh cookies.txt from COOKIES_URL env var (Supabase public URL)"""
    cookies_url = os.getenv("COOKIES_URL")
    if not cookies_url:
        app.logger.info("No COOKIES_URL set – proceeding without cookies.")
        if COOKIES_FILE_PATH.exists():
            COOKIES_FILE_PATH.unlink()
        return True

    try:
        app.logger.info(f"Downloading cookies from {cookies_url}")
        response = requests.get(cookies_url, timeout=15)
        response.raise_for_status()
        with open(COOKIES_FILE_PATH, "wb") as f:
            f.write(response.content)
        app.logger.info("Cookies downloaded and saved successfully.")
        return True
    except Exception as e:
        app.logger.error(f"Failed to download cookies: {str(e)}")
        return False

# Download cookies at startup
download_cookies_from_url()

@app.route("/", methods=["GET"])
def handle_audio_request():
    # flask-cors handles the OPTIONS/Preflight automatically now
    
    video_url = request.args.get("url")
    if not video_url:
        return jsonify(error="Missing 'url' parameter."), 400

    # Refresh cookies on every request to ensure they aren't stale
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
        'no_warnings': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.youtube.com/',
        },
        'sleep_interval': 3,
        'max_sleep_interval': 10,
    }

    # Use cookies if available
    if COOKIES_FILE_PATH.exists():
        ydl_opts['cookiefile'] = str(COOKIES_FILE_PATH)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        app.logger.error(f"yt-dlp failed: {str(e)}")
        error_msg = str(e)
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            return jsonify(
                error="YouTube blocked the request (common on cloud servers).",
                hint="Update cookies.txt in Supabase or use residential proxies."
            ), 503
        return jsonify(error="Failed to download or convert audio.", detail=error_msg), 500

    return _generate_token_response(filename)


@app.route("/download", methods=["GET"])
def download_audio():
    token = request.args.get("token")
    if not token:
        return jsonify(error="Missing 'token' parameter."), 400

    if not access_manager.has_access(token):
        return jsonify(error="Invalid or unknown token."), 401

    if not access_manager.is_valid(token):
        return jsonify(error="Token has expired."), 408

    try:
        filename = access_manager.get_audio_file(token)
        directory = ABS_DOWNLOADS_PATH
        access_manager.invalidate_token(token)  # One-time use
        return send_from_directory(directory, path=filename, as_attachment=True, mimetype='audio/mpeg')
    except FileNotFoundError:
        return jsonify(error="File not found on server."), 404
    except Exception as e:
        app.logger.error(f"Serving error: {str(e)}")
        return jsonify(error="Server error."), 500


def _generate_token_response(filename: str):
    token = secrets.token_urlsafe(TOKEN_LENGTH)
    access_manager.add_token(token, filename)
    return jsonify(token=token), 200


def start_token_cleaner():
    cleaner_thread = threading.Thread(target=access_manager.manage_tokens, daemon=True)
    cleaner_thread.start()


# Start cleanup thread
with app.app_context():
    start_token_cleaner()


if __name__ == "__main__":
    # Note: When running on Render via Gunicorn, this block is ignored.
    # The port is handled by the Docker CMD.
    app.run(host="0.0.0.0", port=5000, debug=False)
