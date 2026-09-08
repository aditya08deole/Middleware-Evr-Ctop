from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timezone
import logging
import json
import os
import threading
from firebase.firestore_service import FirestoreService
from services import ThingSpeakService, PreprocessService, CTOPService, EMQXService
from config import Config
import concurrent.futures

# A device is considered INACTIVE if its latest data point is older than this
STALENESS_THRESHOLD_MINUTES = 30

# Per-device locks so an EMQX instant-trigger (fired the moment an MQTT message
# arrives) can never run process_device() concurrently with the periodic
# scheduled tick for the same device — both call process_device_safe().
_device_locks = {}
_device_locks_guard = threading.Lock()

# Initialize scheduler
scheduler = BackgroundScheduler()

# Initialize services
firestore_service = FirestoreService()
thingspeak_service = ThingSpeakService()
preprocess_service = PreprocessService()
ctop_service = CTOPService()
emqx_service = EMQXService()
from utils.local_device_store import local_device_store

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NOTE [Thread Safety]: thingspeak_service, preprocess_service, ctop_service, and
# emqx_service are module-level singletons shared across ThreadPoolExecutor workers.
# requests.Session is thread-safe. PreprocessService is stateless (no shared mutable state).
# EMQXService uses explicit locks for buffer/client access. Do NOT add mutable shared
# state to these services without adding appropriate locks.

