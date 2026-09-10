import requests
import time
from urllib3.util.retry import Retry
from models import db, Device
from config import Config
import os


class ThingSpeakService:
    """Service for fetching data from ThingSpeak API"""

    FETCH_MAX_ATTEMPTS = 2
    FETCH_RETRY_DELAY = 1  # seconds

    def __init__(self):
        self.base_url = Config.THINGSPEAK_BASE_URL
        self.timeout = Config.THINGSPEAK_TIMEOUT
        self.use_firebase = os.environ.get("USE_FIREBASE", "false").lower() == "true"

        if self.use_firebase:
            from utils.encryption import get_encryption_service

            self.encryption_service = get_encryption_service()

        # Phase 2 Optimization: Persistent HTTP Session for connection pooling
        self.session = requests.Session()
        # Transport-level retry: evict a pooled keep-alive connection the
        # server already closed (same fix as ctop_service.py) instead of
        # failing outright on the first reused-but-dead connection.
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

        # Per-device duplicate tracking: {device_id: last_processed_entry_id}
        self.device_last_entry_id = {}

    def fetch_data(self, device_id, device_data=None):
        """
        Fetch the latest data from ThingSpeak for a specific device.
        Includes duplicate detection to ensure only NEW data is processed.

        Args:
            device_id: ID of the device to fetch data for
            device_data: Optional device data dict (for Firestore mode)

        Returns:
            tuple: (success: bool, data: dict or None, error: str or None)
        """
        # Resolve device credentials
        if device_data:
            device = device_data
            channel_id = device.get("channel_id")
            api_key = device.get("api_key")
            device_id_key = str(device.get("id", device_id))  # For duplicate tracking
        else:
            # SQLite fallback
            device = db.session.get(Device, device_id)
            if not device:
                return False, None, "Device not found"
            channel_id = device.channel_id
            api_key = device.api_key
            device_id_key = str(device_id)

        # Decrypt API key if using Firebase
        if self.use_firebase and api_key:
            try:
                api_key = self.encryption_service.decrypt(api_key)
            except Exception as e:
                return False, None, f"Failed to decrypt API key: {str(e)}"

        url = f"{self.base_url}/channels/{channel_id}/feeds.json"
        params = {
            "api_key": api_key,
            "results": 1,  # only fetch the latest 1 entry to prevent CTOP burst
        }

        # App-level retry, mirroring ctop_service.py's send-side resilience:
        # a transient DNS blip or read-timeout shouldn't fail the whole tick
        # for this device when a second attempt a moment later would work.
        last_error = None
        for attempt in range(self.FETCH_MAX_ATTEMPTS):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()

                data = response.json()

                # DUPLICATE DETECTION:
                # In Firestore mode, do NOT filter here. The scheduler's own dedup
                # (utils/scheduler_firestore.py, Step 4) is the single source of
                # truth for what's new — it compares against last_processed_entry_id,
                # which is only advanced after a CTOP send actually succeeds. This
                # in-memory tracker used to advance unconditionally at fetch time,
                # so any entry whose CTOP send failed was marked "seen" here and
                # silently never re-fetched again, permanently dropping it even
                # though the scheduler correctly intended to retry it.
                if device_data:
                    filtered_data = data
                else:
                    filtered_data = self._filter_duplicate_entries(device_id_key, data)
                    self._log_fetch(
                        device, url, response.status_code, filtered_data, None
                    )

                return True, filtered_data, None

            except requests.exceptions.RequestException as e:
                last_error = str(e)
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if not device_data:
                    self._log_fetch(
                        device, url, status_code, None, last_error, type(e).__name__
                    )

                # A 4xx from ThingSpeak (bad channel/API key) won't fix itself
                # by retrying the identical request — stop immediately.
                if status_code is not None and 400 <= status_code < 500:
                    return False, None, last_error

                if attempt < self.FETCH_MAX_ATTEMPTS - 1:
                    time.sleep(self.FETCH_RETRY_DELAY)

        return False, None, last_error

    def _log_fetch(
        self,
        device,
        endpoint,
        response_code,
        response_data,
        error_message,
        error_type=None,
    ):
        """Log ThingSpeak fetch attempt locally"""
        import logging

        logger = logging.getLogger(__name__)

        device_id = (
            str(device.get("id", "unknown"))
            if isinstance(device, dict)
            else str(getattr(device, "id", "unknown"))
        )

        if error_message is None:
            logger.info(
                f"FETCH [Device {device_id}]: Successfully fetched latest data from ThingSpeak."
            )
        else:
            logger.error(
                f"FETCH ERROR [Device {device_id}]: {error_message} (Code: {response_code})"
            )

    def _filter_duplicate_entries(self, device_id, data):
        """
        Filter out entries that have already been processed for this device.
        Uses in-memory tracking (works for both SQLite and Firestore modes).
        Prevents duplicate sends to CTOP.

        Args:
            device_id: Device ID (as string for tracking)
            data: ThingSpeak API response data

        Returns:
            dict: Filtered data with only NEW entries (entry_ids not yet sent)
        """
        if not data or "feeds" not in data:
            return data

        # Get last processed entry_id from in-memory tracker
        last_processed_entry_id = self.device_last_entry_id.get(device_id)

        # Filter feeds using <= comparison to skip all old entries
        filtered_feeds = []
        latest_entry_id = last_processed_entry_id

        for feed in data.get("feeds", []):
            entry_id = str(feed.get("entry_id", ""))

            if not entry_id:
                continue

            # Skip if this entry was already processed (use <= to catch all old entries)
            if last_processed_entry_id:
                try:
                    if int(entry_id) <= int(last_processed_entry_id):
                        logger = self._get_logger()
                        logger.debug(
                            f"Device {device_id}: Skipping old entry_id {entry_id} (<= {last_processed_entry_id})"
                        )
                        continue
                except (ValueError, TypeError):
                    # Non-numeric entry_ids: fall back to string equality
                    if entry_id == last_processed_entry_id:
                        continue

            filtered_feeds.append(feed)

            # Track the highest entry_id we've seen
            try:
                if latest_entry_id is None or int(entry_id) > int(latest_entry_id):
                    latest_entry_id = entry_id
            except (ValueError, TypeError):
                latest_entry_id = entry_id

        # Update in-memory tracker with the latest entry_id
        if latest_entry_id:
            self.device_last_entry_id[device_id] = latest_entry_id

        # Return data with filtered feeds
        return {"channel": data.get("channel"), "feeds": filtered_feeds}

    def _get_logger(self):
        """Get logger instance"""
        import logging

        return logging.getLogger(__name__)
