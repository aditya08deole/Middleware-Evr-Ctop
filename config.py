import os
import base64
import warnings
from datetime import timedelta

class Config:
    """Base configuration class"""
    # SECURITY: Secret key should be set via environment variable in production.
    # For local development, generate a temporary key and print a warning.
    SECRET_KEY = os.environ.get('SECRET_KEY')
    _env = os.environ.get('FLASK_ENV', os.environ.get('ENV', 'development'))

    if not SECRET_KEY:
        if _env == 'production':
            raise ValueError("SECRET_KEY environment variable must be set in production")
        # Development fallback: generate a random key and warn
        warnings.warn(
            "SECRET_KEY not set — generating a temporary secret for development. "
            "Do NOT use this in production. Set SECRET_KEY env var before deployment.",
            UserWarning
        )
        SECRET_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
    
    # Database configuration (SQLite for fallback, Firestore preferred)
    USE_FIREBASE = os.environ.get('USE_FIREBASE', 'false').lower() == 'true'
    
    _base_dir = os.path.abspath(os.path.dirname(__file__))
    _instance_dir = os.path.join(_base_dir, 'instance')
    
    # Ensure instance directory exists for local development
    if not os.path.exists(_instance_dir):
        try:
            os.makedirs(_instance_dir)
        except Exception:
            pass

    _default_db_path = os.path.join(_instance_dir, 'ctop_iot.db')
    
    # Use absolute path for SQLite to avoid ambiguity in Docker
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL', 
        f'sqlite:///{os.path.abspath(_default_db_path)}'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Firebase configuration
    FIREBASE_CREDENTIALS_PATH = os.environ.get('FIREBASE_CREDENTIALS_PATH', '')
    FIREBASE_PROJECT_ID = os.environ.get('FIREBASE_PROJECT_ID', '')
    
    # Scheduler configuration
    SCHEDULER_INTERVAL_SECONDS = int(os.environ.get('SCHEDULER_INTERVAL_SECONDS', '15'))
    SCHEDULER_MAX_WORKERS = int(os.environ.get('SCHEDULER_MAX_WORKERS', '20'))
    
    # ThingSpeak configuration
    THINGSPEAK_BASE_URL = "https://api.thingspeak.com"
    THINGSPEAK_TIMEOUT = 30
    
    # EMQX MQTT Configuration (defaults)
    EMQX_DEFAULT_PORT = int(os.environ.get('EMQX_DEFAULT_PORT', '1883'))
    EMQX_KEEPALIVE = int(os.environ.get('EMQX_KEEPALIVE', '60'))
    EMQX_RECONNECT_DELAY = int(os.environ.get('EMQX_RECONNECT_DELAY', '5'))  # seconds
    EMQX_MESSAGE_BUFFER_SIZE = int(os.environ.get('EMQX_MESSAGE_BUFFER_SIZE', '100'))  # max messages per device
    
    # CTOP configuration
    CTOP_TIMEOUT = 30
    CTOP_MAX_RETRIES = 3
    CTOP_RETRY_DELAY = 2  # seconds
    
    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    
    # Application settings
    MAX_RESULTS_PER_PAGE = 100

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False

class TestingConfig(Config):
    """Testing configuration"""
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SCHEDULER_INTERVAL_SECONDS = 60

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
