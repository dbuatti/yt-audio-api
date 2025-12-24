"""
main.py
YouTube Audio Converter API - Updated for Render.com Deployment
Original by Alperen Sümeroğlu | Enhanced for CORS + Production
"""

import secrets
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS  # <-- Added for cross-origin requests
from uuid import uuid4
from pathlib import Path
import yt_dlp
import access_manager
from constants import *

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for all routes
# Remove or restrict origins in production if needed (e.g., your actual frontend domain)
CORS(app)  # Allows requests from localhost, your frontend, etc.


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

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path.with_suffix('')),  # yt-dlp handles extension
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
        'extractaudio': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        app.logger.error(f"Download failed: {str(e)}")
        return jsonify(error="Failed to download or convert audio.", detail=str(e)), INTERNAL_SERVER_ERROR

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
        # Mark token as used/invalidate after successful download (optional security)
        access_manager.invalidate_token(token)
        return send_from_directory(directory, filename=filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify(error="Requested file could not be found on the server."), NOT_FOUND
    except Exception as e:
        app.logger.error(f"Download serve error: {str(e)}")
        return jsonify(error="Server error during file serving."), INTERNAL_SERVER_ERROR


def _generate_token_response(filename: str):
    """
    Generate secure token and register it with access_manager
    """
    token = secrets.token_urlsafe(TOKEN_LENGTH)
    access_manager.add_token(token, filename)
    return jsonify(token=token)


# Background thread for cleaning expired tokens/files
def start_token_cleaner():
    cleaner_thread = threading.Thread(
        target=access_manager.manage_tokens,
        daemon=True
    )
    cleaner_thread.start()


# Entry point for production (Gunicorn on Render)
if __name__ == "__main__":
    # Only run dev server if executed directly (local testing)
    start_token_cleaner()
    app.run(host="0.0.0.0", port=5000, debug=True)
else:
    # When imported by Gunicorn (on Render), start cleaner on app context
    with app.app_context():
        start_token_cleaner()
