import os
import threading
import time
import uuid
import sys
import gc
import random
import httpx # Added for the fix
from flask import Flask, jsonify
from flask_cors import CORS
import yt_dlp
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions # Added for the fix

# --- Startup Log ---
print("[SYSTEM] >>> 2026 PRODUCTION WORKER INITIALIZING <<<", flush=True)

app = Flask(__name__)
CORS(app)

# --- Configuration ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
PO_TOKEN = os.environ.get("YOUTUBE_PO_TOKEN")
DATA_SYNC_ID = os.environ.get("YOUTUBE_DATA_SYNC_ID")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CRITICAL] Missing Supabase Environment Variables!", flush=True)
    sys.exit(1)

# --- FIX: Manual HTTP Client to bypass 'proxy' bug ---
# This hands Supabase an already-built client so it doesn't try to build one internally
# and trigger the 'proxy' argument TypeError.
http_client = httpx.Client()
supabase: Client = create_client(
    SUPABASE_URL, 
    SUPABASE_KEY,
    options=ClientOptions(http_client=http_client)
)

# ... [The rest of your process_queued_song and job_poller logic remains the same] ...
