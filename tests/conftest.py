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

import pytest
from flask import Flask
from models import db


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
