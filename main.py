import os, secrets, threading, requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from uuid import uuid4
from pathlib import Path
import yt_dlp
import access_manager
from constants import *

app = Flask(__name__)

# Correct CORS for Local Dev and Vercel
CORS(app, resources={
    r"/*": {
        "origins": ["http://localhost:32141", "http://localhost:5173", "https://gig-studio-pro.vercel.app"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Disposition"]
    }
})

COOKIES_FILE_PATH = Path("/tmp/cookies.txt")

def download_cookies_from_url():
    cookies_url = os.getenv("COOKIES_URL")
    if not cookies_url: return True
    try:
        r = requests.get(cookies_url, timeout=10)
        r.raise_for_status()
        with open(COOKIES_FILE_PATH, "wb") as f: f.write(r.content)
        return True
    except Exception as e:
        app.logger.error(f"Cookie sync failed: {e}")
        return False

@app.route("/", methods=["GET"])
def handle_audio_request():
    video_url = request.args.get("url")
    if not video_url: return jsonify(error="Missing URL"), 400

    download_cookies_from_url()
    
    # Get Tokens from Render Environment Variables
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
        'impersonate': 'chrome', # Mimics browser TLS fingerprint
        'quiet': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'mweb'],
                'po_token': [f'web+{po_token}'] if po_token else [],
                'visitor_data': visitor_data if visitor_data else ""
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }
    }

    if COOKIES_FILE_PATH.exists():
        ydl_opts['cookiefile'] = str(COOKIES_FILE_PATH)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        return _generate_token_response(filename)
    except Exception as e:
        app.logger.error(f"Download error: {str(e)}")
        return jsonify(error="YouTube block", detail=str(e)), 500

@app.route("/download", methods=["GET"])
def download_audio():
    token = request.args.get("token")
    if not token or not access_manager.has_access(token):
        return jsonify(error="Invalid token"), 401
    
    try:
        filename = access_manager.get_audio_file(token)
        access_manager.invalidate_token(token)
        # Use path= instead of filename= for modern Flask
        return send_from_directory(ABS_DOWNLOADS_PATH, path=filename, as_attachment=True, mimetype='audio/mpeg')
    except Exception as e:
        return jsonify(error="File error"), 404

def _generate_token_response(filename: str):
    token = secrets.token_urlsafe(TOKEN_LENGTH)
    access_manager.add_token(token, filename)
    return jsonify(token=token), 200

if __name__ == "__main__":
    threading.Thread(target=access_manager.manage_tokens, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
