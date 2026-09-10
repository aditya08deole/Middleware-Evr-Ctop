"""
Regression tests for utils/scheduler.py (the legacy non-Firebase/SQLAlchemy
scheduler) EMQX support.

Before this fix, process_device() in this module unconditionally called
thingspeak_service.fetch_data(device_id) regardless of device.data_source —
routes/device_routes.py's add_device() also never persisted any emqx_*
field, so an EMQX-configured device on this stack (the default when
USE_FIREBASE is unset) silently polled ThingSpeak forever with
channel_id='emqx' and never worked, with no error surfaced anywhere.

Uses the app/db_session fixtures from tests/conftest.py (real SQLAlchemy
models against an in-memory SQLite DB).
"""

import importlib
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import db, Device

# NOT `import utils.scheduler as scheduler_module` — utils/__init__.py does
# `from .scheduler import scheduler, init_scheduler`, which rebinds the
# name `scheduler` in the `utils` package namespace to the
# BackgroundScheduler *instance*. `import utils.scheduler as x` resolves
# via that same rebound package attribute and silently returns the
# scheduler instance instead of the module (see the identical fix + its
# explanation in routes/device_routes.py's _get_emqx_service()).
# importlib.import_module() looks up sys.modules directly instead, which
# is unaffected by the rebinding.
scheduler_module = importlib.import_module('utils.scheduler')


def _make_device(**overrides):
    base = dict(
        name='Deep Probe',
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


def test_process_device_routes_emqx_devices_through_emqx_service(app):
    with app.app_context():
        device = _make_device()
        device_id = device.id

    mock_emqx = mock.MagicMock()
    mock_emqx.fetch_data.return_value = (True, {"channel": {}, "feeds": []}, None)
    mock_thingspeak = mock.MagicMock()
    mock_preprocess = mock.MagicMock()
    mock_ctop = mock.MagicMock()

    with app.app_context(), \
         mock.patch.object(scheduler_module, 'emqx_service', mock_emqx), \
         mock.patch.object(scheduler_module, 'thingspeak_service', mock_thingspeak), \
         mock.patch.object(scheduler_module, 'preprocess_service', mock_preprocess), \
         mock.patch.object(scheduler_module, 'ctop_service', mock_ctop):
        scheduler_module.process_device(device_id)

    mock_emqx.fetch_data.assert_called_once()
    called_device_id, called_device_data = mock_emqx.fetch_data.call_args[0]
    assert called_device_id == device_id
    assert called_device_data['emqx_broker_url'] == 'broker.example.com'
    mock_thingspeak.fetch_data.assert_not_called()


def test_process_device_still_routes_thingspeak_devices_normally(app):
    with app.app_context():
        device = _make_device(
            data_source='thingspeak',
            channel_id='123456',
            api_key='real-api-key-here',
            emqx_broker_url=None,
            emqx_topic=None,
        )
        device_id = device.id

    mock_emqx = mock.MagicMock()
    mock_thingspeak = mock.MagicMock()
    mock_thingspeak.fetch_data.return_value = (True, {"channel": {}, "feeds": []}, None)
    mock_preprocess = mock.MagicMock()
    mock_ctop = mock.MagicMock()

    with app.app_context(), \
         mock.patch.object(scheduler_module, 'emqx_service', mock_emqx), \
         mock.patch.object(scheduler_module, 'thingspeak_service', mock_thingspeak), \
         mock.patch.object(scheduler_module, 'preprocess_service', mock_preprocess), \
         mock.patch.object(scheduler_module, 'ctop_service', mock_ctop):
        scheduler_module.process_device(device_id)

    mock_thingspeak.fetch_data.assert_called_once_with(device_id)
    mock_emqx.fetch_data.assert_not_called()


def test_process_device_safe_skips_concurrent_call_for_same_device(app):
    with app.app_context():
        device = _make_device(data_source='thingspeak', emqx_broker_url=None, emqx_topic=None)
        device_id = device.id

    lock = scheduler_module._get_device_lock(device_id)
    lock.acquire()  # simulate an in-flight run for this device
    try:
        with mock.patch.object(scheduler_module, '_app', app):
            # Must return immediately without touching db.session (no app
            # context pushed here on purpose) — proves the lock short-circuits
            # process_device() before it would ever need one.
            scheduler_module.process_device_safe(device_id)
    finally:
        lock.release()


def test_process_device_safe_pushes_app_context_for_bare_thread_call(app):
    """The EMQX instant-trigger fires process_device_safe() from a raw
    background thread with no Flask app context of its own — this must not
    raise 'Working outside of application context'."""
    with app.app_context():
        device = _make_device(data_source='thingspeak', emqx_broker_url=None, emqx_topic=None)
        device_id = device.id

    mock_thingspeak = mock.MagicMock()
    mock_thingspeak.fetch_data.return_value = (True, {"channel": {}, "feeds": []}, None)

    with mock.patch.object(scheduler_module, '_app', app), \
         mock.patch.object(scheduler_module, 'thingspeak_service', mock_thingspeak), \
         mock.patch.object(scheduler_module, 'preprocess_service', mock.MagicMock()), \
         mock.patch.object(scheduler_module, 'ctop_service', mock.MagicMock()), \
         mock.patch.object(scheduler_module, 'emqx_service', mock.MagicMock()):
        # Deliberately NOT wrapped in `with app.app_context()` — simulates
        # the raw background thread the EMQX on-message callback runs in.
        scheduler_module.process_device_safe(device_id)

    mock_thingspeak.fetch_data.assert_called_once()
