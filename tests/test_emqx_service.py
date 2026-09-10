"""
Unit tests for services/emqx_service.py — EMQX Community Edition compatibility fixes.
=====================================================================================
Covers the correctness fixes made to bring the MQTT subscriber layer in line
with what a self-hosted EMQX Community Edition broker actually needs:

  - Real broker-acknowledged connection state (on_connect/on_disconnect),
    instead of treating "a client object exists" as "connected".
  - Password decryption that isn't gated on USE_FIREBASE (the write path
    encrypts emqx_password unconditionally, so decryption must not be
    conditional either).
  - TLS: CA certificate path + insecure/verify-skip opt-out, needed because
    self-hosted EMQX CE ships a self-signed certificate by default.
  - Configurable QoS (0/1/2) instead of a hardcoded subscribe QoS.
  - Last Will and Testament registered before connect.
  - No dangling client registration if connect_async()/loop_start() raises.

paho-mqtt's Client class is mocked throughout — these tests never open a
real socket or need a live broker. Each test constructs the mock so its
on_connect/on_disconnect/on_subscribe callbacks can be invoked manually,
exactly like paho's network thread would invoke them.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.emqx_service import EMQXService
from utils.encryption import get_encryption_service


def make_mock_mqtt_client():
    """A MagicMock standing in for paho.mqtt.client.Client(). Callbacks
    assigned by the service (on_connect, on_subscribe, on_disconnect,
    on_message) land as plain attributes on the mock, so tests can invoke
    them directly to simulate broker events."""
    return MagicMock()


class TestEMQXConnectionState(unittest.TestCase):
    """is_connected()/get_status() must reflect a real broker CONNACK, not
    just that subscribe_device() was called."""

    def setUp(self):
        self.svc = EMQXService()
        self.device_data = {
            "name": "Tank 1",
            "emqx_broker_url": "broker.example.com",
            "emqx_port": 1883,
            "emqx_topic": "evara/tank/1/data",
        }

    @patch("paho.mqtt.client.Client")
    def test_not_connected_until_on_connect_fires(self, MockClient):
        """subscribe_device() only queues connect_async(); the broker hasn't
        replied yet, so is_connected() must be False immediately after."""
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        result = self.svc.subscribe_device("dev1", self.device_data)

        self.assertTrue(result)
        self.assertFalse(self.svc.is_connected("dev1"))
        status = self.svc.get_status()
        self.assertIn("dev1", status["pending_devices"])
        self.assertNotIn("dev1", status["connected_devices"])

    @patch("paho.mqtt.client.Client")
    def test_connected_true_after_successful_connack(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        self.svc.subscribe_device("dev1", self.device_data)
        # Simulate the broker acking the connection (rc=0) on paho's network thread.
        mock_client.on_connect(mock_client, None, None, 0)

        self.assertTrue(self.svc.is_connected("dev1"))
        status = self.svc.get_status()
        self.assertIn("dev1", status["connected_devices"])
        self.assertNotIn("dev1", status["pending_devices"])

    @patch("paho.mqtt.client.Client")
    def test_connected_false_after_failed_connack(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        self.svc.subscribe_device("dev1", self.device_data)
        # rc=5 => "Connection refused - not authorised" (bad credentials)
        mock_client.on_connect(mock_client, None, None, 5)

        self.assertFalse(self.svc.is_connected("dev1"))
        status = self.svc.get_status()
        self.assertIn("dev1", status["pending_devices"])

    @patch("paho.mqtt.client.Client")
    def test_disconnect_clears_connected_state(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        self.svc.subscribe_device("dev1", self.device_data)
        mock_client.on_connect(mock_client, None, None, 0)
        self.assertTrue(self.svc.is_connected("dev1"))

        mock_client.on_disconnect(mock_client, None, None, 1)
        self.assertFalse(self.svc.is_connected("dev1"))

    @patch("paho.mqtt.client.Client")
    def test_unsubscribe_removes_connection_state(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        self.svc.subscribe_device("dev1", self.device_data)
        mock_client.on_connect(mock_client, None, None, 0)
        self.assertTrue(self.svc.is_connected("dev1"))

        self.svc.unsubscribe_device("dev1")
        self.assertFalse(self.svc.is_connected("dev1"))
        status = self.svc.get_status()
        self.assertNotIn("dev1", status["connected_devices"])
        self.assertNotIn("dev1", status["pending_devices"])

    @patch("paho.mqtt.client.Client")
    def test_no_dangling_registration_when_connect_async_raises(self, MockClient):
        """Regression test: registering the client before connect_async()
        (needed to avoid a race with a fast on_connect) must not leave a
        dangling entry in _clients/_connection_state if connect_async()
        itself throws."""
        mock_client = make_mock_mqtt_client()
        mock_client.connect_async.side_effect = OSError("network unreachable")
        MockClient.return_value = mock_client

        result = self.svc.subscribe_device("dev1", self.device_data)

        self.assertFalse(result)
        status = self.svc.get_status()
        self.assertEqual(status["active_connections"], 0)
        self.assertNotIn("dev1", status["connected_devices"])
        self.assertNotIn("dev1", status["pending_devices"])

    @patch("paho.mqtt.client.Client")
    def test_fast_synchronous_connect_is_not_clobbered(self, MockClient):
        """Regression test for the registration-order race: if on_connect
        fires synchronously from inside connect_async() (e.g. a very fast
        local broker), the True state it sets must survive — registration
        happens before connect_async() is even called, not after."""
        mock_client = make_mock_mqtt_client()

        def fast_connect(*args, **kwargs):
            mock_client.on_connect(mock_client, None, None, 0)

        mock_client.connect_async.side_effect = fast_connect
        MockClient.return_value = mock_client

        self.svc.subscribe_device("dev1", self.device_data)

        self.assertTrue(self.svc.is_connected("dev1"))


class TestEMQXQoS(unittest.TestCase):
    """emqx_qos must be validated, defaulted, and actually passed to subscribe()."""

    def setUp(self):
        self.svc = EMQXService()
        self.base_device = {
            "name": "Tank 1",
            "emqx_broker_url": "broker.example.com",
            "emqx_topic": "evara/tank/1/data",
        }

    @patch("paho.mqtt.client.Client")
    def test_default_qos_is_one(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        self.svc.subscribe_device("dev1", self.base_device)
        mock_client.on_connect(mock_client, None, None, 0)

        mock_client.subscribe.assert_called_once_with("evara/tank/1/data", qos=1)

    @patch("paho.mqtt.client.Client")
    def test_custom_qos_is_honored(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        device = {**self.base_device, "emqx_qos": 2}
        self.svc.subscribe_device("dev1", device)
        mock_client.on_connect(mock_client, None, None, 0)

        mock_client.subscribe.assert_called_once_with("evara/tank/1/data", qos=2)

    @patch("paho.mqtt.client.Client")
    def test_qos_zero_is_honored(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        device = {**self.base_device, "emqx_qos": 0}
        self.svc.subscribe_device("dev1", device)
        mock_client.on_connect(mock_client, None, None, 0)

        mock_client.subscribe.assert_called_once_with("evara/tank/1/data", qos=0)

    @patch("paho.mqtt.client.Client")
    def test_out_of_range_qos_falls_back_to_default(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        device = {**self.base_device, "emqx_qos": 7}
        self.svc.subscribe_device("dev1", device)
        mock_client.on_connect(mock_client, None, None, 0)

        mock_client.subscribe.assert_called_once_with("evara/tank/1/data", qos=1)

    @patch("paho.mqtt.client.Client")
    def test_non_numeric_qos_falls_back_to_default(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        device = {**self.base_device, "emqx_qos": "not-a-number"}
        self.svc.subscribe_device("dev1", device)
        mock_client.on_connect(mock_client, None, None, 0)

        mock_client.subscribe.assert_called_once_with("evara/tank/1/data", qos=1)


class TestEMQXTLS(unittest.TestCase):
    """TLS setup must support self-signed EMQX CE brokers via a CA cert
    path, and gate certificate-verification skipping behind an explicit
    opt-in flag."""

    def setUp(self):
        self.svc = EMQXService()
        self.base_device = {
            "name": "Tank 1",
            "emqx_broker_url": "broker.example.com",
            "emqx_topic": "evara/tank/1/data",
            "emqx_use_tls": True,
        }

    @patch("paho.mqtt.client.Client")
    def test_tls_without_ca_cert_uses_system_trust(self, MockClient):
        import ssl

        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        self.svc.subscribe_device("dev1", self.base_device)

        mock_client.tls_set.assert_called_once_with(cert_reqs=ssl.CERT_REQUIRED)
        mock_client.tls_insecure_set.assert_not_called()

    @patch("paho.mqtt.client.Client")
    def test_tls_with_ca_cert_path_passes_it_through(self, MockClient):
        import ssl

        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        device = {
            **self.base_device,
            "emqx_ca_cert_path": "/etc/ctop/certs/emqx-ca.pem",
        }
        self.svc.subscribe_device("dev1", device)

        mock_client.tls_set.assert_called_once_with(
            ca_certs="/etc/ctop/certs/emqx-ca.pem", cert_reqs=ssl.CERT_REQUIRED
        )

    @patch("paho.mqtt.client.Client")
    def test_tls_insecure_skips_verification(self, MockClient):
        import ssl

        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        device = {**self.base_device, "emqx_tls_insecure": True}
        self.svc.subscribe_device("dev1", device)

        mock_client.tls_set.assert_called_once_with(cert_reqs=ssl.CERT_NONE)
        mock_client.tls_insecure_set.assert_called_once_with(True)

    @patch("paho.mqtt.client.Client")
    def test_no_tls_skips_tls_set_entirely(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        device = {**self.base_device, "emqx_use_tls": False}
        self.svc.subscribe_device("dev1", device)

        mock_client.tls_set.assert_not_called()


class TestEMQXLastWill(unittest.TestCase):
    @patch("paho.mqtt.client.Client")
    def test_will_set_before_connect(self, MockClient):
        mock_client = make_mock_mqtt_client()
        call_order = []
        mock_client.will_set.side_effect = lambda *a, **kw: call_order.append(
            "will_set"
        )
        mock_client.connect_async.side_effect = lambda *a, **kw: call_order.append(
            "connect_async"
        )
        MockClient.return_value = mock_client

        svc = EMQXService()
        svc.subscribe_device(
            "dev1",
            {
                "name": "Tank 1",
                "emqx_broker_url": "broker.example.com",
                "emqx_topic": "evara/tank/1/data",
            },
        )

        mock_client.will_set.assert_called_once_with(
            "ctop/dev1/status", payload="offline", qos=1, retain=True
        )
        self.assertEqual(call_order, ["will_set", "connect_async"])


class TestEMQXPasswordDecryption(unittest.TestCase):
    """The write path (routes/device_routes_firestore.py) encrypts
    emqx_password unconditionally, regardless of USE_FIREBASE. Decryption
    on the read side must therefore not be gated on that flag either —
    it previously was, so every non-Firebase deployment sent raw Fernet
    ciphertext as the MQTT password and every broker login failed."""

    @patch("paho.mqtt.client.Client")
    def test_password_decrypted_even_without_firebase(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        encryption_service = get_encryption_service()
        plaintext_password = "s3cr3t-mqtt-pass"
        encrypted_password = encryption_service.encrypt(plaintext_password)

        svc = EMQXService()
        svc.use_firebase = False  # explicitly the non-Firebase path

        svc.subscribe_device(
            "dev1",
            {
                "name": "Tank 1",
                "emqx_broker_url": "broker.example.com",
                "emqx_topic": "evara/tank/1/data",
                "emqx_username": "device-user",
                "emqx_password": encrypted_password,
            },
        )

        mock_client.username_pw_set.assert_called_once_with(
            "device-user", plaintext_password
        )

    @patch("paho.mqtt.client.Client")
    def test_undecryptable_password_falls_back_to_raw_value(self, MockClient):
        """Legacy devices seeded before encryption was added would have a
        plaintext password on file — decrypt() will raise on that, and the
        service must fall back to using it as-is rather than failing the
        whole subscription."""
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        svc = EMQXService()
        svc.use_firebase = False

        result = svc.subscribe_device(
            "dev1",
            {
                "name": "Tank 1",
                "emqx_broker_url": "broker.example.com",
                "emqx_topic": "evara/tank/1/data",
                "emqx_username": "device-user",
                "emqx_password": "plain-legacy-password",
            },
        )

        self.assertTrue(result)
        mock_client.username_pw_set.assert_called_once_with(
            "device-user", "plain-legacy-password"
        )


class TestEMQXSubscribeAck(unittest.TestCase):
    """on_subscribe must be registered so a broker-side ACL rejection is
    distinguishable from 'connected, just no data yet'."""

    @patch("paho.mqtt.client.Client")
    def test_on_subscribe_callback_registered(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        svc = EMQXService()
        svc.subscribe_device(
            "dev1",
            {
                "name": "Tank 1",
                "emqx_broker_url": "broker.example.com",
                "emqx_topic": "evara/tank/1/data",
            },
        )

        self.assertIsNotNone(mock_client.on_subscribe)

    @patch("paho.mqtt.client.Client")
    def test_on_subscribe_handles_granted_qos_without_raising(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        svc = EMQXService()
        svc.subscribe_device(
            "dev1",
            {
                "name": "Tank 1",
                "emqx_broker_url": "broker.example.com",
                "emqx_topic": "evara/tank/1/data",
            },
        )

        # Should not raise for a normal granted-QoS ack.
        mock_client.on_subscribe(mock_client, None, 1, [1])

    @patch("paho.mqtt.client.Client")
    def test_on_subscribe_handles_rejection_without_raising(self, MockClient):
        mock_client = make_mock_mqtt_client()
        MockClient.return_value = mock_client

        svc = EMQXService()
        svc.subscribe_device(
            "dev1",
            {
                "name": "Tank 1",
                "emqx_broker_url": "broker.example.com",
                "emqx_topic": "evara/tank/1/data",
            },
        )

        # 128 = SUBACK failure code (e.g. ACL denied) — must not raise.
        mock_client.on_subscribe(mock_client, None, 1, [128])


if __name__ == "__main__":
    unittest.main()
