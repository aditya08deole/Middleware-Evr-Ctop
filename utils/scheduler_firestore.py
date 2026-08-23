from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime
import logging
import json
from firebase.firestore_service import FirestoreService
from services import ThingSpeakService, PreprocessService, CTOPService, EMQXService
from config import Config
import concurrent.futures

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
        
        logger.info(f"Fetched data for device {device_data.get('name')}")
        
        # Step 2: Preprocess
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
            # Idle interval (e.g., EMQX buffer had no new messages or no valid readings)
            logger.debug(f"No new entries to process for device {device_data.get('name')}")
            local_device_store.update_device_fields(device_id, {
                'last_status': 'success',
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
            # Silently skip if there's no new data (to prevent terminal spam every 15s)
            logger.debug(f"No new data for device {device_id} (last entry: {last_entry_id})")
            local_device_store.update_device_fields(device_id, {
                'last_status': 'success',
                'last_sync_time': datetime.utcnow().isoformat()
            })
            return
            
        # Step 5: Send to CTOP endpoints
        success_count = 0
        latest_entry_id = str(last_entry_id)
        
        for i, payload in enumerate(new_transformed_data):
            results = ctop_service.send_to_ctop(device_id, device_data, payload=payload)
            
            # Check if it was successful (results is a dict with success field)
            if isinstance(results, dict) and results.get('success'):
                success_count += 1
                latest_entry_id = str(new_processed_data[i].get('entry_id', latest_entry_id))
        
        logger.info(f"Sent {success_count}/{len(new_transformed_data)} NEW payloads to CTOP for device {device_data.get('name')}")
        
        # Update local mirror (entry_id, status, sync_time)
        final_status = 'success' if success_count > 0 else 'error'
        local_device_store.update_entry_id(device_id, latest_entry_id, last_status=final_status)
        
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
                executor.submit(process_device, device['id'], device): device.get('name', 'Unknown')
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

def init_scheduler(app):
    """
    Initialize the scheduler with the Flask app
    
    Args:
        app: Flask application instance
    """
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
