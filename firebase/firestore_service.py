import firebase_admin
from firebase_admin import credentials, firestore
from typing import List, Dict, Optional, Any
from datetime import datetime
import os
import json

class FirestoreService:
    """Service for Firestore database operations"""
    
    _instance = None
    _db = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FirestoreService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize Firebase Admin SDK (once)"""
        if self._initialized:
            return
        
        self._initialized = True
        
        credentials_path = os.environ.get('FIREBASE_CREDENTIALS_PATH')
        if not credentials_path or not os.path.exists(credentials_path):
            if not hasattr(self, '_warned'):
                print("WARNING: Firebase credentials not found. Set FIREBASE_CREDENTIALS_PATH to use Firestore.")
                self._warned = True
            self._db = None
            return
            
        try:
            # Check if Firebase is already initialized
            if not firebase_admin._apps:
                cred = credentials.Certificate(credentials_path)
                firebase_admin.initialize_app(cred)
            
            self._db = firestore.client()
            print("Firestore initialized successfully")
            
        except Exception as e:
            print(f"Error initializing Firestore: {str(e)}")
            self._db = None
    
    @property
    def db(self):
        """Get Firestore client"""
        return self._db
    
    def is_initialized(self) -> bool:
        """Check if Firestore is initialized"""
        return self._db is not None
    
    # Device operations
    def create_device(self, device_data: Dict) -> str:
        """Create a new device"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        device_ref = self._db.collection('devices').document()
        device_data['created_at'] = firestore.SERVER_TIMESTAMP
        device_data['updated_at'] = firestore.SERVER_TIMESTAMP
        device_data['total_fetches'] = 0
        device_data['successful_sends'] = 0
        device_data['failed_sends'] = 0
        device_ref.set(device_data)
        return device_ref.id
    
    def get_device(self, device_id: str) -> Optional[Dict]:
        """Get a device by ID"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        device_ref = self._db.collection('devices').document(device_id)
        doc = device_ref.get()
        return doc.to_dict() if doc.exists else None
    
    # Simple class-level cache to reduce read costs
    _devices_cache = {'data': None, 'timestamp': 0}
    CACHE_TTL_SECONDS = 60

    def list_devices(self, filters: Dict = None, limit: int = 100) -> List[Dict]:
        """List devices with optional filters, using a short-lived cache"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
            
        import time
        current_time = time.time()
        
        # Determine if we can use cache (only if no custom filters/limits are strictly required, 
        # or we cache the general list and filter in memory. Let's cache the unfiltered limit=100 list)
        is_cacheable = filters == {'is_active': True} or not filters
        if is_cacheable and self._devices_cache['data'] is not None:
            if current_time - self._devices_cache['timestamp'] < self.CACHE_TTL_SECONDS:
                cached_devices = self._devices_cache['data']
                if filters and 'is_active' in filters:
                    return [d for d in cached_devices if d.get('is_active') == filters['is_active']]
                return cached_devices
        
        query = self._db.collection('devices')
        
        if filters:
            if 'is_active' in filters:
                query = query.where('is_active', '==', filters['is_active'])
            if 'created_by' in filters:
                query = query.where('created_by', '==', filters['created_by'])
        
        # Removed order_by('created_at') to avoid requiring a composite index.
        # We can sort in memory if needed, but for fetch-all and UI we usually just need the list.
        query = query.limit(limit)
        
        try:
            docs = query.stream()
            devices = []
            for doc in docs:
                device = doc.to_dict()
                device['id'] = doc.id
                devices.append(device)
            
            # Sort in memory by created_at descending
            devices.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            
            # Update cache if it was a generic query
            if not filters:
                self._devices_cache['data'] = devices
                self._devices_cache['timestamp'] = current_time
                
            return devices

        except Exception as e:
            if "index" in str(e).lower():
                print(f"FAILED: Firestore index required. Please create it: {str(e)}")
            raise e
    
    def update_device(self, device_id: str, updates: Dict, batch=None) -> bool:
        """Update a device, optionally using a batch"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        device_ref = self._db.collection('devices').document(device_id)
        updates['updated_at'] = firestore.SERVER_TIMESTAMP
        
        if batch:
            batch.update(device_ref, updates)
        else:
            device_ref.update(updates)
        
        # Invalidate cache
        self._devices_cache['data'] = None
        return True

    def get_batch(self):
        """Create a new write batch"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        return self._db.batch()

    def commit_batch(self, batch):
        """Commit a write batch"""
        if not self.is_initialized():
            return False
        batch.commit()
        return True
    
    def delete_device(self, device_id: str) -> bool:
        """Delete a device"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        self._db.collection('devices').document(device_id).delete()
        return True
    
    def increment_device_stats(self, device_id: str, fetch_count: int = 1, success_count: int = 0, fail_count: int = 0):
        """Increment device statistics by specific amounts (for batch syncing)"""
        if not self.is_initialized():
            return
        
        updates = {
            'total_fetches': firestore.Increment(fetch_count),
            'updated_at': firestore.SERVER_TIMESTAMP
        }
        
        if success_count > 0:
            updates['successful_sends'] = firestore.Increment(success_count)
        
        if fail_count > 0:
            updates['failed_sends'] = firestore.Increment(fail_count)
        
        self.update_device(device_id, updates)
    
    # Log operations
    def create_log(self, log_data: Dict) -> str:
        """Create a log entry"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        log_ref = self._db.collection('logs').document()
        log_data['created_at'] = firestore.SERVER_TIMESTAMP
        log_data['date'] = datetime.now().strftime('%Y-%m-%d')
        log_data['hour'] = datetime.now().hour
        log_ref.set(log_data)
        return log_ref.id
    
    def get_logs(self, device_id: str = None, log_type: str = None, limit: int = 100) -> List[Dict]:
        """Get logs with optional filtering"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        query = self._db.collection('logs')
        
        if device_id:
            query = query.where('device_id', '==', device_id)
        
        if log_type:
            query = query.where('log_type', '==', log_type)
        
        query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
        query = query.limit(limit)
        
        docs = query.stream()
        logs = []
        for doc in docs:
            log_data = doc.to_dict()
            log_data['id'] = doc.id
            logs.append(log_data)
        
        return logs
    
    # Processed data operations
    def create_processed_data(self, data: Dict) -> str:
        """Create processed data entry"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        data_ref = self._db.collection('processed_data').document()
        data['created_at'] = firestore.SERVER_TIMESTAMP
        data['updated_at'] = firestore.SERVER_TIMESTAMP
        data_ref.set(data)
        return data_ref.id
    
    def get_processed_data(self, device_id: str = None, limit: int = 100) -> List[Dict]:
        """Get processed data"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        query = self._db.collection('processed_data')
        
        if device_id:
            query = query.where('device_id', '==', device_id)
        
        query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
        query = query.limit(limit)
        
        docs = query.stream()
        data_list = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            data_list.append(data)
        
        return data_list
    
    # User operations
    def create_user(self, user_data: Dict) -> str:
        """Create a user document"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        user_ref = self._db.collection('users').document(user_data['id'])
        user_data['created_at'] = firestore.SERVER_TIMESTAMP
        user_data['last_login'] = firestore.SERVER_TIMESTAMP
        user_data['is_active'] = True
        user_ref.set(user_data)
        return user_ref.id
    
    def get_user(self, user_id: str) -> Optional[Dict]:
        """Get user by ID"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        user_ref = self._db.collection('users').document(user_id)
        doc = user_ref.get()
        return doc.to_dict() if doc.exists else None
    
    def update_user(self, user_id: str, updates: Dict) -> bool:
        """Update user"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        user_ref = self._db.collection('users').document(user_id)
        user_ref.update(updates)
        return True
    
    # Settings operations
    def get_settings(self) -> Dict:
        """Get system settings"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        settings_ref = self._db.collection('settings').document('global_settings')
        doc = settings_ref.get()
        return doc.to_dict() if doc.exists else {}
    
    def update_settings(self, settings: Dict) -> bool:
        """Update system settings"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")

        settings_ref = self._db.collection('settings').document('global_settings')
        settings['updated_at'] = firestore.SERVER_TIMESTAMP
        settings_ref.set(settings, merge=True)
        return True

    def create_or_update_settings(self, doc_id: str, settings: Dict) -> bool:
        """Create or update a named settings document (e.g. 'scheduler',
        'notifications'), separate from the single 'global_settings' doc
        used by get_settings()/update_settings() above."""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")

        settings_ref = self._db.collection('settings').document(doc_id)
        settings = dict(settings)
        settings['updated_at'] = firestore.SERVER_TIMESTAMP
        settings_ref.set(settings, merge=True)
        return True
    
    # Real-time listener
    def on_devices_change(self, callback):
        """Listen to device changes in real-time"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        def on_snapshot(col_snapshot, changes, read_time):
            callback(col_snapshot, changes, read_time)
        
        self._db.collection('devices').on_snapshot(on_snapshot)
    
    # Statistics
    def get_stats(self) -> Dict:
        """Get system statistics using efficient count queries"""
        if not self.is_initialized():
            raise Exception("Firestore not initialized")
        
        try:
            # Use count() for efficiency if supported by SDK, otherwise fallback to stream()
            total_devices = self._db.collection('devices').count().get()[0][0].value
            active_devices = self._db.collection('devices').where('is_active', '==', True).count().get()[0][0].value
            
            total_logs = self._db.collection('logs').count().get()[0][0].value
            error_logs = self._db.collection('logs').where('status', '==', 'error').count().get()[0][0].value
            
            total_processed = self._db.collection('processed_data').count().get()[0][0].value
            
            return {
                'total_devices': total_devices,
                'active_devices': active_devices,
                'total_logs': total_logs,
                'error_logs': error_logs,
                'total_processed': total_processed
            }
        except Exception:
            # Fallback for older SDKs or index issues
            devices = self.list_devices(limit=1000)
            total_devices = len(devices)
            active_devices = len([d for d in devices if d.get('is_active', False)])
            
            logs = self.get_logs(limit=1000)
            total_logs = len(logs)
            error_logs = len([l for l in logs if l.get('status') == 'error'])
            
            processed_data = self.get_processed_data(limit=1000)
            total_processed = len(processed_data)
            
            return {
                'total_devices': total_devices,
                'active_devices': active_devices,
                'total_logs': total_logs,
                'error_logs': error_logs,
                'total_processed': total_processed
            }
