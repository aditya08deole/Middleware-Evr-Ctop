"""
Regression tests for PUT /devices/<id> (routes/device_routes_firestore.py
update_device()).

validate_device_data was removed from this route so a partial update (e.g.
"just add a second CTOP endpoint") doesn't have to resend name/ctop_url_1/
auth_token — but that also silently dropped every format/range check that
decorator used to provide (ctop_url_1/_2 format, auth_token length,
latitude/longitude range, required EMQX fields). These tests confirm the
re-added inline checks catch the same bad input, but only when that field
is actually present in the request — an update that doesn't touch a field
still doesn't need to resend it.

Also covers two correctness bugs found in the same code path:
  - existing_targets_by_key previously keyed only by host_id, collapsing
    two targets on the same host (different node_path) into one and
    risking copying the wrong target's node_token onto the other.
  - ctop_service.send_to_ctop's dedup order could discard a target's own
    node_token in favor of the device's shared auth_token if the target's
    resolved URL happened to collide with ctop_url_1/_2 (see
    test_ctop_service_dual_url.py for that half of the fix).

Uses the same pattern as test_device_routes_emqx.py: register the real
blueprint on a throwaway Flask app, mock only firestore_service and
local_device_store so no real Firebase/SQLite is touched.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask
import routes.device_routes_firestore as routes_mod
from utils.local_device_store import local_device_store


def make_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(routes_mod.device_bp)
    return app


BASE_DEVICE = {
    "id": "dev1",
    "name": "Tank 1",
    "data_source": "thingspeak",
    "ctop_url_1": "https://ctop.example.com/node/1",
    "auth_token": "existing-encrypted-token",
    "ctop_targets": [],
}


class TestUpdateDeviceValidation(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.get_device_patch = patch.object(
            routes_mod.firestore_service, "get_device", return_value=dict(BASE_DEVICE)
        )
        self.update_device_patch = patch.object(
            routes_mod.firestore_service, "update_device", return_value=True
        )
        self.local_update_patch = patch.object(
            local_device_store, "update_device_fields", return_value=True
        )
        self.get_device_patch.start()
        self.update_device_patch.start()
        self.local_update_patch.start()
        self.addCleanup(self.get_device_patch.stop)
        self.addCleanup(self.update_device_patch.stop)
        self.addCleanup(self.local_update_patch.stop)
        # get_device_by_id is also called post-update to check data_source
        # for the EMQX resubscribe branch — must return something so that
        # branch doesn't crash on None.get(...).
        patcher = patch.object(
            local_device_store, "get_device_by_id", return_value=dict(BASE_DEVICE)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_partial_update_without_name_or_ctop_url_1_still_succeeds(self):
        """Baseline: a PUT touching only one unrelated field must not need
        to resend name/ctop_url_1/auth_token."""
        resp = self.client.put("/devices/dev1", json={"is_active": False})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

    def test_invalid_ctop_url_1_format_rejected(self):
        resp = self.client.put("/devices/dev1", json={"ctop_url_1": "not-a-url"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ctop_url_1", resp.get_json()["error"])

    def test_invalid_ctop_url_2_format_rejected(self):
        resp = self.client.put("/devices/dev1", json={"ctop_url_2": "not-a-url"})
        self.assertEqual(resp.status_code, 400)

    def test_blank_ctop_url_2_is_not_treated_as_invalid(self):
        """Clearing the optional secondary URL must still be allowed."""
        resp = self.client.put("/devices/dev1", json={"ctop_url_2": ""})
        self.assertEqual(resp.status_code, 200)

    def test_too_short_auth_token_rejected(self):
        resp = self.client.put("/devices/dev1", json={"auth_token": "short"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Auth token", resp.get_json()["error"])

    def test_latitude_out_of_range_rejected(self):
        resp = self.client.put("/devices/dev1", json={"latitude": 200})
        self.assertEqual(resp.status_code, 400)

    def test_longitude_out_of_range_rejected(self):
        resp = self.client.put("/devices/dev1", json={"longitude": -200})
        self.assertEqual(resp.status_code, 400)

    def test_switching_to_emqx_without_required_fields_rejected(self):
        resp = self.client.put("/devices/dev1", json={"data_source": "emqx"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("EMQX", resp.get_json()["error"])

    def test_already_emqx_device_partial_update_not_touching_emqx_fields_ok(self):
        """A device that's already emqx, with valid emqx config already
        stored, updating an unrelated field must not be forced to resend
        every emqx_* field — the merged-state check should see the
        existing config and pass."""
        emqx_device = dict(
            BASE_DEVICE,
            data_source="emqx",
            emqx_broker_url="broker.example.com",
            emqx_topic="t/1",
            emqx_username="u",
            emqx_password="encrypted-pw",
        )
        with patch.object(
            routes_mod.firestore_service, "get_device", return_value=emqx_device
        ):
            with patch.object(
                local_device_store, "get_device_by_id", return_value=emqx_device
            ):
                resp = self.client.put("/devices/dev1", json={"is_active": False})
        self.assertEqual(resp.status_code, 200)

    def test_ctop_targets_sharing_host_with_different_paths_dont_cross_contaminate_tokens(
        self,
    ):
        """existing_targets_by_key must match on (host_id, node_path), not
        host_id alone — otherwise saving target A's token could get
        copied onto target B just because they share a host."""
        device_with_two_targets = dict(
            BASE_DEVICE,
            ctop_targets=[
                {
                    "host_id": "dev",
                    "node_path": "/node/1",
                    "node_token": "encrypted-token-A",
                },
                {"host_id": "dev", "node_path": "/node/2"},  # no token
            ],
        )
        with patch.object(
            routes_mod.firestore_service,
            "get_device",
            return_value=device_with_two_targets,
        ):
            # Re-save both targets with blank node_token fields (UI sends
            # no node_token when the user didn't type a new one).
            resp = self.client.put(
                "/devices/dev1",
                json={
                    "ctop_targets": [
                        {"host_id": "dev", "node_path": "/node/1"},
                        {"host_id": "dev", "node_path": "/node/2"},
                    ]
                },
            )

        self.assertEqual(resp.status_code, 200)
        saved_targets = routes_mod.firestore_service.update_device.call_args[0][1][
            "ctop_targets"
        ]
        by_path = {t["node_path"]: t for t in saved_targets}
        self.assertEqual(by_path["/node/1"].get("node_token"), "encrypted-token-A")
        self.assertNotIn("node_token", by_path["/node/2"])


if __name__ == "__main__":
    unittest.main()
