"""
In-memory, per-device rolling history of processed sensor readings — used
only to feed the small trend sparkline on device_detail.html.

Deliberately NOT persisted to disk. This is a visualization nicety, not
data that needs to survive a restart: every value here already flowed
through preprocess_service.transform_to_ctop_format() and (assuming
success) was sent to CTOP, which is the actual system of record. Losing
this history on a process restart loses nothing that matters — the
sparkline just starts empty and refills as new readings arrive. Bounded
per-device so memory can't grow unbounded regardless of device count or
uptime.
"""

import threading
from collections import deque


class ReadingHistoryStore:
    MAX_POINTS_PER_DEVICE = 30

    def __init__(self):
        self._history = {}
        self._last_recorded_entry_id = {}
        self._lock = threading.Lock()

    def record(self, device_id, entry_id, timestamp, values):
        """
        Args:
            device_id: device identifier
            entry_id: the reading's entry_id (from processed_data), used to
                deduplicate — process_device() re-processes the same "new"
                entry on every scheduler tick until a CTOP send for it
                actually succeeds (so it can keep retrying), which would
                otherwise record the same physical reading as a fresh
                sparkline point on every tick a failing device is retried.
                Pass None to skip dedup (always records).
            timestamp: ISO 8601 string (or None) for this reading
            values: dict of {metric_name: value} — typically the CTOP
                payload dict itself (e.g. {'water_level': 61.2,
                'temperature': 29.7}). Only numeric entries are kept.
        """
        numeric_values = {k: v for k, v in (values or {}).items() if isinstance(v, (int, float))}
        if not numeric_values:
            return

        did = str(device_id)
        entry_key = str(entry_id) if entry_id is not None else None

        with self._lock:
            if entry_key is not None and self._last_recorded_entry_id.get(did) == entry_key:
                return

            if did not in self._history:
                self._history[did] = deque(maxlen=self.MAX_POINTS_PER_DEVICE)
            self._history[did].append({'t': timestamp, **numeric_values})
            if entry_key is not None:
                self._last_recorded_entry_id[did] = entry_key

    def get_history(self, device_id):
        with self._lock:
            return list(self._history.get(str(device_id), []))


# Singleton instance exported for project-wide use
reading_history_store = ReadingHistoryStore()
