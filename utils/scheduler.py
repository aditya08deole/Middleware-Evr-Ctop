from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime
import logging
from models import db, Device
from services import ThingSpeakService, PreprocessService, CTOPService
from config import Config

# Initialize scheduler
scheduler = BackgroundScheduler()

# Services will be initialized in init_scheduler
thingspeak_service = None
preprocess_service = None
ctop_service = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def process_device(device_id):
    """
    Process a single device: fetch, preprocess, transform, and send
    Ensures complete isolation per device (no confusion between devices)

    Args:
        device_id: ID of the device to process
    """
    device = db.session.get(Device, device_id)
    if not device:
        logger.error(f"Device {device_id} not found")
        return

    if not device.is_active:
        logger.info(f"Device {device.name} (ID: {device_id}) is inactive, skipping")
        return

    logger.info(f"Processing device: {device.name} (ID: {device_id})")
    logger.debug(f"Device isolation: Processing device_id={device_id}, name={device.name}, type={device.device_type}")

    try:
        # Step 1: Fetch from ThingSpeak (with duplicate detection)
        success, raw_data, error = thingspeak_service.fetch_data(device_id)

        if not success:
            logger.error(f"Failed to fetch data for device {device.name}: {error}")
            device.last_status = 'error'
            device.last_sync_time = datetime.utcnow()
            db.session.commit()
            return

        logger.info(f"Fetched data for device {device.name}")

        # Step 2: Preprocess (device-specific preprocessing)
        success, processed_data, error = preprocess_service.preprocess_data(device_id, raw_data)

        if not success:
            logger.error(f"Failed to preprocess data for device {device.name}: {error}")
            device.last_status = 'error'
            device.last_sync_time = datetime.utcnow()
            db.session.commit()
            return

        logger.info(f"Preprocessed {len(processed_data)} entries for device {device.name} (type: {device.device_type})")

        if not processed_data:
            logger.warning(f"No valid entries to process for device {device.name}")
            device.last_status = 'success'
            device.last_sync_time = datetime.utcnow()
            db.session.commit()
            return

        # Step 3: Transform to CTOP format (device-specific schema mapping)
        transformed_data = preprocess_service.transform_to_ctop_format(device_id, processed_data)

        # Step 4: Store processed data (optional)
        preprocess_service.store_processed_data(device_id, raw_data, processed_data, transformed_data)

        # Step 5: Send to CTOP endpoints
        success_count = 0
        sent_entry_ids = []

        for i, payload in enumerate(transformed_data):
            if i >= len(processed_data):
                break

            # Extract entry_id for tracking (will mark as sent after successful deliver)
            entry_id = processed_data[i].get('entry_id')

            # pass payload as keyword so CTOPService treats it as payload (not device_data)
            results = ctop_service.send_to_ctop(device_id, payload=payload)

            # Check if it was successful (results is a dict with success field)
            if isinstance(results, dict) and results.get('success'):
                success_count += 1
                if entry_id:
                    sent_entry_ids.append(entry_id)
                logger.debug(f"Device {device_id}: Successfully sent entry_id={entry_id}")
            else:
                logger.warning(f"Device {device_id}: Failed to send entry_id={entry_id}, result={results}")

        logger.info(f"Sent {success_count}/{len(transformed_data)} payloads to CTOP for device {device.name}")

        # Update device status
        device.last_status = 'success' if success_count > 0 else 'warning'
        device.last_sync_time = datetime.utcnow()
        db.session.commit()

    except Exception as e:
        logger.error(f"Error processing device {device.name}: {str(e)}")
        device.last_status = 'error'
        device.last_sync_time = datetime.utcnow()
        db.session.commit()


def scheduled_job(app):
    """
    Scheduled job to process all active devices
    Ensures each device is processed independently with complete separation
    """
    with app.app_context():
        logger.info("=" * 70)
        logger.info(f"Starting scheduled job at {datetime.utcnow()}")
        logger.info("DEVICE SEPARATION: Processing devices sequentially with full isolation")

        devices = Device.query.filter_by(is_active=True).all()

        if not devices:
            logger.info("No active devices to process")
            logger.info("=" * 70)
            return

        logger.info(f"Processing {len(devices)} active devices")

        device_results = {
            'total': len(devices),
            'success': 0,
            'error': 0,
            'devices_processed': []
        }

        for device in devices:
            try:
                logger.info(f"[Device Separation] Processing: ID={device.id}, Name={device.name}, Type={device.device_type}")
                process_device(device.id)
                device_results['success'] += 1
                device_results['devices_processed'].append({
                    'id': device.id,
                    'name': device.name,
                    'status': 'success'
                })
            except Exception as e:
                logger.error(f"Error in scheduled job for device {device.name}: {str(e)}")
                device_results['error'] += 1
                device_results['devices_processed'].append({
                    'id': device.id,
                    'name': device.name,
                    'status': 'error'
                })

        logger.info(f"Scheduled job completed at {datetime.utcnow()}")
        logger.info(f"Results: {device_results['success']} successful, {device_results['error']} errors")
        logger.info("=" * 70)


def init_scheduler(app):
    """
    Initialize the scheduler with the Flask app
    
    Args:
        app: Flask application instance
    """
    global thingspeak_service, preprocess_service, ctop_service
    
    # Initialize services within app context or after app is created
    thingspeak_service = ThingSpeakService()
    preprocess_service = PreprocessService()
    ctop_service = CTOPService()
    
    interval_minutes = Config.SCHEDULER_INTERVAL_SECONDS / 60
    
    # Add the scheduled job
    scheduler.add_job(
        func=scheduled_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id='iot_data_pipeline',
        name='IoT Data Pipeline Job',
        replace_existing=True,
        kwargs={'app': app}
    )
    
    # Start the scheduler
    scheduler.start()
    
    logger.info(f"Scheduler initialized with {interval_minutes} minute interval")

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
            'iot_data_pipeline',
            trigger=IntervalTrigger(minutes=minutes)
        )
        logger.info(f"Rescheduled IoT Data Pipeline to {minutes} minute interval")
        return True
    except Exception as e:
        logger.error(f"Failed to reschedule job: {str(e)}")
        return False
