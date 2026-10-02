"""
Regression test for routes/device_routes_firestore.py's _sync_device_data(),
used by the manual "Sync"/"Fetch" buttons and "Sync All Devices".

Before this fix, it unconditionally set last_status="success" and
send_success=True after attempting CTOP sends, regardless of whether any
of them actually succeeded — so clicking Sync during a real CTOP outage
(e.g. the 503 "Database service temporarily unavailable" responses seen
live) reported "success" to the user while the periodic scheduler
correctly showed "error" for the same device moments later. Mirrors the
equivalent fix already covered for both schedulers in test_ctop_backoff.py.
"""

import importlib
from unittest.mock import MagicMock, patch

dr = importlib.import_module("routes.device_routes_firestore")


def _device_data(**overrides):
    base = {
        "id": "dev1",
        "name": "Test Tank",
        "data_source": "thingspeak",
        "last_processed_entry_id": "0",
    }
    base.update(overrides)
    return base


def _fresh_feed(entry_id="1"):
    return {"entry_id": entry_id, "field1": "10", "field2": "20"}


def _run(device_data, send_result):
    mock_thingspeak = MagicMock()
    mock_thingspeak.fetch_data.return_value = (True, {"feeds": [_fresh_feed()]}, None)
    mock_preprocess = MagicMock()
    mock_preprocess.preprocess_data.return_value = (
        True,
        [{"entry_id": "1", "Level": 10.0}],
        None,
    )
    mock_preprocess.transform_to_ctop_format.return_value = [{"water_level": 10.0}]
    mock_ctop = MagicMock()
    mock_ctop.send_to_ctop.return_value = send_result
    mock_local_store = MagicMock()

    with patch.object(dr, "thingspeak_service", mock_thingspeak), patch.object(
        dr, "preprocess_service", mock_preprocess
    ), patch.object(dr, "ctop_service", mock_ctop), patch.object(
        dr, "local_device_store", mock_local_store
    ):
        result = dr._sync_device_data("dev1", device_data)

    return result, mock_local_store


def test_ctop_rejecting_every_payload_reports_failure():
    device_data = _device_data()
    (success, error, results), mock_local_store = _run(
        device_data,
        {
            "success": False,
            "error": 'HTTP 503: {"message":"Database service temporarily unavailable"}',
        },
    )

    assert success is False
    assert "503" in error

    # The device record must be left showing the real failure, not success.
    update_call = mock_local_store.update_entry_id.call_args
    assert update_call.kwargs["last_status"] == "error"
    mock_local_store.increment_device_stats.assert_called_once_with(
        "dev1", send_success=False
    )


def test_ctop_accepting_the_payload_reports_success():
    device_data = _device_data()
    (success, error, results), mock_local_store = _run(
        device_data, {"success": True, "error": None}
    )

    assert success is True
    assert error is None

    update_call = mock_local_store.update_entry_id.call_args
    assert update_call.kwargs["last_status"] == "success"
    mock_local_store.increment_device_stats.assert_called_once_with(
        "dev1", send_success=True
    )
