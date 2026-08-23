#!/bin/bash
set -e

echo "Starting Evara IoT Middleware Entrypoint..."

# Ensure instance directory exists and has correct permissions
mkdir -p /app/instance
chmod 777 /app/instance

# Initialize database schema (runs once before Gunicorn starts)
echo "Initializing database schema..."
python << END
from app import create_app
from models import db
app = create_app('production')
with app.app_context():
    db.create_all()
    print('Database initialized successfully.')
END

# Start Gunicorn
echo "Starting Gunicorn..."
exec gunicorn --bind 0.0.0.0:8080 --workers 4 --timeout 120 --access-logfile - --error-logfile - app:app
