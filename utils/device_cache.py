import threading
import logging
from datetime import datetime
from utils.local_device_store import local_device_store

logger = logging.getLogger(__name__)

class DeviceCache:
    """
    Bridge class that maintains the existing DeviceCache interface
    but pulls data from the persistent LocalDeviceStore instead of Firestore.
    This ensures all existing code works without modification while 
    benefiting from the new local mirror architecture.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DeviceCache, cls).__new__(cls)
                cls._instance._last_update = None
                cls._instance._refresh_interval = 300  # 5 minutes
        return cls._instance
    
    def get_devices(self, force_refresh=False, active_only=True):
        """
        Get devices from the local store.
        If force_refresh is True, it triggers a sync from Firebase to the local store.

        active_only defaults to True to preserve existing behavior for the
        scheduler (which must never poll/post to a disabled device) — pass
        active_only=False for UI/reporting callers that need to show
        disabled devices too (e.g. the dashboard), rather than having them
        silently disappear instead of showing as disabled.
        """
        if force_refresh:
            self.refresh()

        # Always return what's in the local mirror (which is already in memory)
        return local_device_store.get_devices(active_only=active_only)
    
    def refresh(self):
        """
        Force the local store to sync from Firebase.
        This is a 'heavy' operation (1 Firebase Read) and should be used sparingly.
        """
        logger.info("DeviceCache: Forcing sync from Firebase to Local Mirror...")
        success = local_device_store.sync_from_firebase()
        if success:
            self._last_update = datetime.now()
        return success
            
    def get_device_by_id(self, device_id):
        """Get a specific device from the local store"""
        return local_device_store.get_device_by_id(device_id)
    
    def update_device_cache(self, device_id, updates):
        """
        Update a specific device in the local store memory and disk.
        This does NOT write to Firebase.
        """
        return local_device_store.update_device_fields(device_id, updates)

# Global singleton instance for backward compatibility
device_cache = DeviceCache()
