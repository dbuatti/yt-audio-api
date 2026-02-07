FROM python:3.12-slim

# Install system dependencies, Node.js, and Deno
# We combine these to keep the image size small
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    nodejs \
    npm \
    && curl -fsSL https://deno.land/x/install/install.sh | sh \
    && ln -s /root/.deno/bin/deno /usr/local/bin/deno \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install -U yt-dlp

# Copy the rest of the application
COPY . .

# Create folder for temporary storage
RUN mkdir -p downloads && chmod 777 downloads

# Set environment variables
ENV PORT=10000
ENV PYTHONUNBUFFERED=1

EXPOSE 10000

# Using gthread with 1 worker and 4 threads is perfect for 512MB RAM
CMD ["sh", "-c", "gunicorn main:app --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 120 --worker-class gthread"]
