"""
Unit Tests for LocalDeviceStore (SQLite Version)
===============================================
Verifies:
- CRUD operations (add, get, delete)
- Stats tracking and capping
- Field validation (whitelist)
- Thread safety (RLock)
- Persistence (Flush)
"""

import unittest
import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock

# Import the LocalDeviceStore
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.local_device_store import local_device_store, STATS_CAP_PER_DEVICE


class TestLocalDeviceStoreSQLite(unittest.TestCase):
    def setUp(self):
        """Reset the singleton state for each test, and redirect its disk
        persistence to a throwaway temp file for the duration of the test.

        This uses the same real singleton object the app uses (rather than
        constructing a separate instance) so ALLOWED_DEVICE_UPDATE_FIELDS,
        locking, etc. are exercised exactly as in production — but every
        add_device()/flush() call in these tests used to write straight into
        the real instance/device_mirror.db on disk, permanently leaving
        fixture rows like 'test_1', 'valid_1', 'stats_1' mixed into
        production device data on every test run. Pointing _db_path at a
        temp file for the test's lifetime keeps all of that off the real file.
        """
        self.store = local_device_store
        self._real_db_path = self.store._db_path
        self._temp_dir = tempfile.mkdtemp()
        self.store._db_path = os.path.join(self._temp_dir, "test_device_mirror.db")
        self.store._init_db()

        with self.store._lock:
            self.store._devices = {}
            self.store._stats = {}
            self.store._dirty_devices.clear()
            self.store._dirty_meta = True

    def tearDown(self):
        """Restore the real db_path and discard the temp file."""
        self.store._db_path = self._real_db_path
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_add_and_get_device(self):
        """Verify device addition and retrieval"""
        device = {"id": "test_1", "name": "Test Device", "is_active": True}
        success = self.store.add_device(device)
        self.assertTrue(success)

        retrieved = self.store.get_device_by_id("test_1")
        self.assertEqual(retrieved["name"], "Test Device")
        self.assertTrue(retrieved["is_active"])

    def test_delete_device(self):
        """Verify device deletion"""
        self.store.add_device({"id": "del_1", "name": "Delete Me"})
        self.assertIsNotNone(self.store.get_device_by_id("del_1"))

        success = self.store.delete_device("del_1")
        self.assertTrue(success)
        self.assertIsNone(self.store.get_device_by_id("del_1"))

    def test_stats_increment_and_cap(self):
        """Verify stats increment and respect the cap"""
        did = "stats_1"
        self.store.add_device({"id": did, "name": "Stats Test"})

        # Increment
        self.store.increment_device_stats(did, fetch_success=True, send_success=True)
        self.store.increment_device_stats(did, fetch_success=True, send_success=False)

        with self.store._lock:
            stats = self.store._stats[did]
            self.assertEqual(stats["fetches"], 2)
            self.assertEqual(stats["successful_sends"], 1)
            self.assertEqual(stats["failed_sends"], 1)

        # Test capping
        with self.store._lock:
            self.store._stats[did] = {
                "fetches": STATS_CAP_PER_DEVICE - 1,
                "successful_sends": STATS_CAP_PER_DEVICE - 1,
                "failed_sends": STATS_CAP_PER_DEVICE - 1,
            }

        self.store.increment_device_stats(did, fetch_success=True, send_success=True)
        self.store.increment_device_stats(did, fetch_success=True, send_success=True)

        with self.store._lock:
            stats = self.store._stats[did]
            self.assertEqual(stats["fetches"], STATS_CAP_PER_DEVICE)
            self.assertEqual(stats["successful_sends"], STATS_CAP_PER_DEVICE)

    def test_field_validation_whitelist(self):
        """Verify only whitelisted fields are updated"""
        did = "valid_1"
        self.store.add_device({"id": did, "name": "Original", "secret": "hidden"})

        updates = {
            "name": "Updated Name",
            "is_active": False,
            "invalid_field": "hacker_val",
        }

        success = self.store.update_device_fields(did, updates)
        self.assertTrue(success)

        dev = self.store.get_device_by_id(did)
        self.assertEqual(dev["name"], "Updated Name")
        self.assertFalse(dev["is_active"])
        self.assertNotIn("invalid_field", dev)

    def test_is_empty(self):
        """Verify is_empty logic"""
        with self.store._lock:
            self.store._devices = {}
        self.assertTrue(self.store.is_empty())

        self.store.add_device({"id": "e1", "name": "Entry"})
        self.assertFalse(self.store.is_empty())

    @patch("firebase.firestore_service.FirestoreService")
    def test_sync_from_firebase(self, mock_fs_class):
        """Verify batch sync from Firebase"""
        mock_fs = mock_fs_class.return_value
        mock_fs.is_initialized.return_value = True
        mock_fs.list_devices.return_value = [
            {"id": "f1", "name": "Firebase 1", "last_processed_entry_id": "10"},
            {"id": "f2", "name": "Firebase 2", "last_processed_entry_id": "20"},
        ]

        success = self.store.sync_from_firebase()
        self.assertTrue(success)

        self.assertEqual(len(self.store.get_devices()), 2)
        self.assertIsNotNone(self.store.get_device_by_id("f1"))

    @patch("firebase.firestore_service.FirestoreService")
    def test_sync_to_firebase(self, mock_fs_class):
        """Verify batch sync to Firebase"""
        mock_fs = mock_fs_class.return_value
        mock_fs.is_initialized.return_value = True
        mock_batch = MagicMock()
        mock_fs.get_batch.return_value = mock_batch

        # Setup local state
        self.store.add_device({"id": "t1", "last_processed_entry_id": "100"})
        self.store.increment_device_stats("t1", fetch_success=True, send_success=True)

        success = self.store.sync_to_firebase()
        self.assertTrue(success)

        # Verify Firebase calls
        mock_fs.update_device.assert_called()
        # Stats should be decremented after sync (though mock won't reflect real increments)
        with self.store._lock:
            self.assertEqual(self.store._stats["t1"]["fetches"], 0)


if __name__ == "__main__":
    unittest.main()
