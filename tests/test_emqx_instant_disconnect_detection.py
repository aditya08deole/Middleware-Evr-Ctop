"""
Regression tests for instant EMQX disconnect detection in process_device()
(both utils/scheduler_firestore.py and utils/scheduler.py).

Before this change, an EMQX device whose MQTT subscriber client had
actually dropped its connection to the broker (bad credentials, network
blip, broker restart) looked identical to "connected, just no new message
yet" — the only thing that would eventually catch it was the 30-minute
staleness heuristic inferring a problem from the absence of messages. This
wires EMQXService.is_connected() (already tracked, from an earlier
session's fix) directly into device status, so a broker-side disconnect
is visible the very next tick instead of up to 30 minutes later.

The check only applies when a tick's fetch returned no new feeds — if
data actually came in, the connection obviously was working, so real
data must never be discarded just because is_connected() flips a moment
later.
"""

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import db, Device

sf = importlib.import_module('utils.scheduler_firestore')
sqlite_scheduler = importlib.import_module('utils.scheduler')


# ---------------------------------------------------------------------------
# Firestore-path scheduler
# ---------------------------------------------------------------------------

def _emqx_device_data(**overrides):
    base = {
        'id': 'dev1',
        'name': 'Test Device',
        'data_source': 'emqx',
        'is_active': True,
        'last_processed_entry_id': '0',
        'last_reading_time': None,
    }
    base.update(overrides)
    return base


class TestFirestoreInstantDisconnectDetection:
    def _run(self, device_data, is_connected):
        mock_emqx = MagicMock()
        mock_emqx.fetch_data.return_value = (True, {'feeds': []}, None)
        mock_emqx.is_connected.return_value = is_connected
        mock_local_store = MagicMock()

        with patch.object(sf, 'emqx_service', mock_emqx), \
             patch.object(sf, 'local_device_store', mock_local_store):
            sf.process_device('dev1', device_data)

        return mock_emqx, mock_local_store

    def test_disconnected_client_marks_inactive_immediately(self):
        # No stored last_reading_time at all — the pre-existing staleness
        # fallback would ALSO eventually mark this inactive, but only via
        # a different code path with a different (generic) error message.
        # The disconnected check must fire first, with a specific message.
        device_data = _emqx_device_data()
        mock_emqx, mock_local_store = self._run(device_data, is_connected=False)

        mock_local_store.update_device_fields.assert_called_once()
        updates = mock_local_store.update_device_fields.call_args.args[1]
        assert updates['last_status'] == 'inactive'
        assert updates['last_error'] == 'MQTT client is not connected to the broker'

    def test_disconnected_client_fires_even_with_fresh_stored_reading(self):
        """The whole point: don't wait for staleness to catch up. Even a
        last_reading_time from seconds ago must not stop the disconnect
        from being reported immediately."""
        import datetime as dt
        fresh = dt.datetime.now(dt.timezone.utc).isoformat()
        device_data = _emqx_device_data(last_reading_time=fresh)
        mock_emqx, mock_local_store = self._run(device_data, is_connected=False)

        updates = mock_local_store.update_device_fields.call_args.args[1]
        assert updates['last_status'] == 'inactive'
        assert updates['last_error'] == 'MQTT client is not connected to the broker'

    def test_connected_client_does_not_trigger_disconnect_path(self):
        device_data = _emqx_device_data()
        mock_emqx, mock_local_store = self._run(device_data, is_connected=True)

        # is_connected() was checked, but since it's True, we fall through
        # to the pre-existing "no prior reading" staleness branch instead —
        # which also marks inactive, but WITHOUT the specific disconnect
        # error message (proves the two code paths are genuinely distinct).
        mock_emqx.is_connected.assert_called_once()
        updates = mock_local_store.update_device_fields.call_args.args[1]
        assert updates.get('last_error') != 'MQTT client is not connected to the broker'

    def test_thingspeak_devices_never_check_emqx_connection_state(self):
        device_data = _emqx_device_data(data_source='thingspeak')
        mock_thingspeak = MagicMock()
        mock_thingspeak.fetch_data.return_value = (True, {'feeds': []}, None)
        mock_emqx = MagicMock()
        mock_local_store = MagicMock()

        with patch.object(sf, 'thingspeak_service', mock_thingspeak), \
             patch.object(sf, 'emqx_service', mock_emqx), \
             patch.object(sf, 'local_device_store', mock_local_store):
            sf.process_device('dev1', device_data)

        mock_emqx.is_connected.assert_not_called()


# ---------------------------------------------------------------------------
# Legacy SQLAlchemy-path scheduler
# ---------------------------------------------------------------------------

def _make_emqx_device(**overrides):
    base = dict(
        name='EMQX Device',
        channel_id='emqx',
        api_key='not_used_emqx',
        ctop_url_1='https://ctop.example.com/x',
        auth_token='tok-12345',
        device_type='EvaraDeep',
        distance_field='field1',
        data_source='emqx',
        emqx_broker_url='broker.example.com',
        emqx_topic='evara/deep/1/data',
        is_active=True,
    )
    base.update(overrides)
    device = Device(**base)
    db.session.add(device)
    db.session.commit()
    return device


class TestSqliteInstantDisconnectDetection:
    def _run(self, app, device_id, is_connected):
        mock_emqx = MagicMock()
        mock_emqx.fetch_data.return_value = (True, {'feeds': []}, None)
        mock_emqx.is_connected.return_value = is_connected

        with app.app_context(), patch.object(sqlite_scheduler, 'emqx_service', mock_emqx):
            sqlite_scheduler.process_device(device_id)

        return mock_emqx

    def test_disconnected_client_marks_inactive_with_specific_error(self, app):
        with app.app_context():
            device = _make_emqx_device()
            device_id = device.id

        self._run(app, device_id, is_connected=False)

        with app.app_context():
            refreshed = db.session.get(Device, device_id)
            assert refreshed.last_status == 'inactive'
            assert refreshed.last_error == 'MQTT client is not connected to the broker'

    def test_connected_client_is_not_marked_disconnected(self, app):
        with app.app_context():
            device = _make_emqx_device()
            device_id = device.id

        mock_emqx = self._run(app, device_id, is_connected=True)
        mock_emqx.is_connected.assert_called_once()

        with app.app_context():
            refreshed = db.session.get(Device, device_id)
            assert refreshed.last_error != 'MQTT client is not connected to the broker'
