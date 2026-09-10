import sys
import os
import time
import logging
from datetime import datetime, timezone
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.local_device_store import LocalDeviceStore
from utils.scheduler_firestore import process_device

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s"
)
logger = logging.getLogger(__name__)


def simulate_full_sync(device_count=500):
    """
    Stress test the system by simulating a large fleet processing data in parallel.
    """
    logger.info(f"--- STARTING STRESS TEST: {device_count} DEVICES ---")
    store = LocalDeviceStore()

    # 1. Setup Simulated Devices
    logger.info("Setting up simulated devices in SQLite...")
    devices_to_process = []
    for i in range(device_count):
        did = f"stress_dev_{i}"
        device_data = {
            "id": did,
            "name": f"Stress Device {i}",
            "channel_id": str(2000000 + i),
            "api_key": "STRESS_KEY",
            "ctop_url_1": "http://localhost:8080/api/v1",
            "auth_token": "STRESS_TOKEN",
            "is_active": True,
            "device_type": "EvaraTank",
            "tank_height": 200.0,
            "distance_field": "field1",
            "temperature_field": "field2",
            "last_processed_entry_id": "100",
        }
        with store._lock:
            store._devices[did] = device_data
            store._dirty_devices.add(did)
        devices_to_process.append(device_data)

    store.flush()

    # 2. Mock Services to simulate high-speed network responses
    with patch(
        "services.thingspeak_service.ThingSpeakService.fetch_data"
    ) as mock_fetch, patch(
        "services.ctop_service.CTOPService.send_to_ctop"
    ) as mock_send:

        # Mock ThingSpeak to return new data in correct format
        mock_fetch.return_value = (
            True,
            {
                "feeds": [
                    {
                        "entry_id": 101,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "field1": "50.5",  # Distance
                        "field2": "25.0",  # Temp
                    }
                ]
            },
            None,
        )

        # Mock CTOP to return success
        mock_send.return_value = {"success": True, "response": {"status": "ok"}}

        # 3. Parallel Processing Simulation
        logger.info(f"Starting parallel processing of {device_count} devices...")
        import concurrent.futures

        start_time = time.time()
        max_workers = 50  # High density

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="StressWorker"
        ) as executor:
            # We must use the singleton instance in the test

            futures = [
                executor.submit(process_device, dev["id"], dev)
                for dev in devices_to_process
            ]

            # Wait for all to complete (blocks; the returned done/not_done
            # sets aren't needed here)
            concurrent.futures.wait(futures)

        duration = time.time() - start_time
        logger.info("--- STRESS TEST COMPLETE ---")
        logger.info(f"Processed {device_count} devices in {duration:.2f} seconds.")
        logger.info(f"Average throughput: {device_count/duration:.2f} devices/sec.")

        # 4. Verification of data integrity
        logger.info("Verifying data integrity in SQLite...")
        for i in range(device_count):
            did = f"stress_dev_{i}"
            dev = store.get_device_by_id(did)
            if not dev or dev.get("last_processed_entry_id") != "101":
                actual = dev.get("last_processed_entry_id") if dev else "None"
                logger.error(
                    f"FAILURE: Device {did} last_entry_id is {actual}, expected 101"
                )
                return False

        logger.info(
            "DATA INTEGRITY VERIFIED: All devices successfully updated to entry_id 101."
        )

    # 5. Cleanup
    logger.info("Cleaning up stress test data...")
    with store._lock:
        for i in range(device_count):
            did = f"stress_dev_{i}"
            if did in store._devices:
                del store._devices[did]
                store._dirty_devices.add(did)
        store.flush()

    return True


if __name__ == "__main__":
    success = simulate_full_sync(1000)  # Test with 1,000 devices
    if success:
        print("\nSCALING TEST PASSED: System is stable and high-performance.")
        sys.exit(0)
    else:
        print("\nSCALING TEST FAILED: Integrity check failed.")
        sys.exit(1)
