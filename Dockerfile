# Use slim Python image for smaller size
FROM python:3.12-slim

# Install FFmpeg (critical for audio conversion)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy all files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port (Render uses $PORT)
ENV PORT=8080
EXPOSE 8080

# Run with Gunicorn
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:$PORT"]
