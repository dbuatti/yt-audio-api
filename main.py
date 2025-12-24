"""
main.py
YouTube Audio Converter API - Local Repo Cookies Edition
"""

import os
import secrets
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from uuid import uuid4
from pathlib import Path
import yt_dlp
import access_manager
from constants import *

app = Flask(__name__)

# CORS remains the same for your local and production frontends
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

# --- COOKIES CONFIGURATION ---
# This points to the cookies.txt in your GitHub repository root
REPO_COOKIES_PATH = Path(__file__).parent / "cookies.txt"

@app.route("/", methods=["GET"])
def handle_audio_request():
    video_url = request.args.get("url")
    if not video_url:
        return jsonify(error="Missing URL parameter"), 400
    
    po_token = os.getenv("YOUTUBE_PO_TOKEN")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA")

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
            'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36',
            'Accept-Language': 'en-GB,en;q=0.9',
        }
    }

    # USE LOCAL REPO COOKIES
    if REPO_COOKIES_PATH.exists():
        ydl_opts['cookiefile'] = str(REPO_COOKIES_PATH)
        app.logger.info("Using cookies.txt from repository.")
    else:
        app.logger.warning("No cookies.txt found in repository root.")

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
        return send_from_directory(
            ABS_DOWNLOADS_PATH, 
            path=filename, 
            as_attachment=True, 
            mimetype='audio/mpeg'
        )
    except Exception as e:
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
