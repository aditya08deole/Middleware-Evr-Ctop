"""
Regression test for the EMQX field-mapping warning in
services/preprocess_service.py (_check_emqx_field_mapping).

Catches the exact EVT-EF-004 scenario: a device's *_field config still uses
a positional ThingSpeak-style name ('field2') while the device's actual MQTT
payload is named-key JSON ('flow_rate', 'total_liters', ...). EMQXService's
positional field1..field8 synthesis for named-key payloads is not a stable,
documented mapping (see services/emqx_service.py's _mqtt_message_to_feed) —
this warning exists so that mismatch shows up in logs instead of silently
producing a wrong/zeroed sensor value with no trace of why.
"""

import logging

from services.preprocess_service import PreprocessService


def _emqx_device_data(**overrides):
    base = {
        "id": "dev-ef-004",
        "name": "EVT-EF-004",
        "device_type": "EvaraFlow",
        "data_source": "emqx",
        "meter_reading_field": "field3",
        "flow_rate_field": "field2",
        "filtering_method": "none",
        "filter_window": 5,
    }
    base.update(overrides)
    return base


# Shaped like EMQXService._mqtt_message_to_feed()'s output for a named-key
# payload {"flow_rate": ..., "node_id": ..., "total_liters": ...}: raw named
# keys copied verbatim, plus a synthesized field1/field2/field3.
_NAMED_KEY_FEED = {
    "created_at": "2026-10-02T02:00:00Z",
    "entry_id": "1",
    "node_id": "EVT-EF-004",
    "total_liters": "1234.56",
    "flow_rate": "2.3",
    "field1": "2.3",
    "field2": "EVT-EF-004",
    "field3": "1234.56",
}


def test_warns_once_when_fieldn_config_meets_named_key_payload(caplog):
    service = PreprocessService()
    device_data = _emqx_device_data()

    with caplog.at_level(logging.WARNING):
        service.preprocess_data("dev-ef-004", {"feeds": [_NAMED_KEY_FEED]}, device_data=device_data)
        service.preprocess_data("dev-ef-004", {"feeds": [_NAMED_KEY_FEED]}, device_data=device_data)

    mapping_warnings = [
        r for r in caplog.records if "positional field-N names" in r.message
    ]
    assert len(mapping_warnings) == 1  # only once per device, not every tick
    assert "flow_rate_field=field2" in mapping_warnings[0].message
    assert "flow_rate" in mapping_warnings[0].message  # the real key is surfaced


def test_no_warning_for_genuine_thingspeak_style_payload(caplog):
    """A real ThingSpeak-format feed (field1..field8 only, no named keys) is
    exactly what fieldN config is for — must not warn."""
    service = PreprocessService()
    device_data = _emqx_device_data()
    thingspeak_style_feed = {
        "created_at": "2026-10-02T02:00:00Z",
        "entry_id": "1",
        "field2": "1.2",
        "field3": "100.5",
    }

    with caplog.at_level(logging.WARNING):
        service.preprocess_data(
            "dev-ef-004", {"feeds": [thingspeak_style_feed]}, device_data=device_data
        )

    assert not any("positional field-N names" in r.message for r in caplog.records)


def test_no_warning_when_field_config_already_uses_literal_key_name(caplog):
    """The recommended, deterministic config -- *_field set to the literal
    JSON key name -- must never trigger the guessed-mapping warning."""
    service = PreprocessService()
    device_data = _emqx_device_data(
        flow_rate_field="flow_rate", meter_reading_field="total_liters"
    )

    with caplog.at_level(logging.WARNING):
        service.preprocess_data(
            "dev-ef-004", {"feeds": [_NAMED_KEY_FEED]}, device_data=device_data
        )

    assert not any("positional field-N names" in r.message for r in caplog.records)
