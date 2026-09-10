"""
Regression tests for the CTOP delivery health tracking added to
process_device() in both utils/scheduler_firestore.py (Firestore/local-
mirror path) and utils/scheduler.py (legacy SQLAlchemy path):

  - consecutive_failures resets to 0 on any successful CTOP send.
  - consecutive_failures increments on a fully-failed send, and
    needs_attention flips true once it crosses CTOP_ATTENTION_THRESHOLD.
  - Once a device has failed CTOP_BACKOFF_LEVEL_1+ times in a row, further
    send attempts are skipped (not retried every tick) until the backoff
    window has elapsed — this is what stops a dead CTOP endpoint from being
    hammered every 15 seconds forever, which is exactly what was observed
    happening live before this change.
  - Skipping a send during backoff must not lose data: entry_id isn't
    advanced, so the same reading is retried on the next eligible tick.
"""

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import db, Device


# ---------------------------------------------------------------------------
# Firestore-path scheduler (utils/scheduler_firestore.py)
# ---------------------------------------------------------------------------

sf = importlib.import_module('utils.scheduler_firestore')


def _fresh_feed(entry_id='1', minutes_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {'entry_id': entry_id, 'created_at': ts, 'field1': '10'}


def _base_device_data(**overrides):
    base = {
        'id': 'dev1',
        'name': 'Test Device',
        'data_source': 'thingspeak',
        'last_processed_entry_id': '0',
        'consecutive_failures': 0,
        'last_ctop_attempt_time': None,
        'is_active': True,
    }
    base.update(overrides)
    return base


class TestFirestoreSchedulerBackoff:
    def _run(self, device_data, send_result):
        mock_thingspeak = MagicMock()
        mock_thingspeak.fetch_data.return_value = (True, {'feeds': [_fresh_feed()]}, None)
        mock_preprocess = MagicMock()
        mock_preprocess.preprocess_data.return_value = (True, [{'entry_id': '1', 'Level': 10.0}], None)
        mock_preprocess.transform_to_ctop_format.return_value = [{'water_level': 10.0}]
        mock_ctop = MagicMock()
        mock_ctop.send_to_ctop.return_value = send_result
        mock_local_store = MagicMock()

        with patch.object(sf, 'thingspeak_service', mock_thingspeak), \
             patch.object(sf, 'preprocess_service', mock_preprocess), \
             patch.object(sf, 'ctop_service', mock_ctop), \
             patch.object(sf, 'local_device_store', mock_local_store):
            sf.process_device('dev1', device_data)

        return mock_ctop, mock_local_store

    def test_success_resets_consecutive_failures(self):
        device_data = _base_device_data(consecutive_failures=4)
        mock_ctop, mock_local_store = self._run(device_data, {'success': True, 'error': None})

        mock_ctop.send_to_ctop.assert_called_once()
        tracking_call = next(
            c for c in mock_local_store.update_device_fields.call_args_list
            if 'consecutive_failures' in c.args[1]
        )
        updates = tracking_call.args[1]
        assert updates['consecutive_failures'] == 0
        assert updates['needs_attention'] is False

    def test_failure_increments_and_sets_needs_attention_at_threshold(self):
        device_data = _base_device_data(consecutive_failures=4)  # one below threshold (5)
        mock_ctop, mock_local_store = self._run(device_data, {'success': False, 'error': 'boom'})

        tracking_call = next(
            c for c in mock_local_store.update_device_fields.call_args_list
            if 'consecutive_failures' in c.args[1]
        )
        updates = tracking_call.args[1]
        assert updates['consecutive_failures'] == 5
        assert updates['needs_attention'] is True

    def test_failure_below_threshold_does_not_set_needs_attention(self):
        device_data = _base_device_data(consecutive_failures=0)
        mock_ctop, mock_local_store = self._run(device_data, {'success': False, 'error': 'boom'})

        tracking_call = next(
            c for c in mock_local_store.update_device_fields.call_args_list
            if 'consecutive_failures' in c.args[1]
        )
        updates = tracking_call.args[1]
        assert updates['consecutive_failures'] == 1
        assert updates['needs_attention'] is False

    def test_backoff_skips_send_within_cooldown_window(self):
        recent_attempt = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        device_data = _base_device_data(consecutive_failures=10, last_ctop_attempt_time=recent_attempt)
        mock_ctop, mock_local_store = self._run(device_data, {'success': True, 'error': None})

        # Backoff (60s window for 5-19 consecutive failures) hasn't elapsed
        # (only 5s since last attempt) — send must be skipped entirely.
        mock_ctop.send_to_ctop.assert_not_called()

    def test_backoff_allows_send_after_cooldown_elapses(self):
        old_attempt = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        device_data = _base_device_data(consecutive_failures=10, last_ctop_attempt_time=old_attempt)
        mock_ctop, mock_local_store = self._run(device_data, {'success': True, 'error': None})

        # 90s > the 60s backoff window for 10 consecutive failures — should
        # attempt the send again.
        mock_ctop.send_to_ctop.assert_called_once()

    def test_low_failure_count_never_backs_off(self):
        """Below CTOP_BACKOFF_LEVEL_1, backoff must never engage even with
        a last_ctop_attempt_time a fraction of a second ago — a couple of
        failures could just be a transient blip and shouldn't delay retry."""
        recent_attempt = datetime.now(timezone.utc).isoformat()
        device_data = _base_device_data(consecutive_failures=2, last_ctop_attempt_time=recent_attempt)
        mock_ctop, mock_local_store = self._run(device_data, {'success': True, 'error': None})

        mock_ctop.send_to_ctop.assert_called_once()


# ---------------------------------------------------------------------------
# Legacy SQLAlchemy-path scheduler (utils/scheduler.py)
# ---------------------------------------------------------------------------

sqlite_scheduler = importlib.import_module('utils.scheduler')


def _make_thingspeak_device(app, **overrides):
    base = dict(
        name='Tank 1',
        channel_id='123456',
        api_key='real-api-key',
        ctop_url_1='https://ctop.example.com/x',
        auth_token='tok-12345',
        device_type='EvaraDeep',
        distance_field='field1',
        data_source='thingspeak',
        is_active=True,
    )
    base.update(overrides)
    device = Device(**base)
    db.session.add(device)
    db.session.commit()
    return device


class TestSqliteSchedulerBackoff:
    def _run(self, app, device_id, send_result):
        mock_thingspeak = MagicMock()
        mock_thingspeak.fetch_data.return_value = (True, {'feeds': [_fresh_feed()]}, None)
        mock_preprocess = MagicMock()
        mock_preprocess.preprocess_data.return_value = (True, [{'entry_id': '1', 'Distance': 10.0}], None)
        mock_preprocess.transform_to_ctop_format.return_value = [{'distance': 10.0}]
        mock_ctop = MagicMock()
        mock_ctop.send_to_ctop.return_value = send_result

        with app.app_context(), \
             patch.object(sqlite_scheduler, 'thingspeak_service', mock_thingspeak), \
             patch.object(sqlite_scheduler, 'preprocess_service', mock_preprocess), \
             patch.object(sqlite_scheduler, 'ctop_service', mock_ctop), \
             patch.object(sqlite_scheduler, 'emqx_service', MagicMock()):
            sqlite_scheduler.process_device(device_id)

        return mock_ctop

    def test_success_resets_consecutive_failures(self, app):
        with app.app_context():
            device = _make_thingspeak_device(app, consecutive_failures=4)
            device_id = device.id

        self._run(app, device_id, {'success': True, 'error': None})

        with app.app_context():
            refreshed = db.session.get(Device, device_id)
            assert refreshed.consecutive_failures == 0
            assert refreshed.needs_attention is False

    def test_failure_sets_needs_attention_at_threshold(self, app):
        with app.app_context():
            device = _make_thingspeak_device(app, consecutive_failures=4)
            device_id = device.id

        self._run(app, device_id, {'success': False, 'error': 'boom'})

        with app.app_context():
            refreshed = db.session.get(Device, device_id)
            assert refreshed.consecutive_failures == 5
            assert refreshed.needs_attention is True

    def test_backoff_skips_send_within_cooldown_window(self, app):
        with app.app_context():
            device = _make_thingspeak_device(
                app,
                consecutive_failures=10,
                last_ctop_attempt_time=datetime.utcnow() - timedelta(seconds=5),
            )
            device_id = device.id

        mock_ctop = self._run(app, device_id, {'success': True, 'error': None})
        mock_ctop.send_to_ctop.assert_not_called()

    def test_backoff_allows_send_after_cooldown_elapses(self, app):
        with app.app_context():
            device = _make_thingspeak_device(
                app,
                consecutive_failures=10,
                last_ctop_attempt_time=datetime.utcnow() - timedelta(seconds=90),
            )
            device_id = device.id

        mock_ctop = self._run(app, device_id, {'success': True, 'error': None})
        mock_ctop.send_to_ctop.assert_called_once()
