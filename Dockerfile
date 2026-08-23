# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV FLASK_ENV production

# Set work directory
WORKDIR /app

# Install system dependencies
# Added curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# Copy the rest of the application code
COPY . .

# Create the instance directory for SQLite mirror persistence
RUN mkdir -p /app/instance && chmod 777 /app/instance

# Copy and prepare entrypoint script
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose the port the app runs on
EXPOSE 8080

# Use entrypoint script to initialize DB and start Gunicorn
ENTRYPOINT ["/app/entrypoint.sh"]
