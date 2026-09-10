from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime
import logging
import os
import threading
from models import db, Device
from services import ThingSpeakService, PreprocessService, CTOPService, EMQXService
from utils.reading_history import reading_history_store
from config import Config

# Initialize scheduler
scheduler = BackgroundScheduler()

# Services will be initialized in init_scheduler
thingspeak_service = None
preprocess_service = None
ctop_service = None
emqx_service = None

# Flask app reference, set in init_scheduler(). Needed by process_device_safe()
# to push an app context when called from a raw background thread (the EMQX
# instant on-message trigger) rather than from inside scheduled_job(), which
# already runs under `with app.app_context():`.
_app = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Per-device locks so an EMQX instant-trigger (fired the moment an MQTT
# message arrives) can never run process_device() concurrently with the
# periodic scheduled tick for the same device — both call process_device_safe().
_device_locks = {}
_device_locks_guard = threading.Lock()

# CTOP delivery health tracking (see process_device() Step 5) — mirrors
# utils/scheduler_firestore.py's identical constants/logic. A device that
# fails to deliver to CTOP CTOP_ATTENTION_THRESHOLD times in a row is
# flagged needs_attention so it's visible in the UI instead of only in
# logs. Once failures pile up further, backoff kicks in so a dead/
# misconfigured CTOP endpoint isn't hammered every tick forever.
CTOP_ATTENTION_THRESHOLD = 5
CTOP_BACKOFF_LEVEL_1 = 5
CTOP_BACKOFF_LEVEL_1_SECONDS = 60
CTOP_BACKOFF_LEVEL_2 = 20
CTOP_BACKOFF_LEVEL_2_SECONDS = 300


def _ctop_backoff_seconds(consecutive_failures):
    """Seconds to wait between CTOP send attempts for a device with this
    many consecutive failures. 0 means no backoff — retry every tick."""
    if consecutive_failures >= CTOP_BACKOFF_LEVEL_2:
        return CTOP_BACKOFF_LEVEL_2_SECONDS
    if consecutive_failures >= CTOP_BACKOFF_LEVEL_1:
        return CTOP_BACKOFF_LEVEL_1_SECONDS
    return 0


def process_device(device_id):
    """
    Process a single device: fetch, preprocess, transform, and send
    Ensures complete isolation per device (no confusion between devices)

    Args:
        device_id: ID of the device to process
    """
    try:
        device_id = int(device_id)
    except (TypeError, ValueError):
        pass

    device = db.session.get(Device, device_id)
    if not device:
        logger.error(f"Device {device_id} not found")
        return

    if not device.is_active:
        logger.info(f"Device {device.name} (ID: {device_id}) is inactive, skipping")
        return

    logger.info(f"Processing device: {device.name} (ID: {device_id})")
    logger.debug(
        f"Device isolation: Processing device_id={device_id}, name={device.name}, type={device.device_type}"
    )

    data_source = getattr(device, "data_source", "thingspeak") or "thingspeak"

    try:
        # Step 1: Fetch — route by data source platform. Previously this
        # always called thingspeak_service regardless of data_source, so an
        # EMQX-configured device on this (non-Firebase) stack silently
        # polled ThingSpeak forever with channel_id='emqx' and never worked,
        # with no error surfaced anywhere.
        if data_source == "emqx":
            success, raw_data, error = emqx_service.fetch_data(
                device_id, device.to_dict_with_credentials()
            )
        else:
            success, raw_data, error = thingspeak_service.fetch_data(device_id)

        if not success:
            logger.error(f"Failed to fetch data for device {device.name}: {error}")
            if data_source == "emqx":
                emqx_service.rollback_fetch(device_id)
            device.last_status = "error"
            device.last_error = error
            device.last_sync_time = datetime.utcnow()
            db.session.commit()
            return

        logger.info(f"Fetched data for device {device.name}")

        # EMQX-specific fast path: if our subscriber client isn't even
        # connected to the broker right now (bad credentials, network drop,
        # broker restart), that's a much stronger and more immediate signal
        # than "no message arrived this tick" — mirrors the identical check
        # in utils/scheduler_firestore.py.
        if (
            data_source == "emqx"
            and not raw_data.get("feeds")
            and not emqx_service.is_connected(device_id)
        ):
            logger.warning(
                f"Device {device.name}: EMQX client not connected to broker "
                f"— marking inactive immediately."
            )
            device.last_status = "inactive"
            device.last_error = "MQTT client is not connected to the broker"
            device.last_sync_time = datetime.utcnow()
            db.session.commit()
            return

        # Step 2: Preprocess (device-specific preprocessing)
        success, processed_data, error = preprocess_service.preprocess_data(
            device_id, raw_data
        )

        if not success:
            logger.error(f"Failed to preprocess data for device {device.name}: {error}")
            if data_source == "emqx":
                emqx_service.rollback_fetch(device_id)
            device.last_status = "error"
            device.last_error = error
            device.last_sync_time = datetime.utcnow()
            db.session.commit()
            return

        logger.info(
            f"Preprocessed {len(processed_data)} entries for device {device.name} (type: {device.device_type})"
        )

        if not processed_data:
            logger.warning(f"No valid entries to process for device {device.name}")
            if data_source == "emqx":
                # Nothing usable came out of preprocessing, but the fetch
                # itself succeeded — don't leave the drained buffer stuck
                # pending forever.
                emqx_service.confirm_consumed(device_id)
            device.last_status = "success"
            device.last_sync_time = datetime.utcnow()
            db.session.commit()
            return

        # Step 3: Transform to CTOP format (device-specific schema mapping)
        transformed_data = preprocess_service.transform_to_ctop_format(
            device_id, processed_data
        )

        # Record sensor-value history for the dashboard's trend sparkline.
        # Independent of CTOP delivery outcome below — reflects what the
        # sensor reported, not whether it was successfully relayed.
        for entry, payload in zip(processed_data, transformed_data):
            reading_history_store.record(
                device_id, entry.get("entry_id"), entry.get("LCT"), payload
            )

        # Step 4: Store processed data (optional)
        preprocess_service.store_processed_data(
            device_id, raw_data, processed_data, transformed_data
        )

        # Step 5: Send to CTOP endpoints — unless this device is in a backoff
        # cooldown after repeated failures. New data isn't lost by skipping:
        # transformed_data below isn't sent, and for EMQX the drained buffer
        # is rolled back, so the same reading is retried on the next
        # eligible tick.
        consecutive_failures = device.consecutive_failures or 0
        backoff_seconds = _ctop_backoff_seconds(consecutive_failures)
        now = datetime.utcnow()

        if (
            backoff_seconds
            and device.last_ctop_attempt_time
            and (now - device.last_ctop_attempt_time).total_seconds() < backoff_seconds
        ):
            logger.debug(
                f"Device {device.name}: CTOP backoff active "
                f"({consecutive_failures} consecutive failures) — skipping send this tick."
            )
            if data_source == "emqx":
                emqx_service.rollback_fetch(device_id)
            device.last_sync_time = now
            db.session.commit()
            return

        success_count = 0
        sent_entry_ids = []

        for i, payload in enumerate(transformed_data):
            if i >= len(processed_data):
                break

            # Extract entry_id for tracking (will mark as sent after successful deliver)
            entry_id = processed_data[i].get("entry_id")

            # pass payload as keyword so CTOPService treats it as payload (not device_data)
            results = ctop_service.send_to_ctop(device_id, payload=payload)

            # Check if it was successful (results is a dict with success field)
            if isinstance(results, dict) and results.get("success"):
                success_count += 1
                if entry_id:
                    sent_entry_ids.append(entry_id)
                logger.debug(
                    f"Device {device_id}: Successfully sent entry_id={entry_id}"
                )
            else:
                logger.warning(
                    f"Device {device_id}: Failed to send entry_id={entry_id}, result={results}"
                )

        logger.info(
            f"Sent {success_count}/{len(transformed_data)} payloads to CTOP for device {device.name}"
        )

        # Track the consecutive-failure streak that the backoff check above
        # and the needs_attention UI flag both depend on.
        device.consecutive_failures = (
            0 if success_count > 0 else consecutive_failures + 1
        )
        device.needs_attention = device.consecutive_failures >= CTOP_ATTENTION_THRESHOLD
        device.last_ctop_attempt_time = now
        if (
            device.needs_attention
            and device.consecutive_failures == CTOP_ATTENTION_THRESHOLD
        ):
            logger.warning(
                f"Device {device.name} ({device_id}): {device.consecutive_failures} "
                f"consecutive CTOP send failures — flagged needs_attention."
            )

        # Update device status
        device.last_status = "success" if success_count > 0 else "warning"
        if success_count > 0:
            device.last_error = None
        device.last_sync_time = datetime.utcnow()
        db.session.commit()

        # Confirm EMQX buffer consumption on any success, rollback on total failure
        if data_source == "emqx":
            if success_count > 0:
                emqx_service.confirm_consumed(device_id)
            else:
                emqx_service.rollback_fetch(device_id)

    except Exception as e:
        logger.error(f"Error processing device {device.name}: {str(e)}")
        if data_source == "emqx":
            emqx_service.rollback_fetch(device_id)
        device.last_status = "error"
        device.last_error = str(e)
        device.last_sync_time = datetime.utcnow()
        db.session.commit()


def _get_device_lock(device_id):
    key = str(device_id)
    with _device_locks_guard:
        lock = _device_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _device_locks[key] = lock
        return lock


def process_device_safe(device_id):
    """
    Run process_device() for a device, skipping the call entirely if that
    same device is already being processed elsewhere (e.g. the periodic
    scheduled tick and an EMQX instant on-message trigger landing at the
    same time). Whatever run is already in-flight will pick up any newly
    buffered data, so a skipped call here is never lost work.

    Pushes a Flask app context if one isn't already active — process_device()
    uses db.session, and the EMQX instant-trigger fires from a raw
    background thread with no context of its own (scheduled_job() already
    provides one, so the extra push there is just a harmless no-op nesting).
    """
    lock = _get_device_lock(device_id)
    if not lock.acquire(blocking=False):
        logger.debug(
            f"Device {device_id} is already being processed — skipping concurrent trigger."
        )
        return
    try:
        if _app is not None:
            with _app.app_context():
                process_device(device_id)
        else:
            process_device(device_id)
    finally:
        lock.release()


def scheduled_job(app):
    """
    Scheduled job to process all active devices
    Ensures each device is processed independently with complete separation
    """
    with app.app_context():
        logger.info("=" * 70)
        logger.info(f"Starting scheduled job at {datetime.utcnow()}")
        logger.info(
            "DEVICE SEPARATION: Processing devices sequentially with full isolation"
        )

        devices = Device.query.filter_by(is_active=True).all()

        if not devices:
            logger.info("No active devices to process")
            logger.info("=" * 70)
            return

        logger.info(f"Processing {len(devices)} active devices")

        device_results = {
            "total": len(devices),
            "success": 0,
            "error": 0,
            "devices_processed": [],
        }

        for device in devices:
            try:
                logger.info(
                    f"[Device Separation] Processing: ID={device.id}, Name={device.name}, Type={device.device_type}"
                )
                process_device_safe(device.id)
                device_results["success"] += 1
                device_results["devices_processed"].append(
                    {"id": device.id, "name": device.name, "status": "success"}
                )
            except Exception as e:
                logger.error(
                    f"Error in scheduled job for device {device.name}: {str(e)}"
                )
                device_results["error"] += 1
                device_results["devices_processed"].append(
                    {"id": device.id, "name": device.name, "status": "error"}
                )

        logger.info(f"Scheduled job completed at {datetime.utcnow()}")
        logger.info(
            f"Results: {device_results['success']} successful, {device_results['error']} errors"
        )
        logger.info("=" * 70)


_scheduler_lock_file = None  # kept referenced so the OS-level lock isn't released by GC


def _acquire_scheduler_ownership():
    """
    Ensure only one process owns the scheduler/EMQX clients when this app is
    run under multiple Gunicorn workers (each worker imports app.py and calls
    init_scheduler() independently — without this guard, N workers means N
    BackgroundScheduler instances all polling and posting the same devices,
    and N MQTT clients fighting over the same client_id). Mirrors the same
    guard in utils/scheduler_firestore.py, but uses a distinct lock file so
    the two scheduler implementations never contend with each other even if
    something unusual imported both in one process.

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
    instance_dir = os.path.join(base_dir, "instance")
    os.makedirs(instance_dir, exist_ok=True)
    lock_path = os.path.join(instance_dir, "scheduler_sqlite.lock")

    lock_file = open(lock_path, "w")
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
    global thingspeak_service, preprocess_service, ctop_service, emqx_service, _app

    if not _acquire_scheduler_ownership():
        logger.info(
            "Another worker process already owns the scheduler/EMQX clients "
            "— skipping scheduler startup in this worker."
        )
        return

    _app = app

    # Initialize services within app context or after app is created
    thingspeak_service = ThingSpeakService()
    preprocess_service = PreprocessService()
    ctop_service = CTOPService()
    emqx_service = EMQXService()

    interval_minutes = Config.SCHEDULER_INTERVAL_SECONDS / 60

    # Add the scheduled job
    scheduler.add_job(
        func=scheduled_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="iot_data_pipeline",
        name="IoT Data Pipeline Job",
        replace_existing=True,
        kwargs={"app": app},
    )

    # Start the scheduler
    scheduler.start()

    logger.info(f"Scheduler initialized with {interval_minutes} minute interval")

    # Subscribe to all EMQX devices at startup (mirrors scheduler_firestore.py)
    try:
        with app.app_context():
            devices = Device.query.filter_by(is_active=True).all()
            if devices:
                emqx_service.subscribe_all_devices(
                    [d.to_dict_with_credentials() for d in devices]
                )
    except Exception as e:
        logger.warning(f"Failed to subscribe EMQX devices at startup: {e}")


def reschedule_job(minutes):
    """
    Reschedule the IoT Data Pipeline job with a new interval

    Args:
        minutes: New interval in minutes
    """
    if minutes < 1:
        logger.error(f"Invalid interval: {minutes}. Must be >= 1.")
        return False

    try:
        scheduler.reschedule_job(
            "iot_data_pipeline", trigger=IntervalTrigger(minutes=minutes)
        )
        logger.info(f"Rescheduled IoT Data Pipeline to {minutes} minute interval")
        return True
    except Exception as e:
        logger.error(f"Failed to reschedule job: {str(e)}")
        return False
