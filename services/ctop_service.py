import requests
import json
import logging
import threading
import time
from urllib3.util.retry import Retry
from models import db, Device
from config import Config
import os

# Registry of named CTOP hosts (base URL only, e.g. "https://ctop.iiit.ac.in")
# that a device's ctop_targets entries reference by host_id instead of
# hardcoding a full URL — see DeviceModel.ctop_targets. Settings →
# CTOP Hosts is the only place this is meant to be edited: app.py loads it
# into this cache at startup, and settings_routes.py refreshes it
# immediately whenever the registry is saved, so repointing a host (e.g.
# dev-ctop.iiit.ac.in moving to a new address) takes effect on the very
# next scheduler tick without a restart or touching any device record.
_ctop_hosts_cache = {}
_ctop_hosts_lock = threading.Lock()


def set_ctop_hosts(hosts):
    """Replace the in-memory host registry. `hosts` is a list of
    {"id": ..., "base_url": ...} dicts (extra keys like "name" are
    ignored here)."""
    global _ctop_hosts_cache
    with _ctop_hosts_lock:
        _ctop_hosts_cache = {
            h["id"]: h["base_url"].rstrip("/")
            for h in (hosts or [])
            if h.get("id") and h.get("base_url")
        }


def get_ctop_hosts():
    """Current effective host registry, as {host_id: base_url}."""
    with _ctop_hosts_lock:
        return dict(_ctop_hosts_cache)


def as_bearer(token):
    """Turn a raw token into a full 'Bearer <token>' Authorization value,
    without double-prefixing a token that already has one. Module-level
    (not just used inside send_to_ctop below) because app.py's
    /api/test-connection route needs the exact same guard — it used to
    unconditionally do f"Bearer {token}", so a token a user already typed
    with a 'Bearer ' prefix came out as 'Bearer Bearer xyz' and the test
    failed even for valid credentials."""
    if not token:
        return None
    return token if str(token).lower().startswith("bearer ") else f"Bearer {token}"


