import threading
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
                cls._instance._logs = []
                cls._instance._max_logs = 100 # Internal limit of 100 to support 99+ logic
        return cls._instance
        
    def add_log(self, device_id, endpoint, status_code, payload, error_message=None, device_name=None, device_type=None):
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
            'message': 'Attempt 1: Success' if not error_message else str(error_message),
            'created_at': datetime.utcnow().isoformat() + 'Z'
        }
        
        with self._lock:
            self._logs.insert(0, log_entry)
            # Keep only the most recent entries
            if len(self._logs) > self._max_logs:
                self._logs = self._logs[:self._max_logs]
                
    def get_logs(self, limit=10, device_id=None):
        """Retrieve recent logs, optionally filtered by device"""
        with self._lock:
            if device_id:
                filtered = [log for log in self._logs if log.get('device_id') == str(device_id)]
                return filtered[:limit]
            return list(self._logs[:limit])
            
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
