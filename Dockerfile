FROM python:3.12-slim

# Install FFmpeg and JS Runtimes (Deno + Node)
RUN apt-get update && apt-get install -y ffmpeg curl unzip nodejs npm && \
    curl -fsSL https://deno.land/x/install/install.sh | sh && \
    ln -s /root/.deno/bin/deno /usr/local/bin/deno && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Install Python dependencies and force update yt-dlp to latest
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install -U yt-dlp

# Create folder for storage
RUN mkdir -p downloads && chmod 777 downloads

EXPOSE 10000

# Using gthread for better stability on Render's 512MB RAM
CMD ["sh", "-c", "gunicorn main:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 120 --worker-class gthread"]
