"""
Regression test for POST /data/fetch-all (routes/data_routes.py), legacy
non-Firebase/SQLAlchemy branch.

Before this fix, the EMQX path in this branch did:

    from services import EMQXService
    emqx_svc = EMQXService()
    success, raw_data, error = emqx_svc.fetch_data(device.id)

EMQXService is a plain class, not a singleton — a fresh instance has empty
_clients/_message_buffers of its own, completely disconnected from the real
EMQXService singleton (utils.scheduler_firestore.emqx_service) that actually
owns the live MQTT client and receives messages via on_message. So this
endpoint could never see any data a live MQTT subscription had buffered; it
would always report "No active MQTT subscription for this device".

The fix routes through the real singleton instead. This test proves that by
mocking utils.scheduler_firestore.emqx_service and asserting fetch_data() is
called on that mock — if the code regressed to constructing a fresh
EMQXService(), the mock would never be invoked and the assertion would fail.

Uses the app/db_session fixtures from tests/conftest.py (real SQLAlchemy
models against an in-memory SQLite DB) — this endpoint's non-Firebase branch
is pure ORM + service calls, no live Firestore/scheduler needed.
"""

import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import db, Device
from routes.data_routes import data_bp
import utils.scheduler_firestore as scheduler_mod


@pytest.fixture
def client(app):
    app.register_blueprint(data_bp)
    return app.test_client()


def _make_emqx_device():
    device = Device(
        name='Tank 1',
        channel_id='emqx',
        api_key='not_used_emqx',
        ctop_url_1='https://ctop.example.com/api/nodes/1',
        auth_token='dummy-auth-token',
        device_type='EvaraTank',
        data_source='emqx',
        emqx_broker_url='broker.example.com',
        emqx_port=1883,
        emqx_topic='evara/tank/1/data',
        is_active=True,
    )
    db.session.add(device)
    db.session.commit()
    return device


def test_fetch_all_routes_emqx_through_real_singleton(app, client):
    with app.app_context():
        device = _make_emqx_device()
        device_id = device.id

    mock_emqx = mock.MagicMock()
    mock_emqx.fetch_data.return_value = (True, {"channel": {}, "feeds": []}, None)

    with mock.patch.object(scheduler_mod, 'emqx_service', mock_emqx), \
         mock.patch.dict(os.environ, {'USE_FIREBASE': 'false'}):
        resp = client.post('/data/fetch-all')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True

    # The core assertion: fetch_data() must have been called on the real
    # singleton (our mock stand-in for it), not on some disconnected
    # throwaway EMQXService() instance.
    mock_emqx.fetch_data.assert_called_once()
    called_device_id, called_device_data = mock_emqx.fetch_data.call_args[0]
    assert called_device_id == device_id
    # device_data passed through must carry real EMQX credentials/config,
    # not just a bare device_id — fetch_data() needs it to auto-subscribe.
    assert called_device_data['emqx_broker_url'] == 'broker.example.com'
    assert called_device_data['emqx_topic'] == 'evara/tank/1/data'

    result_entry = data['data']['details'][str(device_id)]
    assert result_entry['success'] is True
