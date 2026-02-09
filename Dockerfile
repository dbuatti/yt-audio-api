FROM python:3.12-slim

# Install system dependencies + Deno in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && curl -fsSL https://deno.land/install.sh | sh \
    && ln -s /root/.deno/bin/deno /usr/local/bin/deno \
    && deno --version \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install dependencies first (caching layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create download folder with correct permissions
RUN mkdir -p /tmp/downloads && chmod 777 /tmp/downloads

ENV PORT=10000
ENV PYTHONUNBUFFERED=1

EXPOSE 10000

CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "4", "--timeout", "180", "--worker-class", "gthread"]
