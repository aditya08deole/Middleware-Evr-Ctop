"""
Regression tests for routes/device_routes.py (the legacy non-Firebase
device CRUD blueprint):

  - add_device() previously skipped validate_device_data entirely, never
    encrypted api_key/auth_token/emqx_password, and never read/wrote any
    emqx_* field at all — an EMQX device created through this stack was
    silently persisted as data_source='thingspeak' with every emqx_* column
    left at its default.
  - update_device() had the same gaps for emqx_* fields specifically.

Uses the app/db_session fixtures from tests/conftest.py (real SQLAlchemy
models against an in-memory SQLite DB).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import db, Device
from routes.device_routes import device_bp
from utils.encryption import get_encryption_service


@pytest.fixture
def client(app):
    app.register_blueprint(device_bp)
    return app.test_client()


EMQX_PAYLOAD = {
    "name": "Deep Probe 1",
    "device_type": "EvaraDeep",
    "ctop_url_1": "https://ctop.example.com/nodes/1",
    "auth_token": "super-secret-token",
    "distance_field": "field1",
    "data_source": "emqx",
    "emqx_broker_url": "broker.example.com",
    "emqx_port": 1883,
    "emqx_username": "device-user",
    "emqx_password": "plaintext-mqtt-password",
    "emqx_topic": "evara/deep/1/data",
    "emqx_use_tls": False,
    "emqx_qos": 2,
}


def test_add_device_persists_emqx_fields(app, client):
    resp = client.post("/devices/", json=EMQX_PAYLOAD)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["success"] is True

    with app.app_context():
        device = db.session.get(Device, body["data"]["id"])
        assert device.data_source == "emqx"
        assert device.emqx_broker_url == "broker.example.com"
        assert device.emqx_topic == "evara/deep/1/data"
        assert device.emqx_qos == 2


def test_add_device_encrypts_sensitive_fields(app, client):
    resp = client.post("/devices/", json=EMQX_PAYLOAD)
    assert resp.status_code == 201
    device_id = resp.get_json()["data"]["id"]

    encryption_service = get_encryption_service()

    with app.app_context():
        device = db.session.get(Device, device_id)
        # Stored value must NOT be the plaintext password/token — and must
        # decrypt back to exactly what was submitted.
        assert device.emqx_password != "plaintext-mqtt-password"
        assert (
            encryption_service.decrypt(device.emqx_password)
            == "plaintext-mqtt-password"
        )

        assert device.auth_token != "super-secret-token"
        assert encryption_service.decrypt(device.auth_token) == "super-secret-token"


def test_add_device_missing_required_emqx_field_rejected(app, client):
    payload = dict(EMQX_PAYLOAD)
    del payload["emqx_topic"]

    resp = client.post("/devices/", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_add_device_invalid_qos_rejected(app, client):
    payload = dict(EMQX_PAYLOAD)
    payload["emqx_qos"] = 7

    resp = client.post("/devices/", json=payload)
    assert resp.status_code == 400


def test_update_device_persists_new_emqx_fields(app, client):
    resp = client.post("/devices/", json=EMQX_PAYLOAD)
    device_id = resp.get_json()["data"]["id"]

    update_resp = client.put(
        f"/devices/{device_id}",
        json={
            "emqx_broker_url": "new-broker.example.com",
            "emqx_qos": 0,
            "emqx_tls_insecure": False,
        },
    )
    assert update_resp.status_code == 200

    with app.app_context():
        device = db.session.get(Device, device_id)
        assert device.emqx_broker_url == "new-broker.example.com"
        assert device.emqx_qos == 0


def test_update_device_encrypts_new_auth_token_and_api_key(app, client):
    """Regression test: update_device() originally encrypted emqx_password
    on change but stored a changed api_key/auth_token in plaintext,
    silently overwriting the ciphertext add_device() had stored."""
    resp = client.post("/devices/", json=EMQX_PAYLOAD)
    device_id = resp.get_json()["data"]["id"]

    encryption_service = get_encryption_service()

    update_resp = client.put(
        f"/devices/{device_id}",
        json={
            "auth_token": "brand-new-auth-token",
            "api_key": "brand-new-api-key",
        },
    )
    assert update_resp.status_code == 200

    with app.app_context():
        device = db.session.get(Device, device_id)
        assert device.auth_token != "brand-new-auth-token"
        assert encryption_service.decrypt(device.auth_token) == "brand-new-auth-token"
        assert device.api_key != "brand-new-api-key"
        assert encryption_service.decrypt(device.api_key) == "brand-new-api-key"
