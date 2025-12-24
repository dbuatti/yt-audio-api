FROM python:3.12-slim

# Install FFmpeg and clean up to keep image small
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create downloads directory (referenced in constants.py)
RUN mkdir -p downloads && chmod 777 downloads

# Render sets the $PORT variable; we bind to it at runtime
EXPOSE 10000

CMD ["sh", "-c", "gunicorn main:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --timeout 120"]
