FROM python:3.12-slim

# Install FFmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

# Remove hardcoded PORT — Render provides $PORT at runtime
# EXPOSE is optional, but good practice
EXPOSE 10000

# Use Render's $PORT environment variable directly
CMD exec gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --worker-class sync
