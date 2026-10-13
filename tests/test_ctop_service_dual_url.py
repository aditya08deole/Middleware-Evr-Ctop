"""
Regression tests for services/ctop_service.py sending to BOTH ctop_url_1
and ctop_url_2.

Before this fix, send_to_ctop() read ctop_url_2 from device config but
never sent to it — only ctop_url_1 was ever POSTed, even though
ctop_url_2 is a fully-wired, user-configurable field (form, validation,
both device models). A device with a secondary CTOP endpoint configured
silently never delivered to it, with no error anywhere.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ctop_service import CTOPService, set_ctop_hosts, as_bearer


def make_response(status_code, body="{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    resp.history = []
    return resp


class TestCTOPDualURL(unittest.TestCase):
    def setUp(self):
        self.service = CTOPService()
        self.device_data = {
            "name": "Tank 1",
            "device_type": "EvaraTank",
            "ctop_url_1": "https://ctop.example.com/primary",
            "ctop_url_2": "https://ctop.example.com/secondary",
            "auth_token": "tok-123",
        }

    @patch("time.sleep", return_value=None)
    def test_sends_to_both_urls_when_both_succeed(self, mock_sleep):
        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            result = self.service.send_to_ctop(
                "dev1", self.device_data, payload={"x": 1}
            )

        self.assertEqual(mock_post.call_count, 2)
        called_urls = {call.args[0] for call in mock_post.call_args_list}
        self.assertEqual(
            called_urls,
            {"https://ctop.example.com/primary", "https://ctop.example.com/secondary"},
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(all(r["success"] for r in result["results"]))

    @patch("time.sleep", return_value=None)
    def test_primary_succeeds_secondary_fails_still_reports_overall_success(
        self, mock_sleep
    ):
        # 404 on the secondary URL is a permanent failure (no retry loop).
        responses = {
            "https://ctop.example.com/primary": make_response(200),
            "https://ctop.example.com/secondary": make_response(404, "not found"),
        }

        def fake_post(url, **kwargs):
            return responses[url]

        with patch.object(
            self.service.session, "post", side_effect=fake_post
        ) as mock_post:
            result = self.service.send_to_ctop(
                "dev1", self.device_data, payload={"x": 1}
            )

        self.assertEqual(mock_post.call_count, 2)
        self.assertTrue(result["success"])  # at least one endpoint took the data
        per_url = {r["url"]: r["success"] for r in result["results"]}
        self.assertTrue(per_url["https://ctop.example.com/primary"])
        self.assertFalse(per_url["https://ctop.example.com/secondary"])

    @patch("time.sleep", return_value=None)
    def test_both_fail_reports_overall_failure(self, mock_sleep):
        with patch.object(
            self.service.session, "post", return_value=make_response(404, "nope")
        ) as mock_post:
            result = self.service.send_to_ctop(
                "dev1", self.device_data, payload={"x": 1}
            )

        self.assertEqual(mock_post.call_count, 2)
        self.assertFalse(result["success"])
        self.assertIsNotNone(result["error"])

    @patch("time.sleep", return_value=None)
    def test_only_ctop_url_1_configured_sends_once(self, mock_sleep):
        device_data = dict(self.device_data)
        device_data.pop("ctop_url_2")

        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            result = self.service.send_to_ctop("dev1", device_data, payload={"x": 1})

        self.assertEqual(mock_post.call_count, 1)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["results"]), 1)

    @patch("time.sleep", return_value=None)
    def test_sends_to_resolved_ctop_targets_in_addition_to_legacy_slots(
        self, mock_sleep
    ):
        """ctop_targets reference a host_id from the Settings-managed host
        registry + a per-device node_path — resolved to a full URL at send
        time and sent in addition to (not instead of) ctop_url_1/_2."""
        set_ctop_hosts(
            [
                {"id": "dev", "base_url": "https://dev-ctop.example.com"},
                {"id": "staging", "base_url": "https://staging-ctop.example.com/"},
            ]
        )
        self.addCleanup(set_ctop_hosts, [])

        device_data = dict(self.device_data)
        device_data["ctop_targets"] = [
            {"host_id": "dev", "node_path": "/node/1"},
            {"host_id": "staging", "node_path": "/node/2"},
        ]

        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            result = self.service.send_to_ctop(
                "dev1", device_data, payload={"x": 1}
            )

        self.assertEqual(mock_post.call_count, 4)
        called_urls = {call.args[0] for call in mock_post.call_args_list}
        self.assertEqual(
            called_urls,
            {
                "https://ctop.example.com/primary",
                "https://ctop.example.com/secondary",
                "https://dev-ctop.example.com/node/1",
                "https://staging-ctop.example.com/node/2",  # trailing slash on base_url stripped
            },
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["results"]), 4)

    @patch("time.sleep", return_value=None)
    def test_ctop_target_with_unknown_host_id_is_skipped_not_fatal(self, mock_sleep):
        """A target referencing a host_id that was removed from (or never
        existed in) the registry must not crash the send — the legacy URLs
        still go out, it's just that one target that's silently skipped
        (with a warning logged)."""
        set_ctop_hosts([])  # registry empty/host deleted
        self.addCleanup(set_ctop_hosts, [])

        device_data = dict(self.device_data)
        device_data["ctop_targets"] = [{"host_id": "gone", "node_path": "/node/1"}]

        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            result = self.service.send_to_ctop(
                "dev1", device_data, payload={"x": 1}
            )

        self.assertEqual(mock_post.call_count, 2)  # only the two legacy URLs
        self.assertTrue(result["success"])

    @patch("time.sleep", return_value=None)
    def test_duplicate_resolved_url_is_not_sent_twice(self, mock_sleep):
        set_ctop_hosts([{"id": "prod", "base_url": "https://ctop.example.com"}])
        self.addCleanup(set_ctop_hosts, [])

        device_data = dict(self.device_data)
        # Resolves to the same URL as ctop_url_1 — must not double-send.
        device_data["ctop_targets"] = [{"host_id": "prod", "node_path": "/primary"}]

        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            result = self.service.send_to_ctop(
                "dev1", device_data, payload={"x": 1}
            )

        self.assertEqual(mock_post.call_count, 2)  # not 3
        self.assertTrue(result["success"])

    @patch("time.sleep", return_value=None)
    def test_ctop_target_with_node_token_uses_its_own_bearer_not_shared_auth_token(
        self, mock_sleep
    ):
        """Confirmed live against dev-ctop.iiit.ac.in: a node can require
        its own bearer token distinct from the device's shared
        auth_token — reusing the shared token gets 404 'Node token not
        found'. A target with node_token set must use THAT token, not
        auth_token, in its Authorization header."""
        set_ctop_hosts([{"id": "dev", "base_url": "https://dev-ctop.example.com"}])
        self.addCleanup(set_ctop_hosts, [])

        device_data = dict(self.device_data)
        device_data["ctop_targets"] = [
            {
                "host_id": "dev",
                "node_path": "/node/52",
                "node_token": "node-specific-token",
            }
        ]

        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            self.service.send_to_ctop("dev1", device_data, payload={"x": 1})

        calls_by_url = {call.args[0]: call.kwargs["headers"] for call in mock_post.call_args_list}
        self.assertEqual(
            calls_by_url["https://dev-ctop.example.com/node/52"]["Authorization"],
            "Bearer node-specific-token",
        )
        # The legacy URLs must still use the device's own shared token.
        self.assertEqual(
            calls_by_url["https://ctop.example.com/primary"]["Authorization"],
            "Bearer tok-123",
        )

    @patch("time.sleep", return_value=None)
    def test_ctop_target_without_node_token_falls_back_to_shared_auth_token(
        self, mock_sleep
    ):
        set_ctop_hosts([{"id": "dev", "base_url": "https://dev-ctop.example.com"}])
        self.addCleanup(set_ctop_hosts, [])

        device_data = dict(self.device_data)
        device_data["ctop_targets"] = [{"host_id": "dev", "node_path": "/node/1"}]

        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            self.service.send_to_ctop("dev1", device_data, payload={"x": 1})

        calls_by_url = {call.args[0]: call.kwargs["headers"] for call in mock_post.call_args_list}
        self.assertEqual(
            calls_by_url["https://dev-ctop.example.com/node/1"]["Authorization"],
            "Bearer tok-123",
        )

    @patch("time.sleep", return_value=None)
    def test_target_own_token_wins_when_resolved_url_collides_with_legacy_slot(
        self, mock_sleep
    ):
        """If a ctop_targets entry happens to resolve to the exact same
        URL as ctop_url_1/_2, dedup must keep the TARGET's own node_token,
        not silently fall back to the device's shared auth_token — losing
        it would reproduce the 404 'Node token not found' this feature
        exists to avoid, just via a URL coincidence instead of a missing
        host."""
        device_data = dict(self.device_data)
        device_data["ctop_url_1"] = "https://ctop.example.com/collide"
        del device_data["ctop_url_2"]
        set_ctop_hosts([{"id": "dev", "base_url": "https://ctop.example.com"}])
        self.addCleanup(set_ctop_hosts, [])
        device_data["ctop_targets"] = [
            {"host_id": "dev", "node_path": "/collide", "node_token": "node-only-token"}
        ]

        with patch.object(
            self.service.session, "post", return_value=make_response(200)
        ) as mock_post:
            self.service.send_to_ctop("dev1", device_data, payload={"x": 1})

        self.assertEqual(mock_post.call_count, 1)  # deduped to one URL
        self.assertEqual(
            mock_post.call_args.kwargs["headers"]["Authorization"],
            "Bearer node-only-token",
        )

    def test_as_bearer_does_not_double_prefix_an_already_bearer_token(self):
        self.assertEqual(as_bearer("raw-token"), "Bearer raw-token")
        self.assertEqual(as_bearer("Bearer raw-token"), "Bearer raw-token")
        self.assertEqual(as_bearer("bearer raw-token"), "bearer raw-token")
        self.assertIsNone(as_bearer(""))
        self.assertIsNone(as_bearer(None))


if __name__ == "__main__":
    unittest.main()
