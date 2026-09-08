import threading
from collections import deque
from datetime import datetime
import json

class MemoryLogger:
    """Thread-safe in-memory logger to store the most recent log entries"""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MemoryLogger, cls).__new__(cls)
                # Fixed-capacity ring buffer: O(1) push + automatic eviction
                # of the oldest entry, instead of list.insert(0, ...) doing
                # an O(n) shift on every single log line plus a manual trim.
                cls._instance._logs = deque(maxlen=100)
        return cls._instance

    def add_log(self, device_id, endpoint, status_code, payload, error_message=None, device_name=None, device_type=None, attempt=1):
        """Add a new log entry to the in-memory list"""
        log_entry = {
            'id': f"log_{int(datetime.now().timestamp() * 1000)}",
            'device_id': str(device_id),
            'device_name': device_name or str(device_id),
            'device_type': device_type or 'Unknown',
            'log_type': 'ctop_send',
            'endpoint': endpoint,
            'response_code': status_code,
            'request_payload': str(payload),
            'status': 'success' if not error_message else 'error',
            'message': f'Attempt {attempt}: Success' if not error_message else str(error_message),
            'created_at': datetime.utcnow().isoformat() + 'Z'
        }

        with self._lock:
            self._logs.appendleft(log_entry)

    def get_logs(self, limit=10, device_id=None):
        """Retrieve recent logs, optionally filtered by device"""
        with self._lock:
            if device_id:
                filtered = [log for log in self._logs if log.get('device_id') == str(device_id)]
                return filtered[:limit]
            return list(self._logs)[:limit]
            
    def get_stats(self):
        """Get summary statistics for the dashboard"""
        with self._lock:
            total = len(self._logs)
            errors = sum(1 for log in self._logs if log.get('status') == 'error')
            return {
                'total_logs': total,
                'error_logs': errors
            }

# Global singleton instance
memory_logger = MemoryLogger()
