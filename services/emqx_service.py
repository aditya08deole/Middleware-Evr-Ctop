"""
EMQX MQTT Service for fetching IoT data via MQTT protocol.

This service manages persistent MQTT connections to EMQX brokers,
subscribes to device topics, buffers incoming messages, and exposes
a fetch_data() interface compatible with ThingSpeakService so the
downstream pipeline (preprocess → transform → CTOP) works unchanged.
"""

import json
import threading
import time
import logging
import os
from collections import deque
from datetime import datetime, timezone
from config import Config

logger = logging.getLogger(__name__)


class EMQXService:
    """Service for fetching IoT data from EMQX MQTT brokers.

    Maintains persistent MQTT subscriptions per device and buffers
    incoming messages. The scheduler calls fetch_data() to retrieve
    the latest buffered data in ThingSpeak-compatible format.
    """

    def __init__(self):
        self.use_firebase = os.environ.get("USE_FIREBASE", "false").lower() == "true"

        if self.use_firebase:
            from utils.encryption import get_encryption_service

            self.encryption_service = get_encryption_service()

        # Per-device message buffer: {device_id: deque([messages])}
        # Using deque with maxlen for automatic oldest-message eviction (no copy overhead)
        self._message_buffers = {}
        self._buffer_lock = threading.Lock()
        self._max_buffer_size = Config.EMQX_MESSAGE_BUFFER_SIZE

        # Active MQTT clients: {device_id: mqtt.Client}
        self._clients = {}
        self._clients_lock = threading.Lock()

        # Real broker-acknowledged connection state: {device_id: bool}.
        # Set True only inside on_connect when the broker returns rc==0,
        # and False on disconnect/failed connect — NOT merely when a
        # client object exists in self._clients (which is true the instant
        # connect_async() is called, long before the broker responds).
        self._connection_state = {}

        # Per-device stable field-slot schema for named-key MQTT payloads:
        # {device_id: [key_name_for_field1, key_name_for_field2, ...]}.
        # Established from the first message and only ever appended to, so a
        # later message missing one key doesn't shift every other key into a
        # different field slot (see _mqtt_message_to_feed).
        self._device_field_schemas = {}
        self._schema_lock = threading.Lock()

    def subscribe_device(self, device_id, device_data):
        """
        Start an MQTT subscription for a device.

        Creates a new paho-mqtt client, connects to the device's EMQX broker,
        and subscribes to its topic. Messages are buffered in-memory.

        Args:
            device_id: Unique device identifier
            device_data: Device config dict with EMQX credentials
        """
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt is not installed. Run: pip install paho-mqtt")
            return False

        broker_url = device_data.get("emqx_broker_url")
        port = device_data.get("emqx_port", Config.EMQX_DEFAULT_PORT)
        username = device_data.get("emqx_username")
        password = device_data.get("emqx_password")
        topic = device_data.get("emqx_topic")
        use_tls = device_data.get("emqx_use_tls", False)
        tls_insecure = device_data.get("emqx_tls_insecure", False)
        ca_cert_path = device_data.get("emqx_ca_cert_path") or None

        try:
            qos = int(device_data.get("emqx_qos", 1))
        except (ValueError, TypeError):
            qos = 1
        if qos not in (0, 1, 2):
            qos = 1

        if not broker_url or not topic:
            logger.error(f"[EMQX] Device {device_id}: Missing broker_url or topic")
            return False

        # Decrypt credentials. The add/update device routes encrypt
        # emqx_password unconditionally regardless of USE_FIREBASE, so
        # decryption must not be gated on that flag either — previously
        # this only ran when USE_FIREBASE=true, which meant every non-
        # Firebase deployment sent the raw Fernet ciphertext as the MQTT
        # password and every broker login failed. Fall back to the raw
        # value if it isn't actually encrypted (e.g. legacy plaintext
        # devices seeded before encryption was added).
        if password:
            try:
                encryption_service = getattr(self, "encryption_service", None)
                if encryption_service is None:
                    from utils.encryption import get_encryption_service

                    encryption_service = get_encryption_service()
                password = encryption_service.decrypt(password)
            except Exception as e:
                logger.debug(
                    f"[EMQX] Device {device_id}: Password not decryptable, using as-is: {e}"
                )

        # Unsubscribe existing client if any
        self.unsubscribe_device(device_id)

        device_name = device_data.get("name", device_id)
        client_id = f"ctop-{device_id}"  # Persistent client_id (no timestamp suffix)

        try:
            # Create MQTT client (paho-mqtt v2.0+ API)
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                clean_session=False,  # Persist session across reconnects for QoS 1 reliability
            )

            # Set credentials
            if username:
                client.username_pw_set(username, password)

            # Enable TLS if configured. A CA cert path lets this connect to
            # self-hosted EMQX (Community Edition brokers almost always run
            # with a self-signed cert out of the box, which fails default
            # system-CA verification with no way to configure it before).
            # emqx_tls_insecure is an explicit opt-out of verification for
            # lab/dev brokers only — never the default.
            if use_tls:
                import ssl

                cert_reqs = ssl.CERT_NONE if tls_insecure else ssl.CERT_REQUIRED
                if ca_cert_path:
                    client.tls_set(ca_certs=ca_cert_path, cert_reqs=cert_reqs)
                else:
                    client.tls_set(cert_reqs=cert_reqs)
                if tls_insecure:
                    client.tls_insecure_set(True)

            # Last Will and Testament: lets the broker (and anything else
            # subscribed to this topic) know immediately if this client
            # drops off uncleanly, instead of relying only on the 30-minute
            # staleness heuristic in the scheduler.
            client.will_set(
                f"ctop/{device_id}/status", payload="offline", qos=1, retain=True
            )

            # Set callbacks (using closures to capture device_id)
            def on_connect(client, userdata, flags, rc, properties=None):
                rc_val = rc if isinstance(rc, int) else rc.value
                dev_id = str(device_id)
                if rc_val == 0:
                    with self._clients_lock:
                        self._connection_state[dev_id] = True
                    logger.info(
                        f"[EMQX] Device {device_name} ({device_id}): Connected to {broker_url}:{port}"
                    )
                    client.subscribe(topic, qos=qos)
                    logger.info(
                        f"[EMQX] Device {device_name} ({device_id}): Subscribed to topic '{topic}' (QoS {qos})"
                    )
                else:
                    with self._clients_lock:
                        self._connection_state[dev_id] = False
                    logger.error(
                        f"[EMQX] Device {device_name} ({device_id}): Connection failed, rc={rc_val}"
                    )

            def on_subscribe(client, userdata, mid, reason_codes, properties=None):
                codes = (
                    reason_codes if isinstance(reason_codes, list) else [reason_codes]
                )
                failed = [
                    c
                    for c in codes
                    if (c if isinstance(c, int) else getattr(c, "value", 1)) >= 128
                ]
                if failed:
                    logger.error(
                        f"[EMQX] Device {device_name} ({device_id}): "
                        f"Broker rejected subscription to '{topic}' (reason codes: {codes})"
                    )
                else:
                    logger.debug(
                        f"[EMQX] Device {device_name} ({device_id}): Subscription acknowledged (granted: {codes})"
                    )

            def on_message(client, userdata, msg):
                try:
                    payload_str = msg.payload.decode("utf-8")
                    payload = json.loads(payload_str)

                    # Add receive timestamp if not present
                    if "created_at" not in payload:
                        payload["created_at"] = datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        )

                    with self._buffer_lock:
                        dev_id = str(device_id)
                        if dev_id not in self._message_buffers:
                            self._message_buffers[dev_id] = deque(
                                maxlen=self._max_buffer_size
                            )
                        self._message_buffers[dev_id].append(payload)

                    logger.debug(
                        f"[EMQX] Device {device_name} ({device_id}): "
                        f"Received message on '{msg.topic}' "
                        f"(buffer size: {len(self._message_buffers.get(str(device_id), []))})"
                    )

                    # Process instantly instead of waiting for the next scheduler
                    # tick — MQTT is push-based, so a new reading should reach
                    # CTOP the moment it arrives, not up to 15s later.
                    self._trigger_instant_process(device_id, device_name)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[EMQX] Device {device_name} ({device_id}): "
                        f"Invalid JSON on '{msg.topic}': {e}"
                    )
                except Exception as e:
                    logger.error(
                        f"[EMQX] Device {device_name} ({device_id}): "
                        f"Error processing message: {e}"
                    )

            def on_disconnect(client, userdata, flags, rc, properties=None):
                rc_val = rc if isinstance(rc, int) else rc.value
                with self._clients_lock:
                    self._connection_state[str(device_id)] = False
                if rc_val != 0:
                    logger.warning(
                        f"[EMQX] Device {device_name} ({device_id}): "
                        f"Unexpected disconnect (rc={rc_val}), auto-reconnecting..."
                    )

            client.on_connect = on_connect
            client.on_message = on_message
            client.on_disconnect = on_disconnect
            client.on_subscribe = on_subscribe

            # Enable auto-reconnect
            client.reconnect_delay_set(
                min_delay=Config.EMQX_RECONNECT_DELAY,
                max_delay=Config.EMQX_RECONNECT_DELAY * 10,
            )

            # Register the client BEFORE starting the network thread. loop_start()
            # can invoke on_connect from a background thread almost immediately
            # (e.g. a local broker) — registering after connect_async()/loop_start()
            # would let that callback's True state get clobbered back to False by
            # this same dict write racing behind it.
            with self._clients_lock:
                self._clients[str(device_id)] = client
                self._connection_state[str(device_id)] = False

            try:
                # Connect (non-blocking)
                client.connect_async(
                    broker_url, int(port), keepalive=Config.EMQX_KEEPALIVE
                )
                client.loop_start()  # Starts background network thread
            except Exception:
                # Undo the registration above so a failed connect_async/loop_start
                # doesn't leave a dangling client that never actually started.
                with self._clients_lock:
                    self._clients.pop(str(device_id), None)
                    self._connection_state.pop(str(device_id), None)
                raise

            logger.info(
                f"[EMQX] Device {device_name} ({device_id}): "
                f"MQTT client started → {broker_url}:{port} topic='{topic}'"
            )
            return True

        except Exception as e:
            logger.error(
                f"[EMQX] Device {device_name} ({device_id}): Failed to start MQTT client: {e}"
            )
            return False

    def _trigger_instant_process(self, device_id, device_name):
        """
        Fire process_device_safe() in a background thread right after a
        message is buffered, so it reaches CTOP immediately instead of
        waiting for the next periodic scheduler tick. Deferred import avoids
        a circular import (scheduler_firestore/scheduler import EMQXService).
        process_device_safe() itself is a no-op if that device is already
        being processed, so bursts of messages don't pile up duplicate runs.

        Routes to whichever scheduler backend is actually active
        (USE_FIREBASE) — this same EMQXService singleton is shared by both
        utils/scheduler_firestore.py and utils/scheduler.py, and calling the
        wrong one's process_device_safe() would either look up a device that
        was never synced into its store, or hit db.session with no Flask
        app context pushed.
        """

        def _run():
            try:
                use_firebase = os.environ.get("USE_FIREBASE", "false").lower() == "true"
                if use_firebase:
                    from utils.scheduler_firestore import process_device_safe
                    from utils.local_device_store import local_device_store

                    device_data = local_device_store.get_device_by_id(str(device_id))
                    if device_data:
                        process_device_safe(str(device_id), device_data)
                else:
                    # Legacy SQLite/SQLAlchemy stack: process_device_safe()
                    # takes just the device_id — it looks up the Device row
                    # itself and pushes its own Flask app context.
                    from utils.scheduler import process_device_safe

                    process_device_safe(str(device_id))
            except Exception as e:
                logger.error(
                    f"[EMQX] Instant-process trigger failed for {device_name} ({device_id}): {e}"
                )

        threading.Thread(
            target=_run, daemon=True, name=f"emqx-instant-{device_id}"
        ).start()

    def unsubscribe_device(self, device_id):
        """
        Stop MQTT subscription for a device and clean up.

        Args:
            device_id: Device identifier to unsubscribe
        """
        device_id_str = str(device_id)

        with self._clients_lock:
            client = self._clients.pop(device_id_str, None)
            self._connection_state.pop(device_id_str, None)

        if client:
            try:
                client.loop_stop()
                client.disconnect()
                logger.info(
                    f"[EMQX] Device {device_id}: MQTT client stopped and disconnected"
                )
            except Exception as e:
                logger.warning(
                    f"[EMQX] Device {device_id}: Error during disconnect: {e}"
                )

        with self._buffer_lock:
            self._message_buffers.pop(device_id_str, None)

        with self._schema_lock:
            self._device_field_schemas.pop(device_id_str, None)

    def fetch_data(self, device_id, device_data=None):
        """
        Fetch the latest buffered MQTT data for a device.

        Returns data in ThingSpeak-compatible format so the downstream
        PreprocessService works without changes.

        NOTE: Messages are moved to a pending buffer (not deleted) so they
        can be restored if downstream processing fails. Call confirm_consumed()
        after successful CTOP send, or rollback_fetch() on failure.

        Args:
            device_id: Device identifier
            device_data: Optional device data dict

        Returns:
            tuple: (success: bool, data: dict or None, error: str or None)
                   data format: {"channel": {...}, "feeds": [...]}
        """
        device_id_str = str(device_id)
        device_name = device_data.get("name", device_id) if device_data else device_id

        # Check if client is connected
        with self._clients_lock:
            client = self._clients.get(device_id_str)

        if not client:
            # Try to auto-subscribe if device_data is available
            if device_data and device_data.get("emqx_broker_url"):
                logger.info(
                    f"[EMQX] Device {device_name}: No active subscription, auto-subscribing..."
                )
                if not self.subscribe_device(device_id, device_data):
                    return False, None, "Failed to establish MQTT connection"
                # Give the connection a moment to establish
                return (
                    True,
                    {
                        "channel": {"id": device_id_str, "name": device_name},
                        "feeds": [],
                    },
                    None,
                )
            return False, None, "No active MQTT subscription for this device"

        # Drain the message buffer into a pending state
        with self._buffer_lock:
            buf = self._message_buffers.get(device_id_str)
            if buf:
                messages = list(buf)
                buf.clear()
            else:
                messages = []

            # Store drained messages in a pending key so they can be rolled back
            pending_key = f"_pending_{device_id_str}"
            self._message_buffers[pending_key] = deque(
                messages, maxlen=self._max_buffer_size
            )

        if not messages:
            # No new messages — return empty feeds (not an error)
            return (
                True,
                {"channel": {"id": device_id_str, "name": device_name}, "feeds": []},
                None,
            )

        # Convert MQTT messages to ThingSpeak-compatible feed format
        feeds = []
        for msg in messages:
            feed = self._mqtt_message_to_feed(msg, device_id_str)
            if feed:
                feeds.append(feed)

        logger.info(
            f"[EMQX] Device {device_name} ({device_id}): "
            f"Fetched {len(feeds)} new messages from buffer"
        )

        return (
            True,
            {"channel": {"id": device_id_str, "name": device_name}, "feeds": feeds},
            None,
        )

    def confirm_consumed(self, device_id):
        """Clear the pending buffer after successful downstream processing."""
        device_id_str = str(device_id)
        pending_key = f"_pending_{device_id_str}"
        with self._buffer_lock:
            self._message_buffers.pop(pending_key, None)

    def rollback_fetch(self, device_id):
        """Restore drained messages back to the main buffer on downstream failure."""
        device_id_str = str(device_id)
        pending_key = f"_pending_{device_id_str}"
        with self._buffer_lock:
            pending = self._message_buffers.pop(pending_key, None)
            if pending:
                if device_id_str not in self._message_buffers:
                    self._message_buffers[device_id_str] = deque(
                        maxlen=self._max_buffer_size
                    )
                # Prepend rolled-back messages before any new arrivals
                new_buf = deque(pending, maxlen=self._max_buffer_size)
                new_buf.extend(self._message_buffers[device_id_str])
                self._message_buffers[device_id_str] = new_buf
                logger.warning(
                    f"[EMQX] Device {device_id}: Rolled back {len(pending)} messages to buffer"
                )

    def _mqtt_message_to_feed(self, mqtt_msg, device_id):
        """
        Convert an MQTT JSON message to ThingSpeak-compatible feed entry.

        Supports two incoming formats:
        1. ThingSpeak-style: {"field1": "25.5", "field2": "60", "created_at": "..."}
        2. Named keys: {"temperature": 25.5, "distance": 30.2, "created_at": "..."}
           → mapped to field1, field2, etc. using a per-device schema that's
           established from the first message and only ever appended to
           (never re-sorted from scratch), so one message missing a key
           doesn't shift every other key into a different field slot.

        Args:
            mqtt_msg: Parsed JSON dict from MQTT message
            device_id: Device identifier

        Returns:
            dict: ThingSpeak-compatible feed entry with entry_id, field1-8, created_at
        """
        feed = {}

        # Get or generate timestamp
        created_at = mqtt_msg.get(
            "created_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        feed["created_at"] = created_at

        # Generate a synthetic entry_id from timestamp
        # Use epoch seconds as a monotonically increasing ID
        try:
            if isinstance(created_at, str):
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            else:
                dt = created_at
            feed["entry_id"] = str(int(dt.timestamp()))
        except (ValueError, TypeError, AttributeError):
            feed["entry_id"] = str(int(time.time()))

        # First copy all original message keys to feed (excluding metadata)
        skip_keys = {"created_at", "timestamp", "entry_id", "device_id", "id"}
        for k, v in mqtt_msg.items():
            if k not in skip_keys and v is not None:
                feed[k] = str(v)

        # Check if data is already in fieldN format
        has_field_keys = any(
            k.startswith("field") and k[5:].isdigit() for k in mqtt_msg.keys()
        )

        if has_field_keys:
            # Already in ThingSpeak format — ensure field1 through field8 are set
            for i in range(1, 9):
                key = f"field{i}"
                if key in mqtt_msg and key not in feed:
                    feed[key] = (
                        str(mqtt_msg[key]) if mqtt_msg[key] is not None else None
                    )
        else:
            # Named keys → map to field1, field2, etc. using a stable
            # per-device schema (established once, extended for new keys,
            # never re-derived from just this message's own key set).
            data_keys = sorted([k for k in mqtt_msg.keys() if k not in skip_keys])
            with self._schema_lock:
                schema = self._device_field_schemas.get(device_id)
                if schema is None:
                    schema = list(data_keys[:8])
                    self._device_field_schemas[device_id] = schema
                else:
                    for key in data_keys:
                        if key not in schema and len(schema) < 8:
                            schema.append(key)
                schema_snapshot = list(schema)

            for i, key in enumerate(schema_snapshot, start=1):
                if key not in mqtt_msg:
                    continue
                value = mqtt_msg[key]
                field_name = f"field{i}"
                if field_name not in feed:
                    feed[field_name] = str(value) if value is not None else None

        return feed

    def subscribe_all_devices(self, devices):
        """
        Subscribe to all EMQX devices from the device list.
        Called during application startup.

        Args:
            devices: List of device dicts from the cache/store
        """
        emqx_count = 0
        for device in devices:
            if device.get("data_source") == "emqx":
                self.subscribe_device(device.get("id"), device)
                emqx_count += 1

        if emqx_count > 0:
            logger.info(f"[EMQX] Subscribed to {emqx_count} EMQX device(s) at startup")

    def is_connected(self, device_id):
        """True only if the broker has actually acknowledged this device's
        connection (on_connect fired with rc==0) — not merely that a client
        object exists (true the instant subscribe_device() is called, well
        before the network round-trip completes)."""
        with self._clients_lock:
            return bool(self._connection_state.get(str(device_id)))

    def get_status(self):
        """
        Get status summary of all MQTT connections.

        'connected_devices' reflects broker-acknowledged connections only;
        'pending_devices' are clients that were started but haven't (yet,
        or ever) received a successful CONNACK — e.g. bad credentials, wrong
        broker URL, or still mid-handshake.

        Returns:
            dict: Status info for all tracked devices
        """
        with self._clients_lock:
            active_clients = list(self._clients.keys())
            connected = [d for d in active_clients if self._connection_state.get(d)]
            pending = [d for d in active_clients if not self._connection_state.get(d)]

        with self._buffer_lock:
            buffer_sizes = {
                k: len(v)
                for k, v in self._message_buffers.items()
                if not k.startswith("_pending_")
            }

        return {
            "active_connections": len(active_clients),
            "connected_devices": connected,
            "pending_devices": pending,
            "buffer_sizes": buffer_sizes,
        }

    def shutdown(self):
        """Gracefully disconnect all MQTT clients."""
        with self._clients_lock:
            device_ids = list(self._clients.keys())

        for device_id in device_ids:
            self.unsubscribe_device(device_id)

        logger.info("[EMQX] All MQTT clients shut down")
