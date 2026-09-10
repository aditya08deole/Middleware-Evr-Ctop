"""
Regression test for the streaming (cross-call) median/average filter in
services/preprocess_service.py.

Before this fix, filtering was recomputed from scratch on every
preprocess_data() call using only that call's own feeds:
  window = min(device.filter_window or 5, len(distance_values))
Both ThingSpeak (results=1 per fetch) and EMQX (~1 buffered message per
instant-trigger) typically deliver exactly ONE new reading per call, so
`window` collapsed to 1 and the configured filter had no smoothing effect
at all — a device configured for "average, window 3" behaved identically
to "no filtering".

_apply_streaming_filter() fixes this by keeping a rolling per-device
history across calls, so three consecutive single-reading polls actually
get averaged together.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.preprocess_service import PreprocessService


def _emqx_style_device_data(**overrides):
    base = {
        "name": "Deep Probe 1",
        "device_type": "EvaraDeep",
        "distance_field": "field1",
        "filtering_method": "average",
        "filter_window": 3,
        "ctop_url_1": "https://ctop.example.com/x",
        "auth_token": "tok-12345",
    }
    base.update(overrides)
    return base


def _feed(entry_id, value):
    return {
        "entry_id": entry_id,
        "created_at": f"2026-01-01T00:0{entry_id}:00Z",
        "field1": str(value),
    }


def test_average_filter_smooths_across_separate_calls():
    """Three separate single-reading polls (10, 20, 30) with a window-3
    average filter should produce a running average (10, 15, 20) — NOT the
    raw values passed straight through, which is what the bug produced."""
    service = PreprocessService()
    device_data = _emqx_style_device_data()
    device_id = "streaming-test-device-1"

    _, entries1, _ = service.preprocess_data(
        device_id, {"feeds": [_feed(1, 10)]}, device_data=device_data
    )
    _, entries2, _ = service.preprocess_data(
        device_id, {"feeds": [_feed(2, 20)]}, device_data=device_data
    )
    _, entries3, _ = service.preprocess_data(
        device_id, {"feeds": [_feed(3, 30)]}, device_data=device_data
    )

    assert entries1[0]["Distance"] == 10.0
    assert entries2[0]["Distance"] == 15.0  # avg(10, 20)
    assert entries3[0]["Distance"] == 20.0  # avg(10, 20, 30) — the regression case:
    # the pre-fix code would have returned 30.0 here (window collapsed to 1
    # because each call only ever saw a single feed).


def test_median_filter_smooths_across_separate_calls():
    service = PreprocessService()
    device_data = _emqx_style_device_data(filtering_method="median")
    device_id = "streaming-test-device-2"

    for i, value in enumerate([10, 100, 20], start=1):
        _, entries, _ = service.preprocess_data(
            device_id, {"feeds": [_feed(i, value)]}, device_data=device_data
        )

    # History after all three calls: [10, 100, 20] -> median = 20
    assert entries[0]["Distance"] == 20.0


def test_filter_history_is_isolated_per_device():
    """Two different devices filtering the same field name must not share
    history — device_id is part of the history key."""
    service = PreprocessService()
    device_data = _emqx_style_device_data()

    service.preprocess_data(
        "device-a", {"feeds": [_feed(1, 1000)]}, device_data=device_data
    )
    _, entries_b, _ = service.preprocess_data(
        "device-b", {"feeds": [_feed(1, 5)]}, device_data=device_data
    )

    # device-b's first-ever reading must be exactly 5, unaffected by
    # device-a's unrelated history.
    assert entries_b[0]["Distance"] == 5.0


def test_no_filtering_configured_passes_raw_values_through():
    service = PreprocessService()
    device_data = _emqx_style_device_data(filtering_method="none")
    device_id = "streaming-test-device-none"

    for i, value in enumerate([10, 20, 30], start=1):
        _, entries, _ = service.preprocess_data(
            device_id, {"feeds": [_feed(i, value)]}, device_data=device_data
        )

    assert entries[0]["Distance"] == 30.0
