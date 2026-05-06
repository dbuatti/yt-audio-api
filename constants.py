import os
from pathlib import Path

# --- Directory Configurations ---
ROOT_DIRECTORY = Path(__file__).resolve().parent
DOWNLOADS_DIRECTORY = 'downloads'
ABS_DOWNLOADS_PATH = str(ROOT_DIRECTORY / DOWNLOADS_DIRECTORY)

# --- HTTP Response Codes ---
REQUEST_TIMEOUT = 408       # Used when token is expired
UNAUTHORIZED = 401          # Used when token is invalid
NOT_FOUND = 404             # File not found on server
BAD_REQUEST = 400           # Missing parameters in request
INTERNAL_SERVER_ERROR = 500 # Fallback for unexpected server errors

# --- Token Settings ---
EXPIRY_TIME_MINUTES = 5     # Token expiration duration in minutes
TOKEN_LENGTH = 20           # Length of generated token (in characters)