class CTOPService:
    """Service for sending data to CTOP API endpoints"""

    def __init__(self):
        self.timeout = Config.CTOP_TIMEOUT
        self.max_retries = Config.CTOP_MAX_RETRIES
        self.retry_delay = Config.CTOP_RETRY_DELAY
        self.use_firebase = os.environ.get("USE_FIREBASE", "false").lower() == "true"

        if self.use_firebase:
            from utils.encryption import get_encryption_service

            self.encryption_service = get_encryption_service()

        # Phase 2 Optimization: Persistent HTTP Session for connection pooling
        self.session = requests.Session()
        # Transport-level retry: a pooled keep-alive connection can be closed
        # server-side between scheduler ticks (every 15s) and the next reuse
        # fails with ConnectionResetError/RemoteDisconnected. This makes
        # urllib3 transparently evict the dead connection and retry with a
        # fresh one *inside* a single session.post() call. status=0 means it
        # never retries based on HTTP status code — that decision (4xx vs
        # 5xx) is made explicitly in _send_with_retry below, with logging.
        connection_retry = Retry(
            total=2,
            connect=2,
            read=2,
            redirect=0,
            status=0,
            backoff_factor=0.2,
        )
        # Increase pool size to handle parallel requests from scheduler
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=Config.SCHEDULER_MAX_WORKERS,
            max_retries=connection_retry,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def send_to_ctop(self, device_id, device_data=None, payload=None):
        """
        Send data to CTOP endpoints for a specific device

        Args:
            device_id: ID of the device
            device_data: Optional device data dict (for Firestore)
            payload: Data payload to send

        Returns:
            dict: Results for each endpoint
        """
        # Get device data
        if device_data:
            device = device_data
            ctop_url_1 = device.get("ctop_url_1")
            ctop_url_2 = device.get("ctop_url_2")
            ctop_targets = device.get("ctop_targets") or []
            auth_token = device.get("auth_token")
        else:
            # SQLite fallback
            device = db.session.get(Device, device_id)
            if not device:
                return {"error": "Device not found"}
            ctop_url_1 = device.ctop_url_1
            ctop_url_2 = device.ctop_url_2
            ctop_targets = []
            auth_token = device.auth_token

        # Decrypt auth token if using Firebase
        if self.use_firebase and auth_token:
            try:
                auth_token = self.encryption_service.decrypt(auth_token)
            except Exception as e:
                return {"error": f"Failed to decrypt auth token: {str(e)}"}

        # Resolve ctop_targets (host_id + node_path [+ node_token]) against
        # the current host registry. A target whose host_id was deleted
        # from the registry (or never existed) is skipped with a warning
        # rather than crashing the whole send — the two legacy URLs still
        # go out. node_token is optional and per-target: some CTOP
        # environments (confirmed live against dev-ctop.iiit.ac.in) issue a
        # distinct bearer token per node rather than accepting the same
        # token a device already uses against production — reusing the
        # device's shared auth_token there gets a 404 "Node token not
        # found", not a real delivery failure. When no node_token is set,
        # the shared auth_token is used, same as the legacy URLs.
        resolved_targets = []  # list of (url, override_token_or_None)
        if ctop_targets:
            hosts = get_ctop_hosts()
            for target in ctop_targets:
                host_id = (target or {}).get("host_id")
                node_path = (target or {}).get("node_path") or ""
                base_url = hosts.get(host_id)
                if not base_url:
                    logging.getLogger(__name__).warning(
                        f"CTOP target for device {device_id} references unknown "
                        f"host_id '{host_id}' (removed from Settings?) — skipping."
                    )
                    continue

                node_token = None
                encrypted_node_token = (target or {}).get("node_token")
                if encrypted_node_token:
                    if self.use_firebase:
                        try:
                            node_token = self.encryption_service.decrypt(
                                encrypted_node_token
                            )
                        except Exception as e:
                            logging.getLogger(__name__).warning(
                                f"Failed to decrypt node_token for device {device_id} "
                                f"target '{host_id}': {e} — falling back to the "
                                f"device's shared auth_token for this target."
                            )
                    else:
                        node_token = encrypted_node_token

                resolved_targets.append((base_url + node_path, node_token))

        shared_auth = as_bearer(auth_token)

        # Send to every configured endpoint: the two legacy slots (shared
        # auth_token) plus any resolved ctop_targets (each with its own
        # token if one was set, else the shared one) — deduped by URL so
        # the same endpoint reachable via two different entries isn't
        # POSTed to twice (first occurrence's auth wins, which is why
        # resolved_targets is listed BEFORE the legacy slots below: if a
        # target's resolved URL happens to collide with ctop_url_1/_2, its
        # own node_token must win the dedup, not the device's generic
        # shared auth_token — losing that would silently reproduce the
        # exact "Node token not found" failure this feature exists to
        # avoid). Overall success below is an OR across all of them: as
        # long as at least one endpoint accepts the data, the device's
        # pipeline advances — a down host never blocks delivery to a
        # working one.
        all_targets = [
            (url, as_bearer(token) or shared_auth) for url, token in resolved_targets
        ] + [(ctop_url_1, shared_auth), (ctop_url_2, shared_auth)]
        seen_urls = set()
        targets = []
        for url, auth_header in all_targets:
            if url and url not in seen_urls:
                seen_urls.add(url)
                targets.append((url, auth_header))
        if not targets:
            return {"error": "No CTOP URL configured"}

        # Extract device name and type for better logging
        device_name = None
        device_type = None
        if device_data:
            device_name = device_data.get("name")
            device_type = device_data.get("device_type")
        elif device:
            device_name = device.name
            device_type = device.device_type

        per_url_results = []
        for url, auth_header in targets:
            headers = {"Content-Type": "application/json"}
            if auth_header:
                headers["Authorization"] = auth_header
            success, response_data, error = self._send_with_retry(
                url, payload, headers, device_id, device_name, device_type
            )
            per_url_results.append(
                {
                    "url": url,
                    "success": success,
                    "response": response_data,
                    "error": error,
                }
            )

        overall_success = any(r["success"] for r in per_url_results)
        failed = [r for r in per_url_results if not r["success"]]

        if failed and overall_success:
            # At least one endpoint took the data, so the pipeline can still
            # advance — but a partial failure must not vanish silently.
            failed_summary = ", ".join(f"{r['url']} ({r['error']})" for r in failed)
            logging.getLogger(__name__).warning(
                f"CTOP SEND [Device {device_id}]: delivered to "
                f"{len(per_url_results) - len(failed)}/{len(per_url_results)} endpoints; "
                f"failed: {failed_summary}"
            )

        first_result = per_url_results[0]
        results = {
            "url": first_result["url"],
            "success": overall_success,
            "response": first_result["response"],
            "error": None if overall_success else failed[0]["error"],
            "results": per_url_results,
        }

        # Update device status locally (handled by the caller via local_device_store)
        # We removed the direct Firebase write here to save quota as per Issue #9 fix
        pass

        return results

    def _send_with_retry(
        self, url, payload, headers, device_id, device_name=None, device_type=None
    ):
        """
        Send request with retry logic
        """
        last_error = None
        last_response_data = None

        for attempt in range(self.max_retries):
            try:
                # Use allow_redirects=True to handle 3xx
                response = self.session.post(
                    url,
                    data=json.dumps(payload),
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                )

                response_data = {
                    "status_code": response.status_code,
                    "body": response.text,
                    "history": [r.status_code for r in response.history],
                }
                last_response_data = response_data

                # Only 2xx is considered success
                is_success = 200 <= response.status_code < 300
                log_error = (
                    None
                    if is_success
                    else f"HTTP {response.status_code}: {response.text}"
                )

                # Log the attempt (accurately, so a 4xx/5xx never reads as "Success")
                self._log_send(
                    device_id,
                    url,
                    response.status_code,
                    str(payload),
                    response.text,
                    log_error,
                    attempt + 1,
                    device_name=device_name,
                    device_type=device_type,
                )

                if is_success:
                    return True, response_data, None

                last_error = f"HTTP {response.status_code}: {response.text}"

                # A 4xx (other than 429, which means "rate limited, slow down
                # and try again") is a permanent failure for this request —
                # e.g. a 404 "node token not found" will not start succeeding
                # by resending the identical payload a moment later. Stop
                # burning attempts and wall-clock time on it.
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    return False, last_response_data, last_error

            except requests.exceptions.Timeout:
                last_error = "Request timeout"
                self._log_send(
                    device_id,
                    url,
                    None,
                    str(payload),
                    None,
                    last_error,
                    attempt + 1,
                    "Timeout",
                    device_name=device_name,
                    device_type=device_type,
                )

            except requests.exceptions.ConnectionError:
                last_error = "Connection error"
                self._log_send(
                    device_id,
                    url,
                    None,
                    str(payload),
                    None,
                    last_error,
                    attempt + 1,
                    "ConnectionError",
                    device_name=device_name,
                    device_type=device_type,
                )

            except requests.exceptions.RequestException as e:
                last_error = f"Request error: {str(e)}"
                self._log_send(
                    device_id,
                    url,
                    None,
                    str(payload),
                    None,
                    last_error,
                    attempt + 1,
                    "RequestException",
                    device_name=device_name,
                    device_type=device_type,
                )

            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay)

        return False, last_response_data, last_error

    def _log_send(
        self,
        device_id,
        endpoint,
        response_code,
        request_payload,
        response_body,
        error_message,
        attempt,
        error_type=None,
        device_name=None,
        device_type=None,
    ):
        """Log CTOP send attempt locally"""
        import logging
        from utils.memory_logger import memory_logger

        logger = logging.getLogger(__name__)

        device_id_str = str(device_id)

        # Add to local memory logger for the web dashboard
        memory_logger.add_log(
            device_id=device_id_str,
            endpoint=endpoint,
            status_code=response_code,
            payload=request_payload,
            error_message=error_message,
            device_name=device_name,
            device_type=device_type,
            attempt=attempt,
        )

        if error_message is None:
            logger.info(
                f"CTOP SEND [Device {device_id_str}]: Successfully sent payload to CTOP (Attempt {attempt}, Code {response_code})"
            )
        else:
            logger.error(
                f"CTOP SEND ERROR [Device {device_id_str}]: {error_message} (Attempt {attempt}, Code {response_code}) - Payload: {request_payload}"
            )

    def send_batch(self, device_id, payloads, device_data=None):
        """
        Send multiple payloads to CTOP endpoints

        Args:
            device_id: ID of the device
            payloads: List of payloads to send
            device_data: Optional device data dict (for Firestore mode)

        Returns:
            dict: Results for each payload
        """
        results = {}

        for i, payload in enumerate(payloads):
            result = self.send_to_ctop(
                device_id, device_data=device_data, payload=payload
            )
            results[f"payload_{i}"] = result

        return results
