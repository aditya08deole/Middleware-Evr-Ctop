import threading
from collections import deque
from datetime import datetime

# Per-device cap. Previously this was a SINGLE global deque(maxlen=100)
# shared by every device — on a fleet where a couple of devices are stuck
# retrying CTOP every few minutes (backoff) while most others are healthy,
# those few noisy devices alone fill all 100 slots, so: (a) a healthy
# device's own recent successes get pushed out and its "log history" looks
# empty or stale, and (b) the global error count looks permanently high/
# rising even though it's really just 1-2 devices, not the whole fleet.
# Per-device buffers fix both: each device's own history only evicts its
# own old entries.
PER_DEVICE_CAP = 30

# Soft guard against unbounded growth of the outer dict if garbage/one-off
# device_ids ever flow through (e.g. a typo'd ID during testing). Not a
# correctness mechanism for the 10s-of-devices fleet this runs today.
MAX_TRACKED_DEVICES = 1000


class MemoryLogger:
    """Thread-safe in-memory logger: one small ring buffer per device.

    A device that is currently succeeding should not keep showing old
    error entries from before it recovered — see add_log()'s clear-on-
    success behavior below. This is pure in-memory state; it never reads
    or writes Firestore, by design (CTOP-send volume is far too high to
    log per-attempt to Firestore without burning quota for no benefit).
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MemoryLogger, cls).__new__(cls)
                cls._instance._logs_by_device = {}
        return cls._instance

    def add_log(
        self,
        device_id,
        endpoint,
        status_code,
        payload,
        error_message=None,
        device_name=None,
        device_type=None,
        attempt=1,
    ):
        """Add a new log entry to this device's in-memory ring buffer.

        On a SUCCESS, any error entries already queued for this device are
        dropped first — a device that just recovered shouldn't keep
        reporting the failures that preceded the fix moments ago.
        """
        did = str(device_id)

        log_entry = {
            "id": f"log_{int(datetime.now().timestamp() * 1000)}",
            "device_id": did,
            "device_name": device_name or did,
            "device_type": device_type or "Unknown",
            "log_type": "ctop_send",
            "endpoint": endpoint,
            "response_code": status_code,
            "request_payload": str(payload),
            "status": "success" if not error_message else "error",
            "message": (
                f"Attempt {attempt}: Success"
                if not error_message
                else str(error_message)
            ),
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        with self._lock:
            bucket = self._logs_by_device.get(did)
            if bucket is None:
                if len(self._logs_by_device) >= MAX_TRACKED_DEVICES:
                    # Evict the least-recently-touched device's bucket
                    # rather than growing forever.
                    oldest_id = next(iter(self._logs_by_device))
                    self._logs_by_device.pop(oldest_id, None)
                bucket = deque(maxlen=PER_DEVICE_CAP)
                self._logs_by_device[did] = bucket
            elif not error_message:
                # Success: clear this device's stale error history instead
                # of letting it sit alongside (and keep being counted in)
                # new successes until it ages out on its own.
                surviving = [e for e in bucket if e.get("status") != "error"]
                bucket.clear()
                bucket.extend(surviving)

            bucket.appendleft(log_entry)
            # Re-insert so dict iteration order reflects recency (used by
            # the MAX_TRACKED_DEVICES eviction above).
            self._logs_by_device[did] = self._logs_by_device.pop(did)

    def get_logs(self, limit=10, device_id=None):
        """Retrieve recent logs, optionally filtered by device."""
        with self._lock:
            if device_id:
                bucket = self._logs_by_device.get(str(device_id))
                return list(bucket)[:limit] if bucket else []

            # No device filter: merge every device's buffer and return the
            # most recent `limit` entries fleet-wide, newest first.
            merged = []
            for bucket in self._logs_by_device.values():
                merged.extend(bucket)
            merged.sort(key=lambda e: e.get("created_at", ""), reverse=True)
            return merged[:limit]

    def get_stats(self, device_id=None):
        """Summary statistics for the dashboard. Scoped to one device when
        device_id is given, otherwise fleet-wide across all tracked devices."""
        with self._lock:
            if device_id:
                bucket = self._logs_by_device.get(str(device_id)) or []
                total = len(bucket)
                errors = sum(1 for e in bucket if e.get("status") == "error")
                return {"total_logs": total, "error_logs": errors}

            total = 0
            errors = 0
            for bucket in self._logs_by_device.values():
                total += len(bucket)
                errors += sum(1 for e in bucket if e.get("status") == "error")
            return {"total_logs": total, "error_logs": errors}

    def clear_device(self, device_id):
        """Drop all stored log entries for a device (e.g. on delete)."""
        with self._lock:
            self._logs_by_device.pop(str(device_id), None)


# Global singleton instance
memory_logger = MemoryLogger()
