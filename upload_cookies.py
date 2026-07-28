import os
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
COOKIE_FILE = os.environ.get("COOKIE_FILE", "cookies.txt")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables.")
    exit(1)

if not os.path.exists(COOKIE_FILE):
    print(f"Cookie file not found: {COOKIE_FILE}")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

with open(COOKIE_FILE, "rb") as f:
    data = f.read()

try:
    supabase.storage.from_("cookies").upload("cookies.txt", data, {"upsert": "true"})
    print(f"Uploaded {len(data)} bytes to Supabase 'cookies' bucket.")
except Exception as e:
    print(f"Upload failed: {e}")
    exit(1)
