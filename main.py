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
CORS(app, resources={r"/*": {"origins": "*"}})

# Explicit logging function for Render
def log(message):
    print(f"[SERVER LOG] {message}")
    sys.stdout.flush()

DOWNLOAD_DIR = "/tmp/downloads" if os.path.exists("/tmp") else "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

active_tokens = {}

def cleanup_old_files():
    while True:
        now = time.time()
        for token, data in list(active_tokens.items()):
            if now - data['timestamp'] > 600:
                file_path = data.get('file_path')
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        log(f"Cleaned up file for token: {token}")
                    except:
                        pass
                active_tokens.pop(token, None)
        time.sleep(60)

threading.Thread(target=cleanup_old_files, daemon=True).start()

def download_task(token, video_url):
    log(f"Starting download task for URL: {video_url}")
    
    po_token = os.environ.get("YOUTUBE_PO_TOKEN")
    visitor_data = os.environ.get("YOUTUBE_VISITOR_DATA")
    proxy_url = os.environ.get("PROXY_URL")
    
    log(f"Using Proxy: {'Yes' if proxy_url else 'No'}")
    log(f"PO Token Present: {'Yes' if po_token else 'No'}")

    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    ydl_opts = {
        'format': 'wa*/ba*',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128', # Reduced to save RAM on Render
        }],
        'impersonate': 'chrome-110',
        'po_token': f"web+none:{po_token}" if po_token else None,
        'headers': {
            'X-Goog-Visitor-Id': visitor_data if visitor_data else None,
        },
        'proxy': proxy_url if proxy_url else None,
        'quiet': False, # Set to False so we see yt-dlp's own logs
        'no_warnings': False,
    }

    try:
        log("Invoking yt-dlp...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        
        expected_file = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
        if os.path.exists(expected_file):
            log(f"Download successful! File ready: {expected_file}")
            active_tokens[token]['file_path'] = expected_file
            active_tokens[token]['status'] = 'ready'
        else:
            log("yt-dlp finished but file not found in /tmp")
            active_tokens[token]['status'] = 'error'
            active_tokens[token]['error_message'] = "File conversion failed"

    except Exception as e:
        error_str = str(e)
        log(f"CRITICAL ERROR in download_task: {error_str}")
        active_tokens[token]['status'] = 'error'
        # Check for specific YouTube blocks
        if "500" in error_str or "sign in" in error_str.lower():
            active_tokens[token]['error_message'] = "YouTube Blocked this Proxy/Token"
        else:
            active_tokens[token]['error_message'] = error_str

@app.route('/')
def handle_request():
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({"error": "No URL provided"}), 400

    token = str(uuid.uuid4())
    log(f"Generated new token: {token} for URL")
    
    active_tokens[token] = {
        'status': 'processing',
        'timestamp': time.time(),
        'file_path': None
    }

    thread = threading.Thread(target=download_task, args=(token, video_url))
    thread.start()

    return jsonify({"token": token})

@app.route('/download')
def check_status():
    token = request.args.get('token')
    if not token or token not in active_tokens:
        log(f"Download attempt with invalid token: {token}")
        return jsonify({"error": "Invalid Token"}), 404

    task = active_tokens[token]
    log(f"Status check for token {token}: {task['status']}")

    if task['status'] == 'processing':
        return jsonify({"status": "processing"}), 202
    
    if task['status'] == 'error':
        return jsonify({"error": task.get('error_message', 'Unknown Error')}), 500

    if task['status'] == 'ready':
        log(f"Sending file for token {token}")
        return send_file(task['file_path'], as_attachment=True, download_name="audio.mp3")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    log(f"Server starting on port {port}...")
    app.run(host='0.0.0.0', port=port)
