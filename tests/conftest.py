"""
Shared pytest fixtures for the SQLite-model test suite.

`app` builds a minimal Flask app bound to an in-memory SQLite database via
the project's real SQLAlchemy models — deliberately NOT the full
app.py:create_app() factory, which also starts the live scheduler, syncs
from real Firestore, and spins up EMQX MQTT clients. Tests using these
fixtures (device separation, filtering, duplicate detection, temperature
compensation) exercise pure preprocessing/ORM logic and have no business
touching any of that.
"""

import os
import tempfile

# Point the encryption service at a throwaway key file BEFORE any test
# imports it. Without this, running the suite in a directory that has no
# encryption.key makes utils/encryption.py generate one and write it to the
# project root (it only refuses to do that when FLASK_ENV=production).
#
# That is genuinely dangerous in a deployment directory: a freshly generated
# key is a VALID 44-character Fernet key, so it passes every "is the key
# present and well-formed" check while being unable to decrypt a single
# stored credential. It silently converts "the key is missing", which
# scripts/preflight.sh is built to catch and block on, into "the key is
# fine" — turning a loud, fixable error into permanent data loss.
#
# Set before the imports below, which is why these two lines sit above them
# and E402 is suppressed for tests in pyproject.toml.
os.environ.setdefault(
    "ENCRYPTION_KEY_FILE_PATH",
    os.path.join(tempfile.gettempdir(), "ctop-test-encryption.key"),
)

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

from models import db  # noqa: E402


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    flask_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    flask_app.config["TESTING"] = True

    db.init_app(flask_app)
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def db_session(app):
    with app.app_context():
        yield db.session
        db.session.rollback()
