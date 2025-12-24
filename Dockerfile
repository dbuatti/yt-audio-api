FROM python:3.12-slim

# Install FFmpeg and Deno (JS Runtime)
RUN apt-get update && apt-get install -y ffmpeg curl unzip && \
    curl -fsSL https://deno.land/x/install/install.sh | sh && \
    ln -s /root/.deno/bin/deno /usr/local/bin/deno && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Install Python dependencies and force update yt-dlp
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install -U yt-dlp

# Create folder for storage (though /tmp is used in main.py, this is good practice)
RUN mkdir -p downloads && chmod 777 downloads

EXPOSE 10000

# Using gthread for better handling of background threading tasks
CMD ["sh", "-c", "gunicorn main:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 120 --worker-class gthread"]
