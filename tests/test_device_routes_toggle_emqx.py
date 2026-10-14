"""
Regression test for POST /devices/<id>/toggle (routes/device_routes_firestore.py).

Before this fix, toggling an EMQX device off/on only flipped is_active in
storage — it never called emqx_service.unsubscribe_device()/subscribe_device()
the way PUT /devices/<id> already did for the same is_active change. Two
real consequences:
  - Toggling off left the MQTT client connected and still buffering
    messages (silently, since the scheduler skips inactive devices).
  - Toggling back on reused that same stale buffer instead of starting a
    fresh subscription — so a known-bad leftover message (e.g. old test
    data missing a required field) kept getting retried forever with no
    way to clear it short of editing the device via PUT instead.

Uses the same pattern as test_device_routes_emqx.py: register the real
blueprint on a throwaway Flask app, mock local_device_store and the
firestore_service singleton so no real Firebase/SQLite is touched, and
swap the real EMQXService singleton for a MagicMock.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask
import routes.device_routes_firestore as routes_mod
from utils.local_device_store import local_device_store
import utils.scheduler_firestore as scheduler_mod


def make_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(routes_mod.device_bp)
    return app


class TestToggleDeviceEMQX(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.update_device_patch = patch.object(
            routes_mod.firestore_service, "update_device", return_value=True
        )
        self.local_update_patch = patch.object(
            local_device_store, "update_device_fields", return_value=True
        )
        self.update_device_patch.start()
        self.local_update_patch.start()
        self.addCleanup(self.update_device_patch.stop)
        self.addCleanup(self.local_update_patch.stop)

    def test_toggling_off_unsubscribes_emqx_device(self):
        device = {"id": "dev1", "name": "Tank 1", "data_source": "emqx", "is_active": True}
        mock_emqx = MagicMock()

        with patch.object(local_device_store, "get_device_by_id", return_value=device):
            with patch.object(scheduler_mod, "emqx_service", mock_emqx):
                resp = self.client.post("/devices/dev1/toggle")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["data"]["is_active"])
        mock_emqx.unsubscribe_device.assert_called_once_with("dev1")
        mock_emqx.subscribe_device.assert_not_called()

    def test_toggling_on_resubscribes_emqx_device(self):
        device = {"id": "dev1", "name": "Tank 1", "data_source": "emqx", "is_active": False}
        mock_emqx = MagicMock()

        with patch.object(local_device_store, "get_device_by_id", return_value=device):
            with patch.object(scheduler_mod, "emqx_service", mock_emqx):
                resp = self.client.post("/devices/dev1/toggle")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["data"]["is_active"])
        mock_emqx.subscribe_device.assert_called_once()
        mock_emqx.unsubscribe_device.assert_not_called()
        # Must pass the NEW is_active (True) so subscribe_device doesn't see stale state
        called_device_arg = mock_emqx.subscribe_device.call_args[0][1]
        self.assertTrue(called_device_arg["is_active"])

    def test_toggling_thingspeak_device_does_not_touch_emqx_service(self):
        device = {"id": "dev1", "name": "Tank 1", "data_source": "thingspeak", "is_active": True}
        mock_emqx = MagicMock()

        with patch.object(local_device_store, "get_device_by_id", return_value=device):
            with patch.object(scheduler_mod, "emqx_service", mock_emqx):
                resp = self.client.post("/devices/dev1/toggle")

        self.assertEqual(resp.status_code, 200)
        mock_emqx.subscribe_device.assert_not_called()
        mock_emqx.unsubscribe_device.assert_not_called()


if __name__ == "__main__":
    unittest.main()
