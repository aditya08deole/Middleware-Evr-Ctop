"""
Regression test for the EMQX head-of-line-block fix in process_device()
(utils/scheduler_firestore.py).

EMQX's message buffer always re-fetches the OLDEST pending message
(services/emqx_service.py: fetch_data/rollback_fetch). Rolling back on ANY
CTOP failure — the previous behavior — meant a single message that could
never succeed (e.g. CTOP permanently rejecting it as malformed with a 4xx)
blocked every later message behind it in that device's buffer forever,
since the exact same un-sendable payload just kept getting re-fetched and
re-failing. Confirmed live: EVT-EF-002 got stuck retrying one reading
missing a required CTOP field for minutes while newer readings queued up
behind it.

A permanent (4xx, not 429) CTOP failure must now be treated like a
success for buffer-advancement purposes (confirm_consumed, accepting the
loss of that one reading) so the buffer can move on to the next message.
A transient failure (5xx, timeout, connection error, 429) must still roll
back, since retrying the identical payload later can legitimately work.
"""

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sf = importlib.import_module("utils.scheduler_firestore")


def _fresh_feed(entry_id="1792068187"):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {"entry_id": entry_id, "created_at": ts, "flow_rate": "0.0"}


def _emqx_device_data(**overrides):
    base = {
        "id": "dev1",
        "name": "EVT-EF-002",
        "data_source": "emqx",
        "is_active": True,
        "last_processed_entry_id": "0",
        "consecutive_failures": 0,
        "last_ctop_attempt_time": None,
    }
    base.update(overrides)
    return base


def _run(send_result):
    mock_emqx = MagicMock()
    mock_emqx.fetch_data.return_value = (True, {"feeds": [_fresh_feed()]}, None)
    mock_emqx.is_connected.return_value = True

    mock_preprocess = MagicMock()
    mock_preprocess.preprocess_data.return_value = (
        True,
        [{"entry_id": "1792068187", "FlowRate": 0.0}],
        None,
    )
    mock_preprocess.transform_to_ctop_format.return_value = [{"flow_rate": 0.0}]

    mock_ctop = MagicMock()
    mock_ctop.send_to_ctop.return_value = send_result

    mock_local_store = MagicMock()

    with patch.object(sf, "emqx_service", mock_emqx), patch.object(
        sf, "preprocess_service", mock_preprocess
    ), patch.object(sf, "ctop_service", mock_ctop), patch.object(
        sf, "local_device_store", mock_local_store
    ):
        sf.process_device("dev1", _emqx_device_data())

    return mock_emqx


def test_permanent_4xx_failure_confirms_instead_of_rolling_back():
    mock_emqx = _run(
        {
            "success": False,
            "error": 'HTTP 400: {"error":"Bad Request","message":"Missing parameter water_consumption"}',
        }
    )

    mock_emqx.confirm_consumed.assert_called_once_with("dev1")
    mock_emqx.rollback_fetch.assert_not_called()


def test_transient_5xx_failure_still_rolls_back():
    mock_emqx = _run(
        {"success": False, "error": "HTTP 503: Service Unavailable"}
    )

    mock_emqx.rollback_fetch.assert_called_once_with("dev1")
    mock_emqx.confirm_consumed.assert_not_called()


def test_rate_limited_429_still_rolls_back():
    # 429 is explicitly excluded from "permanent" — rate limiting is a
    # reason to slow down and retry, not drop the reading.
    mock_emqx = _run(
        {"success": False, "error": "HTTP 429: Too Many Requests"}
    )

    mock_emqx.rollback_fetch.assert_called_once_with("dev1")
    mock_emqx.confirm_consumed.assert_not_called()


def test_connection_error_still_rolls_back():
    mock_emqx = _run({"success": False, "error": "Connection error"})

    mock_emqx.rollback_fetch.assert_called_once_with("dev1")
    mock_emqx.confirm_consumed.assert_not_called()


def test_success_still_confirms():
    mock_emqx = _run({"success": True, "error": None})

    mock_emqx.confirm_consumed.assert_called_once_with("dev1")
    mock_emqx.rollback_fetch.assert_not_called()
