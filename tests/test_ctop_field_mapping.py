"""
Regression tests for services/preprocess_service.py's configurable CTOP
output field names (DEFAULT_CTOP_FIELD_MAP / set_ctop_field_map_overrides /
get_ctop_field_map).

transform_to_ctop_format() used to hardcode the outbound JSON key for every
device type (e.g. EvaraTank's Level -> "water_level") directly in Python —
changing it required editing code and redeploying. Settings → CTOP
Parameter Mapping now lets an operator override just the key names that
need to change (e.g. to match a different CTOP environment's schema)
without touching any device record or code, while anything not explicitly
overridden still falls back to the original hardcoded default.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.preprocess_service import (
    PreprocessService,
    DEFAULT_CTOP_FIELD_MAP,
    get_ctop_field_map,
    set_ctop_field_map_overrides,
)


class TestCTOPFieldMapping(unittest.TestCase):
    def setUp(self):
        self.service = PreprocessService()
        self.addCleanup(set_ctop_field_map_overrides, {})

    def test_defaults_match_original_hardcoded_keys_when_no_override_set(self):
        set_ctop_field_map_overrides({})
        merged = get_ctop_field_map()
        self.assertEqual(merged["EvaraTank"]["Level"], "water_level")
        self.assertEqual(merged["EvaraFlow"]["MeterReading"], "waterconsumption")

    def test_partial_override_only_replaces_the_overridden_key(self):
        """Overriding one field for one device type must not drop the
        other default mappings for that type or affect other types."""
        set_ctop_field_map_overrides({"EvaraTank": {"Level": "waterLevel"}})
        merged = get_ctop_field_map()

        self.assertEqual(merged["EvaraTank"]["Level"], "waterLevel")
        self.assertEqual(
            merged["EvaraTank"]["Temperature"], "temperature"
        )  # untouched default
        self.assertEqual(
            merged["EvaraFlow"]["FlowRate"], "flow_rate"
        )  # other type untouched

    def test_transform_to_ctop_format_uses_overridden_key(self):
        set_ctop_field_map_overrides({"EvaraTank": {"Level": "waterLevel"}})

        device_data = {
            "device_type": "EvaraTank",
            "name": "Tank 1",
            "ctop_url_1": "https://ctop.example.com/1",
            "auth_token": "tok",
        }
        processed = [{"Level": 42.0, "Temperature": 21.5, "entry_id": "1"}]

        transformed = self.service.transform_to_ctop_format(
            "dev1", processed, device_data=device_data
        )

        self.assertEqual(transformed, [{"waterLevel": 42.0, "temperature": 21.5}])

    def test_transform_to_ctop_format_uses_default_when_override_cleared(self):
        set_ctop_field_map_overrides({})

        device_data = {
            "device_type": "EvaraFlow",
            "name": "Flow 1",
            "ctop_url_1": "https://ctop.example.com/1",
            "auth_token": "tok",
        }
        processed = [{"FlowRate": 1.2, "MeterReading": 99.0, "entry_id": "1"}]

        transformed = self.service.transform_to_ctop_format(
            "dev1", processed, device_data=device_data
        )

        self.assertEqual(transformed, [{"flow_rate": 1.2, "waterconsumption": 99.0}])

    def test_default_map_covers_every_known_device_type(self):
        self.assertEqual(
            set(DEFAULT_CTOP_FIELD_MAP.keys()),
            {"EvaraTank", "EvaraFlow", "EvaraValve", "EvaraDeep", "EvaraTDS"},
        )


if __name__ == "__main__":
    unittest.main()
