import os
import base64
import warnings


class Config:
    """Base configuration class"""

    # SECURITY: Secret key should be set via environment variable in production.
    # For local development, generate a temporary key and print a warning.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    _env = os.environ.get("FLASK_ENV", os.environ.get("ENV", "development"))

    if not SECRET_KEY:
        if _env == "production":
            raise ValueError(
                "SECRET_KEY environment variable must be set in production"
            )
        # Development fallback: generate a random key and warn
        warnings.warn(
            "SECRET_KEY not set — generating a temporary secret for development. "
            "Do NOT use this in production. Set SECRET_KEY env var before deployment.",
            UserWarning,
        )
        SECRET_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()

    # Database configuration (SQLite for fallback, Firestore preferred)
    USE_FIREBASE = os.environ.get("USE_FIREBASE", "false").lower() == "true"

    _base_dir = os.path.abspath(os.path.dirname(__file__))
    _instance_dir = os.path.join(_base_dir, "instance")

    # Ensure instance directory exists for local development
    if not os.path.exists(_instance_dir):
        try:
            os.makedirs(_instance_dir)
        except Exception:
            pass

    _default_db_path = os.path.join(_instance_dir, "ctop_iot.db")

    # Use absolute path for SQLite to avoid ambiguity in Docker
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.abspath(_default_db_path)}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Firebase configuration
    FIREBASE_CREDENTIALS_PATH = os.environ.get("FIREBASE_CREDENTIALS_PATH", "")
    FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")

    # Scheduler configuration
    SCHEDULER_INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "15"))
    SCHEDULER_MAX_WORKERS = int(os.environ.get("SCHEDULER_MAX_WORKERS", "20"))

    # ThingSpeak configuration
    THINGSPEAK_BASE_URL = "https://api.thingspeak.com"
    THINGSPEAK_TIMEOUT = 30

    # EMQX MQTT Configuration (defaults)
    EMQX_DEFAULT_PORT = int(os.environ.get("EMQX_DEFAULT_PORT", "1883"))
    EMQX_KEEPALIVE = int(os.environ.get("EMQX_KEEPALIVE", "60"))
    EMQX_RECONNECT_DELAY = int(os.environ.get("EMQX_RECONNECT_DELAY", "5"))  # seconds
    EMQX_MESSAGE_BUFFER_SIZE = int(
        os.environ.get("EMQX_MESSAGE_BUFFER_SIZE", "100")
    )  # max messages per device

    # Namespace for MQTT client IDs (see emqx_service.subscribe_device).
    # MQTT requires a client_id to be unique across the whole broker: if two
    # live connections share one, the broker closes the older session every
    # time the other reconnects, and the two instances kick each other off in
    # a loop (observed as "Unexpected disconnect (rc=128)" every few seconds
    # on every device, with telemetry gaps in between). Because client IDs
    # here are derived from the device ID, they are identical across
    # deployments — so a second instance (e.g. a developer running app.py
    # locally while the deployed one is up) collides with production on every
    # device at once. Override this to anything else (EMQX_CLIENT_ID_PREFIX=
    # local-dev) to run alongside the deployed instance without disturbing
    # it. The default is unchanged, so the deployed instance keeps the stable
    # client_id its clean_session=False session persistence depends on.
    EMQX_CLIENT_ID_PREFIX = os.environ.get("EMQX_CLIENT_ID_PREFIX", "ctop")

    # CTOP configuration
    CTOP_TIMEOUT = 30
    CTOP_MAX_RETRIES = 3
    CTOP_RETRY_DELAY = 2  # seconds

    # Logging
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

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
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SCHEDULER_INTERVAL_SECONDS = 60


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
