import os
import threading
import time
import uuid
import sys
import traceback
import gc
from flask import Flask, request, send_file, jsonify, make_response
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)

# Strict CORS for production stability
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"]
    }
})

# MAX SAFETY CONCURRENCY: Set to 1 for 512MB RAM servers
# This forces the server to finish one song completely before starting the next.
download_semaphore = threading.BoundedSemaphore(value=1)

def log(message):
    print(f"[SERVER LOG] {message}")
    sys.stdout.flush()

DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

active_tokens = {}

def cleanup_old_files():
    """Removes files older than 10 mins to stay within Render's disk limits."""
    while True:
        now = time.time()
        for token, data in list(active_tokens.items()):
            if now - data['timestamp'] > 600:
                file_path = data.get('file_path')
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        log(f"Cleanup: Removed {token}")
                    except Exception as e:
                        log(f"Cleanup error: {e}")
                active_tokens.pop(token, None)
        # Clear memory after cleanup
        gc.collect()
        time.sleep(60)

threading.Thread(target=cleanup_old_files, daemon=True).start()

def download_task(token, video_url):
    # Wait here if another task is running.
    # This prevents the 512MB RAM Out-Of-Memory crash.
    with download_semaphore:
        log(f"--- STARTING DOWNLOAD TASK | Token: {token} ---")
        
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
        use_cookies = next((p for p in paths_to_check if os.path.exists(p)), None)

        ydl_opts = {
            'format': 'wa',
            'noplaylist': True,
            'outtmpl': output_template,
            'progress_hooks': [progress_hook],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'po_token': f"web+none:{po_token}" if po_token else None,
            'headers': {'X-Goog-Visitor-Id': visitor_data if visitor_data else None},
            'proxy': proxy_url if proxy_url else None,
            'cookiefile': use_cookies,
            'nocheckcertificate': True,
            'verbose': False, # Reduced verbosity to save log memory
            'quiet': False,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            expected_file = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp3")
            if os.path.exists(expected_file):
                log(f"SUCCESS | MP3 ready: {token}")
                active_tokens[token]['file_path'] = expected_file
                active_tokens[token]['status'] = 'ready'
            else:
                raise Exception("File not found after conversion")

        except Exception as e:
            log(f"ERROR | {token} | {str(e)}")
            active_tokens[token]['status'] = 'error'
            active_tokens[token]['error_message'] = str(e)
        
        # Force Python to release memory after each song
        gc.collect()

@app.route('/')
def init_download():
    video_url = request.args.get('url')
    if not video_url: return jsonify({"error": "No URL"}), 400
    
    token = str(uuid.uuid4())
    active_tokens[token] = {
        'status': 'processing', 
        'progress': 0, 
        'timestamp': time.time(), 
        'file_path': None
    }
    
    log(f"New Request Queued | Token: {token}")
    threading.Thread(target=download_task, args=(token, video_url)).start()
    return jsonify({"token": token})

@app.route('/download')
def get_file():
    token = request.args.get('token')
    if not token or token not in active_tokens:
        return jsonify({"error": "Invalid token"}), 404
    
    task = active_tokens[token]
    if task['status'] == 'processing':
        return jsonify({
            "status": "processing", 
            "progress": task.get('progress', 0)
        }), 202
    
    if task['status'] == 'error':
        return jsonify({"status": "error", "error": task.get('error_message')}), 500

    if task['status'] == 'ready':
        log(f"Serving file: {token}")
        response = make_response(send_file(
            task['file_path'], 
            as_attachment=True, 
            download_name="audio.mp3",
            mimetype="audio/mpeg"
        ))
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
