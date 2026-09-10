"""
Local Device Store — utils/local_device_store.py
=================================================
Manages a high-speed SQLite mirror (device_mirror.db) that acts as a
free, instant, rate-limit-free cache of Firebase device data.

OPTIMIZED FOR 10,000+ DEVICES:
  - Uses SQLite for persistence (transactional, row-level updates).
  - Uses in-memory dictionary index for O(1) read performance.
  - Periodic background flush to ensure disk sync without blocking.
"""

import json
import os
import threading
import logging
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Path to the local mirror file — sits in the instance folder or project root
_PROJECT_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
_DB_PATH = os.path.join(_PROJECT_ROOT, 'instance', 'device_mirror.db')
if not os.path.exists(os.path.dirname(_DB_PATH)):
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

STORE_SCHEMA_VERSION = 2
STATS_CAP_PER_DEVICE = 1_000_000

# Issue #5 fix: Whitelist of allowed fields that can be updated in local device store
ALLOWED_DEVICE_UPDATE_FIELDS = {
    'name', 'channel_id', 'api_key', 'ctop_url_1', 'ctop_url_2',
    'auth_token', 'latitude', 'longitude', 'is_active', 'device_type',
    'tank_height', 'distance_field', 'temperature_field', 'meter_reading_field',
    'flow_rate_field', 'liters_field', 'tds_field', 'filtering_method',
    'filter_window', 'last_status', 'last_error', 'last_sync_time',
    'last_processed_entry_id',  # Fix #9: was missing, caused silent data loss
    'last_reading_time',        # Timestamp of the latest sensor data point
    'data_source', 'emqx_broker_url', 'emqx_port', 'emqx_username',
    'emqx_password', 'emqx_topic', 'emqx_use_tls',
    'emqx_qos', 'emqx_ca_cert_path', 'emqx_tls_insecure',
    'consecutive_failures', 'needs_attention', 'last_ctop_attempt_time'
}

