"""
Regression test for services/preprocess_service.py's
transform_to_ctop_format() always emitting real floats.

A whole-number ThingSpeak reading like "20" survives _convert_value() as a
Python int (no decimal point to trigger the float-parsing branch), and
round(int, 2) stays an int too — so a payload like {"temperature": 20}
used to go out to CTOP as a JSON integer, not a float.

Confirmed live against dev-ctop.iiit.ac.in (2026-10-13): it validates
payload types strictly and rejects an int value with
"Wrong data type for water_level. Expected float but got <class 'int'>" —
a real, intermittent 400 that only showed up when a reading happened to
be a whole number, which would have been very hard to reproduce/diagnose
from logs alone (it works for 20.5 but not for 20).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.preprocess_service import PreprocessService


class TestCTOPNumericTypes(unittest.TestCase):
    def setUp(self):
        self.service = PreprocessService()

    def _transform(self, device_data, processed_entry):
        return self.service.transform_to_ctop_format(
            "dev1", [processed_entry], device_data=device_data
        )[0]

    def test_evaratank_whole_number_reading_is_sent_as_float(self):
        device_data = {
            "device_type": "EvaraTank",
            "name": "Tank 1",
            "tank_height": 120,
            "distance_field": "field2",
            "temperature_field": "field1",
            "filtering_method": "none",
            "ctop_url_1": "https://x",
            "auth_token": "tok",
        }
        raw_data = {
            "feeds": [
                {
                    "entry_id": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                    "field1": "20",  # whole number -> int, pre-fix
                    "field2": "80",
                }
            ]
        }

        ok, processed, err = self.service.preprocess_data(
            "dev1", raw_data, device_data=device_data
        )
        self.assertTrue(ok)

        transformed = self.service.transform_to_ctop_format(
            "dev1", processed, device_data=device_data
        )[0]

        for key, value in transformed.items():
            self.assertIsInstance(
                value,
                float,
                f"{key}={value!r} must be a float, not {type(value).__name__}",
            )
        self.assertEqual(transformed["temperature"], 20.0)

    def test_evaraflow_whole_number_meter_reading_is_sent_as_float(self):
        device_data = {"device_type": "EvaraFlow", "name": "Flow 1"}
        processed_entry = {"FlowRate": 5, "MeterReading": 100, "entry_id": "1"}

        transformed = self._transform(device_data, processed_entry)

        self.assertEqual(transformed, {"flow_rate": 5.0, "water_consumption": 100.0})
        for value in transformed.values():
            self.assertIsInstance(value, float)

    def test_already_float_values_stay_float(self):
        device_data = {"device_type": "EvaraTDS", "name": "TDS 1"}
        processed_entry = {"Temperature": 21.5, "TDS": 350.25, "entry_id": "1"}

        transformed = self._transform(device_data, processed_entry)

        self.assertEqual(transformed, {"temperature": 21.5, "tds": 350.25})


if __name__ == "__main__":
    unittest.main()
