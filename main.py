"""
main.py
YouTube Audio Converter API - Production-ready version for Render.com
Original by Alperen Sümeroğlu | Enhanced with CORS, better error handling,
realistic headers, and production compatibility
"""

import secrets
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from uuid import uuid4
from pathlib import Path
import yt_dlp
import access_manager
from constants import *

# Initialize Flask app
app = Flask(__name__)

# Enable CORS - allows your frontend (localhost or production domain) to call the API
CORS(app)  # In production, you can restrict: CORS(app, origins=["https://your-frontend.com"])


@app.route("/", methods=["GET"])
def handle_audio_request():
    """
    Main endpoint: Receive YouTube URL → download + convert to MP3 → return secure token
    """
    video_url = request.args.get("url")
    if not video_url:
        return jsonify(error="Missing 'url' parameter in request."), BAD_REQUEST

    filename = f"{uuid4()}.mp3"
    output_path = Path(ABS_DOWNLOADS_PATH) / filename

    # yt-dlp options with realistic headers to reduce chance of "sign in" blocks
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path.with_suffix('')),  # Let yt-dlp manage extension
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
        # Small delays to appear less bot-like
        'sleep_interval': 3,
        'max_sleep_interval': 10,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        app.logger.error(f"Download failed: {str(e)}")
        # Provide more user-friendly error messages for common issues
        error_msg = str(e)
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            return jsonify(
                error="YouTube blocked this request (common on cloud servers).",
                detail="Try a different video or consider using residential proxies for reliability."
            ), 503  # Service Unavailable
        return jsonify(error="Failed to download or convert audio.", detail=error_msg), INTERNAL_SERVER_ERROR

    return _generate_token_response(filename)


@app.route("/download", methods=["GET"])
def download_audio():
    """
    Serve the MP3 file using a valid one-time token
    """
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

        # Optional: Invalidate token after successful download (one-time use)
        access_manager.invalidate_token(token)

        return send_from_directory(
            directory,
            filename=filename,
            as_attachment=True,
            mimetype='audio/mpeg'
        )
    except FileNotFoundError:
        return jsonify(error="Requested file could not be found on the server."), NOT_FOUND
    except Exception as e:
        app.logger.error(f"File serving error: {str(e)}")
        return jsonify(error="Server error during file serving."), INTERNAL_SERVER_ERROR


def _generate_token_response(filename: str):
    """
    Generate a secure token and register it with the access manager
    """
    token = secrets.token_urlsafe(TOKEN_LENGTH)
    access_manager.add_token(token, filename)
    return jsonify(token=token), 200


def start_token_cleaner():
    """
    Start the background thread that cleans expired tokens and files
    """
    cleaner_thread = threading.Thread(
        target=access_manager.manage_tokens,
        daemon=True
    )
    cleaner_thread.start()


# Start the token cleaner when the app starts (works with Gunicorn too)
with app.app_context():
    start_token_cleaner()


# Development server (only when running locally)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
