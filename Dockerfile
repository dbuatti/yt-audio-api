FROM python:3.12-slim

# Install system dependencies including latest FFmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy code and install Python deps
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# Expose port (Render uses $PORT)
ENV PORT=8080
EXPOSE 8080

# Run with gunicorn
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:$PORT"]
