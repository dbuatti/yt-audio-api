import os
import secrets
import threading
import time
import uuid
import requests
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
# Enable CORS for all routes so your React app can talk to it
CORS(app, resources={r"/*": {"origins": "*"}})

# Directory for temporary downloads
DOWNLOAD_DIR = "/tmp/downloads" if os.path.exists("/tmp") else "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Shared memory for tracking background tasks
active_tokens = {}

def cleanup_old_files():
    """Removes files older than 10 minutes to save disk space."""
    while True:
        now = time.time()
        for token, data in list(active_tokens.items()):
            if now - data['timestamp'] > 600:  # 10 minutes
                file_path = data.get('file_path')
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
                active_tokens.pop(token, None)
        time.sleep(60)

# Start cleanup thread
threading.Thread(target=cleanup_old_files, daemon=True).start()

def download_task(token, video_url):
    """Background task to download and convert the video."""
    po_token = os.environ.get("YOUTUBE_PO_TOKEN")
    visitor_data = os.environ.get("YOUTUBE_VISITOR_DATA")
    proxy_url = os.environ.get("PROXY_URL")
    
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        # Identity Settings
        'impersonate': 'chrome-110', 
        'po_token': f"web+none:{po_token}" if po_token else None,
        'headers': {
            'X-Goog-Visitor-Id': visitor_data if visitor_data else None,
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1'
        },
        'proxy': proxy_url if proxy_url else None,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        
        # Mark as complete
        active_tokens[token]['file_path'] = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
        active_tokens[token]['status'] = 'ready'
    except Exception as e:
        print(f"yt-dlp Error: {str(e)}")
        active_tokens[token]['status'] = 'error'
        active_tokens[token]['error_message'] = "YouTube Block" if "500" in str(e) or "sign in" in str(e).lower() else str(e)

@app.route('/')
def handle_request():
    """Accepts a URL and returns a tracking token immediately."""
    video_url = request.args.get('url')
    if not video_url:
        return jsonify({"error": "No URL provided"}), 400

    token = str(uuid.uuid4())
    active_tokens[token] = {
        'status': 'processing',
        'timestamp': time.time(),
        'file_path': None
    }

    # Start the download in a background thread
    thread = threading.Thread(target=download_task, args=(token, video_url))
    thread.start()

    return jsonify({"token": token})

@app.route('/download')
def check_status():
    """Check if the file is ready or download it."""
    token = request.args.get('token')
    if not token or token not in active_tokens:
        return jsonify({"error": "Invalid Token"}), 404

    task = active_tokens[token]

    if task['status'] == 'processing':
        return jsonify({"status": "processing"}), 202
    
    if task['status'] == 'error':
        return jsonify({"error": task.get('error_message', 'Unknown Error')}), 500

    if task['status'] == 'ready':
        return send_file(task['file_path'], as_attachment=True, download_name="audio.mp3")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