def process_device(device_id, device_data):
    """
    Process a single device: fetch, preprocess, transform, and send
    
    Args:
        device_id: ID of the device to process
        device_data: Device data dictionary
    """
    if not device_data:
        logger.error(f"Device {device_id} not found")
        return
    
    if not device_data.get('is_active', True):
        logger.info(f"Device {device_data.get('name')} (ID: {device_id}) is inactive, skipping")
        return
    
    logger.info(f"Processing device: {device_data.get('name')} (ID: {device_id})")
    
    try:
        # Step 1: Fetch data — route by data source platform
        data_source = device_data.get('data_source', 'thingspeak')
        
        if data_source == 'emqx':
            success, raw_data, error = emqx_service.fetch_data(device_id, device_data)
        else:
            success, raw_data, error = thingspeak_service.fetch_data(device_id, device_data)
        
        if not success:
            logger.error(f"Failed to fetch data for device {device_data.get('name')}: {error}")
            # Rollback EMQX buffer if applicable (fetch returned an error after draining)
            if data_source == 'emqx':
                emqx_service.rollback_fetch(device_id)
            local_device_store.update_device_fields(device_id, {
                'last_status': 'error',
                'last_error': error,
                'last_sync_time': datetime.utcnow().isoformat()
            })
            local_device_store.increment_device_stats(device_id, fetch_success=False, send_success=False)
            return
        
        # ── 30-MINUTE STALENESS GUARD ──────────────────────────────────────────
        # Determine the timestamp of the latest data point from the feed.
        # If it's older than STALENESS_THRESHOLD_MINUTES, mark the device INACTIVE
        # and skip all further processing (no CTOP send for stale data).
        feeds = raw_data.get('feeds', []) if raw_data else []
        latest_reading_time = None
        if feeds:
            # ThingSpeak/EMQX feeds have a 'created_at' field (ISO 8601 UTC)
            last_feed = feeds[-1]  # feeds are chronological, last is newest
            ts_str = last_feed.get('created_at')
            if ts_str:
                try:
                    latest_reading_time = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    latest_reading_time = None

        now_utc = datetime.now(timezone.utc)
        if latest_reading_time:
            age_minutes = (now_utc - latest_reading_time).total_seconds() / 60
            logger.info(f"Fetched fresh data for device {device_data.get('name')} (reading time: {latest_reading_time.isoformat()})")
            if age_minutes > STALENESS_THRESHOLD_MINUTES:
                logger.info(
                    f"Device {device_data.get('name')} data is stale ({age_minutes:.1f} min old > {STALENESS_THRESHOLD_MINUTES} min). "
                    f"Marking INACTIVE."
                )
                local_device_store.update_device_fields(device_id, {
                    'last_status': 'inactive',
                    'last_error': None,
                    'last_reading_time': latest_reading_time.isoformat(),
                    'last_sync_time': now_utc.isoformat()
                })
                return
        else:
            # No feed came back on THIS poll — this happens on nearly every tick
            # for BOTH platforms once the current reading has already been seen:
            #   - ThingSpeak: fetch_data() does not re-filter by entry_id anymore
            #     (see thingspeak_service.py), so an empty response here means
            #     ThingSpeak genuinely has zero feeds on the channel.
            #   - EMQX: fetch_data() returns feeds=[] whenever no MQTT message
            #     landed in the buffer since the last poll — normal, not stale.
            # Fall back to the device's own last known reading time (persisted
            # whenever we did see a fresh entry) and only mark INACTIVE if THAT
            # is stale/missing.
            stored_reading_time = device_data.get('last_reading_time')
            stored_dt = None
            if stored_reading_time:
                try:
                    stored_dt = datetime.fromisoformat(str(stored_reading_time).replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    stored_dt = None

            if stored_dt is None:
                logger.info(f"Device {device_data.get('name')} has no prior reading. Marking INACTIVE.")
                local_device_store.update_device_fields(device_id, {
                    'last_status': 'inactive',
                    'last_error': None,
                    'last_sync_time': now_utc.isoformat()
                })
                return

            age_minutes = (now_utc - stored_dt).total_seconds() / 60
            if age_minutes > STALENESS_THRESHOLD_MINUTES:
                logger.info(
                    f"Device {device_data.get('name')} last reading is stale "
                    f"({age_minutes:.1f} min old > {STALENESS_THRESHOLD_MINUTES} min). Marking INACTIVE."
                )
                local_device_store.update_device_fields(device_id, {
                    'last_status': 'inactive',
                    'last_error': None,
                    'last_sync_time': now_utc.isoformat()
                })
                return

            # Still within the freshness window — just an idle poll tick, not
            # stale. Nothing was actually sent this tick, so don't touch
            # last_status: it previously got hard-set to 'success' here even
            # when the last real CTOP send had failed, which made the
            # dashboard flash "success" for a few seconds every time
            # ThingSpeak hadn't produced a fresh reading yet, then flip back
            # to 'error' the moment a new reading came in and failed again.
            logger.debug(f"Device {device_data.get('name')} idle this tick (last reading {age_minutes:.1f} min ago).")
            local_device_store.update_device_fields(device_id, {
                'last_sync_time': now_utc.isoformat()
            })
            return
        # ── END STALENESS GUARD ────────────────────────────────────────────────

        # Step 2: Preprocess (only reached for fresh data within 30 min)
        success, processed_data, error = preprocess_service.preprocess_data(device_id, raw_data, device_data=device_data)

        if not success:
            logger.error(f"Failed to preprocess data for device {device_data.get('name')}: {error}")
            local_device_store.update_device_fields(device_id, {
                'last_status': 'error',
                'last_error': error,
                'last_sync_time': datetime.utcnow().isoformat()
            })
            local_device_store.increment_device_stats(device_id, fetch_success=True, send_success=False)
            return

        if not processed_data:
            # Fresh data was fetched but preprocessing produced no entries.
            # No send was attempted, so don't overwrite last_status — see
            # the idle-tick branch above for why.
            logger.debug(f"No new entries to process for device {device_data.get('name')}")
            local_device_store.update_device_fields(device_id, {
                'last_reading_time': latest_reading_time.isoformat() if latest_reading_time else None,
                'last_sync_time': datetime.utcnow().isoformat()
            })
            return

        # Step 3: Transform to CTOP format
        transformed_data = preprocess_service.transform_to_ctop_format(device_id, processed_data, device_data=device_data)
        
        # Step 4: Ensure we only send NEW entries to CTOP to prevent duplicates
        try:
            last_entry_id = int(device_data.get('last_processed_entry_id') or 0)
        except (ValueError, TypeError):
            last_entry_id = 0
        
        new_processed_data = []
        new_transformed_data = []
        
        for i, entry in enumerate(processed_data):
            try:
                entry_id = int(entry.get('entry_id') or 0)
            except (ValueError, TypeError):
                # Non-numeric entry_id (e.g. from EMQX): treat as new data
                entry_id = 0
            
            if last_entry_id > 0 and entry_id <= last_entry_id:
                logger.debug(f"Skipping old entry {entry_id} for device {device_id} (last processed: {last_entry_id})")
                continue
            
            logger.info(f"NEW DATA detected for device {device_id}: entry_id {entry_id} > last {last_entry_id}")
            new_processed_data.append(entry)
            if i < len(transformed_data):
                new_transformed_data.append(transformed_data[i])
                
        if not new_transformed_data:
            # Silently skip if there's no new data (to prevent terminal spam every 15s).
            #
            # This is the main cause of the dashboard flashing "success" for
            # a few seconds and then flipping back to "error": when the
            # last CTOP send for the current entry_id failed, entry_id is
            # deliberately NOT advanced (so the failed reading gets retried
            # next tick) — but that means once ThingSpeak stops returning a
            # newer reading than last_processed_entry_id, this same entry
            # looks "already handled" and used to get unconditionally
            # stamped 'success' here, overwriting the real 'error' status
            # from the failed send. No send was attempted this tick, so
            # last_status must be left exactly as the last real attempt
            # left it.
            logger.debug(f"No new data for device {device_id} (last entry: {last_entry_id})")
            local_device_store.update_device_fields(device_id, {
                'last_reading_time': latest_reading_time.isoformat() if latest_reading_time else None,
                'last_sync_time': datetime.utcnow().isoformat()
            })
            return
            
        # Step 5: Send to CTOP endpoints
        success_count = 0
        last_send_error = None
        latest_entry_id = str(last_entry_id)

        for i, payload in enumerate(new_transformed_data):
            results = ctop_service.send_to_ctop(device_id, device_data, payload=payload)

            # Check if it was successful (results is a dict with success field)
            if isinstance(results, dict) and results.get('success'):
                success_count += 1
                latest_entry_id = str(new_processed_data[i].get('entry_id', latest_entry_id))
            elif isinstance(results, dict):
                last_send_error = results.get('error')

        logger.info(f"Sent {success_count}/{len(new_transformed_data)} NEW payloads to CTOP for device {device_data.get('name')}")

        # Update local mirror (entry_id, status, sync_time, reading_time).
        # update_entry_id() has no last_error parameter, so the actual CTOP
        # error message (e.g. "HTTP 503: ...") was previously lost — the
        # device would show status 'error' with no explanation anywhere in
        # the UI. Set it explicitly, and clear it on success.
        final_status = 'success' if success_count > 0 else 'error'
        local_device_store.update_entry_id(
            device_id, latest_entry_id, last_status=final_status,
            last_reading_time=latest_reading_time.isoformat() if latest_reading_time else None
        )
        if final_status == 'error' and last_send_error:
            local_device_store.update_device_fields(device_id, {'last_error': last_send_error})
        
        # Increment stats locally
        local_device_store.increment_device_stats(device_id, fetch_success=True, send_success=success_count > 0)
        
        # Confirm EMQX buffer consumption on any success, rollback on total failure
        if data_source == 'emqx':
            if success_count > 0:
                emqx_service.confirm_consumed(device_id)
            else:
                emqx_service.rollback_fetch(device_id)
        
    except Exception as e:
        logger.error(f"Error processing device {device_data.get('name')}: {str(e)}")
        # Rollback EMQX buffer on unhandled exception to prevent data loss
        if device_data.get('data_source') == 'emqx':
            emqx_service.rollback_fetch(device_id)
        local_device_store.update_device_fields(device_id, {
            'last_status': 'error',
            'last_error': str(e),
            'last_sync_time': datetime.utcnow().isoformat()
        })
        local_device_store.increment_device_stats(device_id, fetch_success=False, send_success=False)

def _get_device_lock(device_id):
    key = str(device_id)
    with _device_locks_guard:
        lock = _device_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _device_locks[key] = lock
        return lock

def process_device_safe(device_id, device_data):
    """
    Run process_device() for a device, skipping the call entirely if that same
    device is already being processed elsewhere (e.g. the periodic scheduled
    tick and an EMQX instant on-message trigger landing at the same time).
    Whatever run is already in-flight will pick up any newly buffered data,
    so a skipped call here is never lost work.
    """
    lock = _get_device_lock(device_id)
    if not lock.acquire(blocking=False):
        logger.debug(f"Device {device_id} is already being processed — skipping concurrent trigger.")
        return
    try:
        process_device(device_id, device_data)
    finally:
        lock.release()

def scheduled_job():
    """The main job function that runs on schedule (Firestore version)"""
    try:
        # Use DeviceCache to get active devices (minimizes Firestore reads)
        from utils.device_cache import device_cache
        devices = device_cache.get_devices()
        
        if not devices:
            # If cache is empty, might need a refresh or no devices exist
            logger.info("No active devices found in cache.")
            return
            
        logger.info(f"Starting parallel processing for {len(devices)} devices...")
        
        # Phase 1 Optimization: Parallel Processing via ThreadPoolExecutor
        max_workers = getattr(Config, 'SCHEDULER_MAX_WORKERS', 20)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Create a dictionary of futures to device names for better error reporting
            future_to_device = {
                executor.submit(process_device_safe, device['id'], device): device.get('name', 'Unknown')
                for device in devices
            }
            
            for future in concurrent.futures.as_completed(future_to_device):
                device_name = future_to_device[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Parallel processing failed for device {device_name}: {str(e)}")
                    
        logger.info("Parallel processing cycle complete.")
            
    except Exception as e:
        logger.error(f"Error in scheduled job: {str(e)}")

def sync_mirror_job():
    """
    Sync job that runs every hour to reconcile local mirror with Firebase.
    This is the only 'official' sync point that performs cloud writes.
    """
    logger.info("Starting hourly sync: Local Mirror <-> Firebase")
    
    # 1. Write back any entry_id updates and stats to Firebase
    local_device_store.sync_to_firebase()
    
    # 2. Pull any remote changes (new devices, edited settings) from Firebase
    local_device_store.sync_from_firebase()
    
    logger.info("Hourly sync complete.")

_scheduler_lock_file = None  # kept referenced so the OS-level lock isn't released by GC


def _acquire_scheduler_ownership():
    """
    Ensure only one process owns the scheduler/EMQX clients when this app is
    run under multiple Gunicorn workers (each worker imports app.py and calls
    init_scheduler() independently — without this guard, N workers means N
    BackgroundScheduler instances all polling and posting the same devices,
    and N MQTT clients fighting over the same client_id).

    Uses a non-blocking file lock (fcntl, POSIX-only): the first worker to
    start wins the lock and runs the scheduler; the rest skip it. On
    platforms without fcntl (Windows local dev via `python app.py`), this is
    a no-op — those runs are always single-process anyway.
    """
    try:
        import fcntl
    except ImportError:
        return True

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    instance_dir = os.path.join(base_dir, 'instance')
    os.makedirs(instance_dir, exist_ok=True)
    lock_path = os.path.join(instance_dir, 'scheduler.lock')

    lock_file = open(lock_path, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return False

    global _scheduler_lock_file
    _scheduler_lock_file = lock_file
    return True


def init_scheduler(app):
    """
    Initialize the scheduler with the Flask app

    Args:
        app: Flask application instance
    """
    if not _acquire_scheduler_ownership():
        logger.info(
            "Another worker process already owns the scheduler/EMQX clients "
            "— skipping scheduler startup in this worker."
        )
        return

    interval_seconds = Config.SCHEDULER_INTERVAL_SECONDS

    # Add the scheduled job
    scheduler.add_job(
        func=scheduled_job,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id='iot_data_pipeline_firestore',
        name='IoT Data Pipeline Job (Local Cache)',
        replace_existing=True
    )

    # Add the hourly sync job
    scheduler.add_job(
        func=sync_mirror_job,
        trigger=IntervalTrigger(hours=1),
        id='mirror_sync_job',
        name='Hourly Mirror Sync (Local <-> Firebase)',
        replace_existing=True
    )
    
    # Start the scheduler
    try:
        scheduler.start()
        logger.info(f"Firestore Scheduler initialized with {interval_seconds} second interval")
        
        # Subscribe to all EMQX devices at startup
        try:
            from utils.device_cache import device_cache
            devices = device_cache.get_devices()
            if devices:
                emqx_service.subscribe_all_devices(devices)
        except Exception as e:
            logger.warning(f"Failed to subscribe EMQX devices at startup: {e}")
        
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        if "Scheduler is already running" in str(e):
            logger.info("Scheduler is already running, skipping start.")
        else:
            logger.error(f"Failed to start scheduler: {str(e)}")

def reschedule_job(seconds):
    """
    Reschedule the IoT Data Pipeline job with a new interval (Firestore)
    
    Args:
        seconds: New interval in seconds
    """
    if seconds < 1:
        logger.error(f"Invalid interval: {seconds}. Must be >= 1.")
        return False
        
    try:
        scheduler.reschedule_job(
            'iot_data_pipeline_firestore',
            trigger=IntervalTrigger(seconds=seconds)
        )
        logger.info(f"Rescheduled Firestore IoT Data Pipeline to {seconds} second interval")
        return True
    except Exception as e:
        logger.error(f"Failed to reschedule job: {str(e)}")
        return False
