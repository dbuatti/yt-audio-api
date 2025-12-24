import os
import secrets
import threading
import time
import uuid
import sys
import requests
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
# Enable CORS for your React frontend
CORS(app, resources={r"/*": {"origins": "*"}})

# Explicit logging for Render's log viewer
def log(message):
    print(f"[SERVER LOG] {message}")
    sys.stdout.flush()

# Use /tmp for Render's ephemeral storage
DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

active_tokens = {}

def cleanup_old_files():
    """Background loop to prevent /tmp from filling up."""
    while True:
        now = time.time()
        for token, data in list(active_tokens.items()):
            if now - data['timestamp'] > 600:  # 10 minutes
                file_path = data.get('file_path')
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        log(f"Cleaned up expired file: {token}")
                    except Exception as e:
                        log(f"Cleanup error: {e}")
                active_tokens.pop(token, None)
        time.sleep(60)

# Start cleanup thread immediately
threading.Thread(target=cleanup_old_files, daemon=True).start()

def download_task(token, video_url):
    """The core engine that talks to YouTube via Proxy and PO_TOKEN."""
    log(f"Task Started | Token: {token} | URL: {video_url}")
    
    po_token = os.environ.get("YOUTUBE_PO_TOKEN")
    visitor_data = os.environ.get("YOUTUBE_VISITOR_DATA")
    proxy_url = os.environ.get("PROXY_URL")
    
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    # Determine cookie path: Secret file takes priority, then local repo file
    cookie_path = '/etc/secrets/cookies.txt' if os.path.exists('/etc/secrets/cookies.txt') else 'cookies.txt'

    ydl_opts = {
        'format': 'wa*/ba*',
        'outtmpl': output_template,
        'cookiefile': cookie_path, # Fixed syntax error here
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        # Identity Logic
        'impersonate': 'chrome-110',
        'po_token': f"web+none:{po_token}" if po_token else None,
        'headers': {
            'X-Goog-Visitor-Id': visitor_data if visitor_data else None,
        },
        'proxy': proxy_url if proxy_url else None,
        'quiet': False,
        'no_warnings': False,
    }

    try:
        log(f"Attempting yt-dlp with Cookie Path: {cookie_path}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        
        expected_file = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
        if os.path.exists(expected_file):
            log(f"SUCCESS | File created for token: {token}")
            active_tokens[token]['file_path'] = expected_file
            active_tokens[token]['status'] = 'ready'
        else:
            raise Exception("yt-dlp finished but MP3 file was not found.")

    except Exception as e:
        error_msg = str(e)
        log(f"ERROR | Token: {token} | Message: {error_msg}")
        active_tokens[token]['status'] = 'error'
        
        if "sign in" in error_msg.lower() or "confirm you are not a bot" in error_msg.lower():
            active_tokens[token]['error_message'] = "YouTube Block (Cookies Expired or IP Blacklisted)"
        else:
            active_tokens[token]['error_message'] = error_msg

@app.route('/')
def init_download():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({"error": "No URL provided"}), 400

    token = str(uuid.uuid4())
    active_tokens[token] = {
        'status': 'processing',
        'timestamp': time.time(),
        'file_path': None
    }

    thread = threading.Thread(target=download_task, args=(token, video_url))
    thread.start()

    return jsonify({"token": token})

@app.route('/download')
def get_file():
    token = request.args.get('token')
    if not token or token not in active_tokens:
        return jsonify({"error": "Invalid or expired token"}), 404

    task = active_tokens[token]

    if task['status'] == 'processing':
        return jsonify({"status": "processing"}), 202
    
    if task['status'] == 'error':
        return jsonify({"error": task.get('error_message', 'Unknown failure')}), 500

    if task['status'] == 'ready':
        return send_file(task['file_path'], as_attachment=True, download_name="audio.mp3")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