class LocalDeviceStore:
    """
    Thread-safe manager for the local high-speed SQLite mirror.
    Optimized for high-density IoT scaling (10,000+ devices).
    """

    _instance = None
    _class_lock = threading.Lock()

    def __new__(cls, db_path: str = _DB_PATH):
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: str = _DB_PATH):
        if self._initialized:
            return
        self._initialized = True
        self._db_path = db_path
        self._lock = threading.RLock()
        self._dirty_devices = set()  # Track which device IDs need flushing
        self._dirty_meta = False
        
        # In-memory representation for O(1) reads
        self._devices: Dict[str, Dict] = {}
        self._meta: Dict = {
            'schema_version': STORE_SCHEMA_VERSION,
            'last_synced_from_firebase': None,
            'last_synced_to_firebase': None
        }
        self._stats: Dict[str, Dict] = {}
        self._consecutive_flush_errors: int = 0  # Health monitoring counter
        
        # Initialize SQLite database
        self._init_db()
        # Load from SQLite into memory
        self._load_from_db()
        
        # Start background flush thread
        self._stop_flush_thread = False
        self._flush_thread = threading.Thread(target=self._background_flush_loop, daemon=True)
        self._flush_thread.start()

    def _init_db(self):
        """Create tables if they don't exist."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    device_id TEXT PRIMARY KEY,
                    fetches INTEGER DEFAULT 0,
                    successful_sends INTEGER DEFAULT 0,
                    failed_sends INTEGER DEFAULT 0
                )
            ''')
            conn.commit()

    def _load_from_db(self):
        """Load data from SQLite into memory."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                # Load meta
                cursor = conn.execute('SELECT key, value FROM meta')
                for key, value in cursor:
                    self._meta[key] = value
                
                # Check schema version
                db_version = int(self._meta.get('schema_version', 0))
                if db_version != STORE_SCHEMA_VERSION:
                    logger.warning(f"[LocalStore] DB Schema mismatch (DB:{db_version} != App:{STORE_SCHEMA_VERSION}).")
                
                # Load devices
                cursor = conn.execute('SELECT id, data FROM devices')
                for did, data_json in cursor:
                    self._devices[did] = json.loads(data_json)
                
                # Load stats
                cursor = conn.execute('SELECT device_id, fetches, successful_sends, failed_sends FROM stats')
                for did, f, ss, fs in cursor:
                    self._stats[did] = {
                        'fetches': f,
                        'successful_sends': ss,
                        'failed_sends': fs
                    }
                
                logger.info(f"[LocalStore] Loaded {len(self._devices)} devices from SQLite.")
        except Exception as e:
            logger.error(f"[LocalStore] Failed to load from SQLite: {e}")

    def _background_flush_loop(self):
        import time
        while not self._stop_flush_thread:
            try:
                time.sleep(10)  # Flush every 10 seconds if dirty
                if self._dirty_devices or self._dirty_meta:
                    self.flush()
                    self._consecutive_flush_errors = 0  # Reset on success
            except Exception as e:
                self._consecutive_flush_errors += 1
                logger.error(f"[LocalStore] Background flush error (consecutive: {self._consecutive_flush_errors}): {e}")
                if self._consecutive_flush_errors >= 10:
                    logger.critical(f"[LocalStore] FLUSH FAILING REPEATEDLY ({self._consecutive_flush_errors}x). Check disk/SQLite health!")

    def flush(self):
        """Flush in-memory changes to SQLite."""
        with self._lock:
            if not self._dirty_devices and not self._dirty_meta:
                return
            
            try:
                with sqlite3.connect(self._db_path) as conn:
                    # Flush meta
                    if self._dirty_meta:
                        for k, v in self._meta.items():
                            conn.execute('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', (k, str(v)))
                        self._dirty_meta = False
                    
                    # Flush dirty devices
                    for did in list(self._dirty_devices):
                        device_data = self._devices.get(did)
                        if device_data:
                            # Sanitize data for JSON serialization (handle Firestore timestamps)
                            sanitized_data = self._sanitize_for_json(device_data)
                            conn.execute('INSERT OR REPLACE INTO devices (id, data) VALUES (?, ?)', 
                                         (did, json.dumps(sanitized_data)))
                        else:
                            conn.execute('DELETE FROM devices WHERE id = ?', (did,))
                    
                    # Flush all stats (simpler for now)
                    for did, s in self._stats.items():
                        conn.execute('INSERT OR REPLACE INTO stats (device_id, fetches, successful_sends, failed_sends) VALUES (?, ?, ?, ?)',
                                     (did, s['fetches'], s['successful_sends'], s['failed_sends']))
                    
                    conn.commit()
                    self._dirty_devices.clear()
            except Exception as e:
                logger.error(f"[LocalStore] Flush error: {e}")

    # ------------------------------------------------------------------
    # FIREBASE SYNC
    # ------------------------------------------------------------------

    def sync_from_firebase(self) -> bool:
        """Pull device data from Firebase, merging with local runtime fields."""
        # Local runtime fields that should NOT be overwritten by Firebase
        _LOCAL_RUNTIME_FIELDS = {'last_status', 'last_error', 'last_sync_time'}
        
        try:
            from firebase.firestore_service import FirestoreService
            fs = FirestoreService()
            if not fs.is_initialized(): return False

            logger.info("[LocalStore] Scaling Sync: Pulling batch from Firebase...")
            devices = fs.list_devices(limit=10000) # Support massive scale
            
            with self._lock:
                new_devices = {}
                for device in devices:
                    did = device.get('id')
                    existing = self._devices.get(did)
                    
                    if existing:
                        # Merge: keep local progress if newer
                        if existing.get('last_processed_entry_id'):
                            try:
                                local_id = int(existing.get('last_processed_entry_id', 0))
                                remote_id = int(device.get('last_processed_entry_id', 0))
                                if local_id > remote_id:
                                    device['last_processed_entry_id'] = str(local_id)
                            except: pass
                        
                        # Preserve local runtime fields (they're updated every 15s locally)
                        for field in _LOCAL_RUNTIME_FIELDS:
                            if field in existing:
                                device[field] = existing[field]
                    
                    new_devices[did] = device
                    self._dirty_devices.add(did)
                
                self._devices = new_devices
                self._meta['last_synced_from_firebase'] = datetime.now(timezone.utc).isoformat()
                self._dirty_meta = True
                self.flush()
            return True
        except Exception as e:
            logger.error(f"[LocalStore] Sync from Firebase failed: {e}")
            return False

    def sync_to_firebase(self) -> bool:
        try:
            from firebase.firestore_service import FirestoreService
            fs = FirestoreService()
            if not fs.is_initialized(): return False

            with self._lock:
                devices = list(self._devices.values())
                stats_snapshot = {did: dict(s) for did, s in self._stats.items()}

            if not devices: return True

            batch = fs.get_batch()
            count = 0
            from google.cloud import firestore
            
            for dev in devices:
                did = dev.get('id')
                eid = dev.get('last_processed_entry_id')
                if not did or not eid: continue
                
                payload = {
                    'last_processed_entry_id': str(eid),
                    'last_entry_id_timestamp': datetime.now(timezone.utc).isoformat(),
                    'last_status': dev.get('last_status', 'success'),
                    'last_sync_time': dev.get('last_sync_time'),
                }
                
                s = stats_snapshot.get(did)
                if s:
                    if s['fetches'] > 0: payload['total_fetches'] = firestore.Increment(s['fetches'])
                    if s['successful_sends'] > 0: payload['successful_sends'] = firestore.Increment(s['successful_sends'])
                    if s['failed_sends'] > 0: payload['failed_sends'] = firestore.Increment(s['failed_sends'])
                
                fs.update_device(did, payload, batch=batch)
                count += 1
                if count >= 450:
                    fs.commit_batch(batch)
                    batch = fs.get_batch()
                    count = 0
            
            if count > 0: fs.commit_batch(batch)

            # Clear synced stats
            with self._lock:
                for did, s in stats_snapshot.items():
                    if did in self._stats:
                        self._stats[did]['fetches'] -= s['fetches']
                        self._stats[did]['successful_sends'] -= s['successful_sends']
                        self._stats[did]['failed_sends'] -= s['failed_sends']
                self._meta['last_synced_to_firebase'] = datetime.now(timezone.utc).isoformat()
                self._dirty_meta = True
                self.flush()
            return True
        except Exception as e:
            logger.error(f"[LocalStore] Sync to Firebase failed: {e}")
            return False

    # ------------------------------------------------------------------
    # CRUD OPERATIONS
    # ------------------------------------------------------------------

    def add_device(self, device_data: Dict) -> bool:
        """Add a new device to the local mirror and flush to disk."""
        did = str(device_data.get('id'))
        if not did:
            return False
        
        with self._lock:
            self._devices[did] = device_data
            self._dirty_devices.add(did)
            self.flush() # Ensure persistence for new devices
            return True

    def delete_device(self, device_id: str) -> bool:
        """Remove a device from the local mirror and flush to disk."""
        did = str(device_id)
        with self._lock:
            if did in self._devices:
                del self._devices[did]
                self._dirty_devices.add(did) # flush() handles deletions if ID not in _devices
                if did in self._stats:
                    del self._stats[did]
                self.flush()
                return True
        return False

    # ------------------------------------------------------------------
    # ACCESSORS (O(1) Memory Performance)
    # ------------------------------------------------------------------

    def get_devices(self, active_only: bool = False) -> List[Dict]:
        with self._lock:
            if active_only:
                return [d for d in self._devices.values() if d.get('is_active', True)]
            return list(self._devices.values())

    def get_device_by_id(self, device_id: str) -> Optional[Dict]:
        with self._lock:
            return self._devices.get(str(device_id))

    # NOTE [Flush consistency]: add_device()/delete_device() above flush()
    # synchronously — a device create/delete is rare and its durability
    # matters immediately. update_entry_id()/increment_device_stats()/
    # update_device_fields() below deliberately do NOT: they're called on
    # every scheduler tick for every device (this store docstring's whole
    # "OPTIMIZED FOR 10,000+ DEVICES" design goal), and a synchronous
    # SQLite write per call at that volume would defeat the point of the
    # in-memory index. They rely on _background_flush_loop() picking up
    # `_dirty_devices`/`_dirty_meta` within 10 seconds instead — an
    # intentional durability/throughput tradeoff, not an oversight.
    def update_entry_id(self, device_id: str, entry_id: str, last_status: str = 'success', last_reading_time: Optional[str] = None) -> bool:
        did = str(device_id)
        with self._lock:
            if did in self._devices:
                self._devices[did]['last_processed_entry_id'] = str(entry_id)
                self._devices[did]['last_status'] = last_status
                if last_status in ('success', 'inactive'):
                    self._devices[did]['last_error'] = None
                if last_reading_time is not None:
                    self._devices[did]['last_reading_time'] = last_reading_time
                self._devices[did]['last_sync_time'] = datetime.now(timezone.utc).isoformat()
                self._dirty_devices.add(did)
                return True
        return False

    def increment_device_stats(self, device_id: str, fetch_success: bool = True, send_success: bool = True):
        did = str(device_id)
        with self._lock:
            if did not in self._stats:
                self._stats[did] = {'fetches': 0, 'successful_sends': 0, 'failed_sends': 0}
            
            s = self._stats[did]
            s['fetches'] = min(s['fetches'] + 1, STATS_CAP_PER_DEVICE)
            if send_success: s['successful_sends'] = min(s['successful_sends'] + 1, STATS_CAP_PER_DEVICE)
            else: s['failed_sends'] = min(s['failed_sends'] + 1, STATS_CAP_PER_DEVICE)
            self._dirty_meta = True # Trigger flush for stats

    def update_device_fields(self, device_id: str, updates: Dict) -> bool:
        did = str(device_id)
        with self._lock:
            if did in self._devices:
                for k, v in updates.items():
                    if k in ALLOWED_DEVICE_UPDATE_FIELDS:
                        self._devices[did][k] = v
                if updates.get('last_status') in ('success', 'inactive') and 'last_error' not in updates:
                    self._devices[did]['last_error'] = None
                self._dirty_devices.add(did)
                return True
        return False

    def is_empty(self) -> bool:
        with self._lock: return len(self._devices) == 0

    def get_last_synced(self) -> Optional[str]:
        with self._lock: return self._meta.get('last_synced_from_firebase')

    def _sanitize_for_json(self, data: Dict) -> Dict:
        """Recursively convert non-serializable objects (like Firestore timestamps) to strings."""
        if isinstance(data, dict):
            return {k: self._sanitize_for_json(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_for_json(x) for x in data]
        elif hasattr(data, 'isoformat'): # Handles datetime, DatetimeWithNanoseconds, etc.
            return data.isoformat()
        return data

    def get_telemetry(self) -> Dict:
        """Return internal performance telemetry for the /api/diagnostics endpoint."""
        with self._lock:
            total_devices = len(self._devices)
            total_fetches = sum(s.get('fetches', 0) for s in self._stats.values())
            total_successful_sends = sum(s.get('successful_sends', 0) for s in self._stats.values())
            total_failed_sends = sum(s.get('failed_sends', 0) for s in self._stats.values())
            in_memory_updates = len(self._dirty_devices)
            disk_flushes = 0  # Approximation: tracked via flush calls

            return {
                'total_devices': total_devices,
                'total_fetches': total_fetches,
                'successful_sends': total_successful_sends,
                'failed_sends': total_failed_sends,
                'in_memory_updates': in_memory_updates,
                'disk_flushes': disk_flushes,
                'consecutive_flush_errors': self._consecutive_flush_errors,
                'last_synced_from_firebase': self._meta.get('last_synced_from_firebase'),
                'last_synced_to_firebase': self._meta.get('last_synced_to_firebase'),
                'schema_version': STORE_SCHEMA_VERSION
            }

# Singleton instance exported for project-wide use
local_device_store = LocalDeviceStore()
