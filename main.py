import os
import threading
import time
import uuid
import sys
import traceback
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

def log(message):
    print(f"[SERVER LOG] {message}")
    sys.stdout.flush()

DOWNLOAD_DIR = "/tmp/downloads"
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
                        log(f"Cleanup: Removed expired file for {token}")
                    except Exception as e:
                        log(f"Cleanup error: {e}")
                active_tokens.pop(token, None)
        time.sleep(60)

threading.Thread(target=cleanup_old_files, daemon=True).start()

def download_task(token, video_url):
    log(f"--- STARTING TASK | Token: {token} ---")
    
    # Progress Hook to update the dictionary
    def progress_hook(d):
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%','')
            try:
                active_tokens[token]['progress'] = float(p)
            except:
                pass
        elif d['status'] == 'finished':
            active_tokens[token]['progress'] = 100

    po_token = os.environ.get("YOUTUBE_PO_TOKEN")
    visitor_data = os.environ.get("YOUTUBE_VISITOR_DATA")
    proxy_url = os.environ.get("PROXY_URL")
    
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    paths_to_check = ['./cookies.txt', '/etc/secrets/cookies.txt']
    use_cookies = None
    for path in paths_to_check:
        if os.path.exists(path):
            use_cookies = path
            break

    ydl_opts = {
        'format': 'wa',
        'outtmpl': output_template,
        'progress_hooks': [progress_hook], # Integrated progress tracking
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }],
        'po_token': f"web+none:{po_token}" if po_token else None,
        'headers': {'X-Goog-Visitor-Id': visitor_data if visitor_data else None},
        'proxy': proxy_url if proxy_url else None,
        'cookiefile': use_cookies,
        'nocheckcertificate': True,
        'verbose': True,
        'quiet': False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        
        expected_file = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
        if os.path.exists(expected_file):
            log(f"SUCCESS | Token: {token}")
            active_tokens[token]['file_path'] = expected_file
            active_tokens[token]['status'] = 'ready'
        else:
            raise Exception("File conversion failed.")

    except Exception as e:
        log(f"ERROR | Token: {token} | {str(e)}")
        active_tokens[token]['status'] = 'error'
        active_tokens[token]['error_message'] = str(e)

@app.route('/')
def init_download():
    video_url = request.args.get('url')
    if not video_url: return jsonify({"error": "No URL"}), 400

    token = str(uuid.uuid4())
    active_tokens[token] = {
        'status': 'processing',
        'progress': 0, # Initialize progress
        'timestamp': time.time(),
        'file_path': None
    }
    threading.Thread(target=download_task, args=(token, video_url)).start()
    return jsonify({"token": token})

@app.route('/download')
def get_file():
    token = request.args.get('token')
    if not token or token not in active_tokens:
        return jsonify({"error": "Invalid token"}), 404

    task = active_tokens[token]

    if task['status'] == 'processing':
        # Return progress percentage to the frontend
        return jsonify({
            "status": "processing", 
            "progress": task.get('progress', 0)
        }), 202
    
    if task['status'] == 'error':
        return jsonify({"error": task.get('error_message')}), 500

    if task['status'] == 'ready':
        return send_file(task['file_path'], as_attachment=True, download_name="audio.mp3")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
