"""
Unit tests for the EMQX branch of POST /devices/<id>/test-connection
(routes/device_routes_firestore.py).

Before this fix, "connected" meant only "a paho MQTT client object exists in
EMQXService._clients" — true the instant subscribe_device() is called, well
before (or even if never) the broker actually acks the connection. A device
with a wrong password or unreachable broker would still show as connected.

These tests register the real blueprint on a throwaway Flask app (no
scheduler, no live Firebase) and mock only:
  - local_device_store.get_device_by_id() — so no real device_mirror.db rows
    are touched.
  - utils.scheduler_firestore.emqx_service — the real EMQXService singleton
    swapped for a MagicMock so no real MQTT client/socket is ever created.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from flask import Flask
from routes.device_routes_firestore import device_bp
from utils.local_device_store import local_device_store
import utils.scheduler_firestore as scheduler_mod


def make_app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(device_bp)
    return app


EMQX_DEVICE = {
    'id': 'dev1',
    'name': 'Tank 1',
    'data_source': 'emqx',
    'emqx_broker_url': 'broker.example.com',
    'emqx_port': 1883,
    'emqx_topic': 'evara/tank/1/data',
}


class TestEMQXTestConnectionRoute(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()

    @patch.object(local_device_store, 'get_device_by_id', return_value=EMQX_DEVICE)
    def test_already_connected_reports_success_without_resubscribing(self, mock_get_device):
        mock_emqx = MagicMock()
        mock_emqx.is_connected.return_value = True

        with patch.object(scheduler_mod, 'emqx_service', mock_emqx):
            resp = self.client.post('/devices/dev1/test-connection')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['data']['platform'], 'emqx')
        mock_emqx.subscribe_device.assert_not_called()

    @patch.object(local_device_store, 'get_device_by_id', return_value=EMQX_DEVICE)
    def test_not_connected_but_acks_within_poll_window_reports_success(self, mock_get_device):
        mock_emqx = MagicMock()
        # First call (pre-subscribe check): not connected. Then it becomes
        # connected on the second poll, simulating the broker ack landing
        # a little after subscribe_device() returns.
        mock_emqx.is_connected.side_effect = [False, False, True]
        mock_emqx.subscribe_device.return_value = True

        with patch.object(scheduler_mod, 'emqx_service', mock_emqx), \
             patch('time.sleep', return_value=None):
            resp = self.client.post('/devices/dev1/test-connection')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        mock_emqx.subscribe_device.assert_called_once_with('dev1', EMQX_DEVICE)

    @patch.object(local_device_store, 'get_device_by_id', return_value=EMQX_DEVICE)
    def test_broker_never_acks_reports_honest_failure(self, mock_get_device):
        """This is the core regression this fix targets: a broker that never
        acks (wrong credentials, wrong URL, ACL denial) must NOT be reported
        as a successful connection just because a client object was created."""
        mock_emqx = MagicMock()
        mock_emqx.is_connected.return_value = False
        mock_emqx.subscribe_device.return_value = True

        with patch.object(scheduler_mod, 'emqx_service', mock_emqx), \
             patch('time.sleep', return_value=None):
            resp = self.client.post('/devices/dev1/test-connection')

        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn('has not acknowledged the connection', data['error'])

    @patch.object(local_device_store, 'get_device_by_id', return_value=EMQX_DEVICE)
    def test_client_start_failure_reports_failure(self, mock_get_device):
        mock_emqx = MagicMock()
        mock_emqx.is_connected.return_value = False
        mock_emqx.subscribe_device.return_value = False

        with patch.object(scheduler_mod, 'emqx_service', mock_emqx):
            resp = self.client.post('/devices/dev1/test-connection')

        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn('Failed to start MQTT client', data['error'])

    @patch.object(local_device_store, 'get_device_by_id', return_value=None)
    def test_unknown_device_returns_404(self, mock_get_device):
        resp = self.client.post('/devices/does-not-exist/test-connection')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
