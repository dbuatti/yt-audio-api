"""
main.py
YouTube Audio Converter API - Production-ready for Render.com
With dynamic cookies.txt download from Supabase Storage (via COOKIES_URL env var)
"""

import os
import secrets
import threading
import requests  # Added for downloading cookies
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from uuid import uuid4
from pathlib import Path
import yt_dlp
import access_manager
from constants import *

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Allows cross-origin requests from your frontend

# Global variable to hold the local path to the current cookies file
COOKIES_FILE_PATH = Path("/tmp/cookies.txt")  # Temporary path in Render's filesystem


def download_cookies_from_url():
    """
    Download cookies.txt from the URL in COOKIES_URL env var.
    Returns True if successful or no URL set, False on failure.
    """
    cookies_url = os.getenv("COOKIES_URL")
    if not cookies_url:
        app.logger.info("No COOKIES_URL set – running without cookies.")
        if COOKIES_FILE_PATH.exists():
            COOKIES_FILE_PATH.unlink()  # Remove any old file
        return True

    try:
        app.logger.info(f"Downloading fresh cookies from {cookies_url}")
        response = requests.get(cookies_url, timeout=10)
        response.raise_for_status()
        with open(COOKIES_FILE_PATH, "wb") as f:
            f.write(response.content)
        app.logger.info("Cookies downloaded successfully.")
        return True
    except Exception as e:
        app.logger.error(f"Failed to download cookies: {str(e)}")
        return False


# Download cookies once at startup
download_cookies_from_url()


@app.route("/", methods=["GET"])
def handle_audio_request():
    """
    Main endpoint: Receive YouTube URL → download + convert to MP3 → return secure token
    """
    video_url = request.args.get("url")
    if not video_url:
        return jsonify(error="Missing 'url' parameter in request."), BAD_REQUEST

    # Refresh cookies on every request (in case they expired/updated in Supabase)
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
        'extractaudio': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Referer': 'https://www.youtube.com/',
        },
        'sleep_interval': 3,
        'max_sleep_interval': 10,
    }

    # Add cookies if file exists
    if COOKIES_FILE_PATH.exists():
        ydl_opts['cookiefile'] = str(COOKIES_FILE_PATH)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        app.logger.error(f"Download failed: {str(e)}")
        error_msg = str(e)
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            return jsonify(
                error="YouTube blocked this request (common on cloud servers).",
                hint="Try updating your cookies.txt in Supabase or using residential proxies."
            ), 503
        return jsonify(error="Failed to download or convert audio.", detail=error_msg), INTERNAL_SERVER_ERROR

    return _generate_token_response(filename)


@app.route("/download", methods=["GET"])
def download_audio():
    token = request.args.get("token")
    if not token:
        return jsonify(error="Missing 'token' parameter in request."), BAD_REQUEST

    if not access_manager.has_access(token):
        return jsonify(error="Token is invalid or unknown."), UNAUTHORIZED

    if not access_manager.is_valid(token):
        return jsonify(error="Token has expired."), REQUEST_TIMEOUT

    try:
        filename = access_manager.get_audio_file(token)
        directory = ABS_DOWNLOADS_PATH
        access_manager.invalidate_token(token)  # One-time use
        return send_from_directory(directory, filename=filename, as_attachment=True, mimetype='audio/mpeg')
    except FileNotFoundError:
        return jsonify(error="Requested file could not be found on the server."), NOT_FOUND
    except Exception as e:
        app.logger.error(f"File serving error: {str(e)}")
        return jsonify(error="Server error during file serving."), INTERNAL_SERVER_ERROR


def _generate_token_response(filename: str):
    token = secrets.token_urlsafe(TOKEN_LENGTH)
    access_manager.add_token(token, filename)
    return jsonify(token=token), 200


def start_token_cleaner():
    cleaner_thread = threading.Thread(target=access_manager.manage_tokens, daemon=True)
    cleaner_thread.start()


with app.app_context():
    start_token_cleaner()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
