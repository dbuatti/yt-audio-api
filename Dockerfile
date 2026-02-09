FROM python:3.12-slim

# Install system dependencies, Node.js, and Deno
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
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create folder for temporary storage
RUN mkdir -p /tmp/downloads && chmod 777 /tmp/downloads

# Set environment variables
ENV PORT=10000
ENV PYTHONUNBUFFERED=1

EXPOSE 10000

CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "4", "--timeout", "120", "--worker-class", "gthread"]
