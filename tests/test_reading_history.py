"""
Regression tests for utils/reading_history.py — the trend sparkline's
in-memory reading store.

Caught via live testing against the real running app (not from unit
tests, which only exercised a single tick): a device with failing CTOP
sends gets the same "new" entry re-processed by process_device() on every
scheduler tick, because entry_id deliberately doesn't advance until a send
succeeds (so it can keep retrying). Recording history keyed only on
"is this entry eligible for a CTOP retry" duplicated the exact same
physical reading into the sparkline on every 15-second tick — a device
stuck retrying for an hour would show ~240 identical points instead of
one. record() must dedupe by entry_id so only genuinely new readings
produce a new sparkline point.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.reading_history import ReadingHistoryStore


def test_repeated_entry_id_is_recorded_once():
    store = ReadingHistoryStore()

    # Simulate the same failing-CTOP-send entry being reprocessed on three
    # consecutive scheduler ticks — exactly what was observed live.
    store.record("dev1", "1001", "2026-09-10T09:50:59+00:00", {"flow_rate": 0.0})
    store.record("dev1", "1001", "2026-09-10T09:51:14+00:00", {"flow_rate": 0.0})
    store.record("dev1", "1001", "2026-09-10T09:51:29+00:00", {"flow_rate": 0.0})

    history = store.get_history("dev1")
    assert len(history) == 1


def test_new_entry_id_is_recorded_as_a_new_point():
    store = ReadingHistoryStore()

    store.record("dev1", "1001", "2026-09-10T09:50:59+00:00", {"water_level": 50.0})
    store.record(
        "dev1", "1001", "2026-09-10T09:51:14+00:00", {"water_level": 50.0}
    )  # retry, same entry
    store.record(
        "dev1", "1002", "2026-09-10T09:52:21+00:00", {"water_level": 51.6}
    )  # genuinely new

    history = store.get_history("dev1")
    assert len(history) == 2
    assert history[0]["water_level"] == 50.0
    assert history[1]["water_level"] == 51.6


def test_history_is_isolated_per_device():
    store = ReadingHistoryStore()

    store.record("dev-a", "1", "2026-09-10T09:50:00+00:00", {"x": 1})
    store.record("dev-b", "1", "2026-09-10T09:50:00+00:00", {"x": 2})

    assert len(store.get_history("dev-a")) == 1
    assert len(store.get_history("dev-b")) == 1
    assert store.get_history("dev-a")[0]["x"] == 1
    assert store.get_history("dev-b")[0]["x"] == 2


def test_history_is_bounded_per_device():
    store = ReadingHistoryStore()
    for i in range(store.MAX_POINTS_PER_DEVICE + 10):
        store.record("dev1", str(i), f"2026-09-10T09:{i:02d}:00+00:00", {"x": i})

    history = store.get_history("dev1")
    assert len(history) == store.MAX_POINTS_PER_DEVICE
    # Oldest points evicted first — the store should hold the most recent ones.
    assert history[-1]["x"] == store.MAX_POINTS_PER_DEVICE + 9


def test_non_numeric_values_are_dropped_and_empty_reading_not_recorded():
    store = ReadingHistoryStore()
    store.record(
        "dev1", "1", "2026-09-10T09:50:00+00:00", {"status": "ok", "note": None}
    )
    assert store.get_history("dev1") == []

    store.record(
        "dev1", "2", "2026-09-10T09:51:00+00:00", {"status": "ok", "water_level": 12.5}
    )
    history = store.get_history("dev1")
    assert len(history) == 1
    assert "status" not in history[0]
    assert history[0]["water_level"] == 12.5


def test_none_entry_id_never_dedupes():
    """entry_id=None is documented to skip dedup entirely — used by
    callers that don't have (or don't want) entry-level tracking."""
    store = ReadingHistoryStore()
    store.record("dev1", None, "2026-09-10T09:50:00+00:00", {"x": 1})
    store.record("dev1", None, "2026-09-10T09:50:01+00:00", {"x": 1})

    assert len(store.get_history("dev1")) == 2
