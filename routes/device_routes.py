from flask import Blueprint, request, jsonify, render_template
from models import db, Device, Log
from services import ThingSpeakService, PreprocessService, CTOPService
from middleware import handle_errors
from middleware.validation_middleware import validate_device_data
from utils.encryption import get_encryption_service
from datetime import datetime

device_bp = Blueprint('devices_sqlite', __name__, url_prefix='/devices')

# Initialize services
thingspeak_service = ThingSpeakService()
preprocess_service = PreprocessService()
ctop_service = CTOPService()
encryption_service = get_encryption_service()


def _get_emqx_service():
    """
    Fetch the live EMQXService singleton owned by utils/scheduler.py.

    Must be a function (not a module-level `from utils.scheduler import
    emqx_service`) — that global starts out None and is only assigned a
    real EMQXService() instance once init_scheduler() runs, which happens
    *after* this blueprint is imported. A module-level import would bind
    the None snapshot forever; re-reading the attribute on every call picks
    up the real instance once it exists.

    Uses `from utils.scheduler import emqx_service` (not `from utils import
    scheduler` and NOT `import utils.scheduler as x`) on purpose:
    utils/__init__.py does `from .scheduler import scheduler,
    init_scheduler`, which rebinds the name `scheduler` in the utils
    *package* namespace to the BackgroundScheduler instance. `import
    utils.scheduler as x` resolves via that same rebound package attribute
    (`x = utils.scheduler`, i.e. attribute access on the utils package) and
    silently gets the BackgroundScheduler instance instead of the module.
    `from utils.scheduler import emqx_service` resolves via sys.modules
    directly instead, which is unaffected by the rebinding — it's the only
    form of this import that's actually safe here.
    """
    from utils.scheduler import emqx_service
    return emqx_service


@device_bp.route('/', methods=['GET'])
@handle_errors
def list_devices():
    """Get all devices"""
    devices = Device.query.all()
    return jsonify({
        'success': True,
        'data': [device.to_dict() for device in devices]
    })

@device_bp.route('/<int:device_id>', methods=['GET'])
@handle_errors
def get_device(device_id):
    """Get a specific device"""
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404
    return jsonify({
        'success': True,
        'data': device.to_dict()
    })

@device_bp.route('/<int:device_id>/history', methods=['GET'])
@handle_errors
def get_device_history(device_id):
    """Recent sensor-reading history for this device's trend sparkline —
    see utils/reading_history.py; same in-memory-only store, shared by
    both scheduler backends."""
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404

    from utils.reading_history import reading_history_store
    history = reading_history_store.get_history(device_id)

    return jsonify({
        'success': True,
        'data': history
    })

@device_bp.route('/', methods=['POST'])
@validate_device_data
@handle_errors
def add_device():
    """Add a new device"""
    data = request.get_json()

    if 'device_type' not in data or not data['device_type']:
        return jsonify({
            'success': False,
            'error': 'Missing required field: device_type'
        }), 400

    # Encrypt sensitive fields before storing — previously this route
    # stored api_key/auth_token/emqx_password in plaintext even though the
    # Firestore-path route (device_routes_firestore.py) always encrypts them.
    data_source = data.get('data_source', 'thingspeak')
    encrypt_fields = ['api_key', 'auth_token']
    if data_source == 'emqx' and data.get('emqx_password'):
        encrypt_fields.append('emqx_password')
    encrypted_data = encryption_service.encrypt_dict(data, encrypt_fields)

    try:
        device = Device(
            name=encrypted_data['name'],
            device_type=encrypted_data['device_type'],
            channel_id=encrypted_data['channel_id'],
            api_key=encrypted_data['api_key'],
            ctop_url_1=encrypted_data['ctop_url_1'],
            ctop_url_2=encrypted_data.get('ctop_url_2'),
            auth_token=encrypted_data['auth_token'],
            latitude=encrypted_data.get('latitude'),
            longitude=encrypted_data.get('longitude'),
            is_active=encrypted_data.get('is_active', True),
            # EvaraTank specific fields
            tank_height=encrypted_data.get('tank_height'),
            distance_field=encrypted_data.get('distance_field'),
            temperature_field=encrypted_data.get('temperature_field'),
            filtering_method=encrypted_data.get('filtering_method', 'none'),
            filter_window=encrypted_data.get('filter_window', 5),
            # EvaraFlow specific fields
            meter_reading_field=encrypted_data.get('meter_reading_field'),
            flow_rate_field=encrypted_data.get('flow_rate_field'),
            # EvaraValve specific fields
            liters_field=encrypted_data.get('liters_field'),
            # EvaraTDS specific fields
            tds_field=encrypted_data.get('tds_field'),
            # Data source platform + EMQX fields — previously absent here
            # entirely, so an EMQX device created via this (non-Firebase)
            # stack silently kept data_source='thingspeak' and every
            # emqx_* field at its column default, regardless of what the
            # add-device form actually submitted.
            data_source=data_source,
            emqx_broker_url=encrypted_data.get('emqx_broker_url'),
            emqx_port=encrypted_data.get('emqx_port', 1883),
            emqx_username=encrypted_data.get('emqx_username'),
            emqx_password=encrypted_data.get('emqx_password'),
            emqx_topic=encrypted_data.get('emqx_topic'),
            emqx_use_tls=encrypted_data.get('emqx_use_tls', False),
            emqx_qos=encrypted_data.get('emqx_qos', 1),
            emqx_ca_cert_path=encrypted_data.get('emqx_ca_cert_path'),
            emqx_tls_insecure=encrypted_data.get('emqx_tls_insecure', False),
        )

        db.session.add(device)
        db.session.commit()

        if data_source == 'emqx':
            emqx_service = _get_emqx_service()
            if emqx_service is not None:
                emqx_service.subscribe_device(device.id, device.to_dict_with_credentials())
            else:
                import logging
                logging.getLogger(__name__).warning(
                    f"EMQX service not yet initialized — device {device.id} will pick up "
                    f"its MQTT subscription on the next scheduler tick."
                )

        return jsonify({
            'success': True,
            'message': 'Device added successfully',
            'data': device.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': f'Failed to add device: {str(e)}'
        }), 500

@device_bp.route('/<int:device_id>', methods=['PUT'])
@handle_errors
def update_device(device_id):
    """Update a device"""
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404
    data = request.get_json()

    # Encrypt sensitive fields if a new value was provided — previously only
    # emqx_password was handled here, so a PUT that changed api_key or
    # auth_token stored the new value in plaintext, silently overwriting
    # whatever ciphertext add_device() had originally stored.
    fields_to_encrypt = []
    if 'api_key' in data and data['api_key']:
        fields_to_encrypt.append('api_key')
    if 'auth_token' in data and data['auth_token']:
        fields_to_encrypt.append('auth_token')
    if 'emqx_password' in data and data['emqx_password']:
        fields_to_encrypt.append('emqx_password')
    if fields_to_encrypt:
        data = encryption_service.encrypt_dict(data, fields_to_encrypt)

    try:
        # Update fields if provided
        if 'name' in data:
            device.name = data['name']
        if 'device_type' in data:
            device.device_type = data['device_type']
        if 'channel_id' in data:
            device.channel_id = data['channel_id']
        if 'api_key' in data:
            device.api_key = data['api_key']
        if 'ctop_url_1' in data:
            device.ctop_url_1 = data['ctop_url_1']
        if 'ctop_url_2' in data:
            device.ctop_url_2 = data['ctop_url_2']
        if 'auth_token' in data:
            device.auth_token = data['auth_token']
        if 'latitude' in data:
            device.latitude = data['latitude']
        if 'longitude' in data:
            device.longitude = data['longitude']
        if 'is_active' in data:
            device.is_active = data['is_active']
        # EvaraTank specific fields
        if 'tank_height' in data:
            device.tank_height = data['tank_height']
        if 'distance_field' in data:
            device.distance_field = data['distance_field']
        if 'temperature_field' in data:
            device.temperature_field = data['temperature_field']
        if 'filtering_method' in data:
            device.filtering_method = data['filtering_method']
        if 'filter_window' in data:
            device.filter_window = data['filter_window']
        # EvaraFlow specific fields
        if 'meter_reading_field' in data:
            device.meter_reading_field = data['meter_reading_field']
        if 'flow_rate_field' in data:
            device.flow_rate_field = data['flow_rate_field']
        # EvaraValve specific fields
        if 'liters_field' in data:
            device.liters_field = data['liters_field']
        # EvaraTDS specific fields
        if 'tds_field' in data:
            device.tds_field = data['tds_field']
        # Data source platform + EMQX fields
        if 'data_source' in data:
            device.data_source = data['data_source']
        if 'emqx_broker_url' in data:
            device.emqx_broker_url = data['emqx_broker_url']
        if 'emqx_port' in data:
            device.emqx_port = data['emqx_port']
        if 'emqx_username' in data:
            device.emqx_username = data['emqx_username']
        if 'emqx_password' in data:
            device.emqx_password = data['emqx_password']
        if 'emqx_topic' in data:
            device.emqx_topic = data['emqx_topic']
        if 'emqx_use_tls' in data:
            device.emqx_use_tls = data['emqx_use_tls']
        if 'emqx_qos' in data:
            device.emqx_qos = data['emqx_qos']
        if 'emqx_ca_cert_path' in data:
            device.emqx_ca_cert_path = data['emqx_ca_cert_path']
        if 'emqx_tls_insecure' in data:
            device.emqx_tls_insecure = data['emqx_tls_insecure']

        device.updated_at = datetime.utcnow()
        db.session.commit()

        # Refresh MQTT subscription if EMQX device
        emqx_service = _get_emqx_service()
        if emqx_service is not None:
            if device.data_source == 'emqx' and device.is_active:
                emqx_service.subscribe_device(device.id, device.to_dict_with_credentials())
            else:
                emqx_service.unsubscribe_device(device.id)

        return jsonify({
            'success': True,
            'message': 'Device updated successfully',
            'data': device.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': f'Failed to update device: {str(e)}'
        }), 500

@device_bp.route('/<int:device_id>', methods=['DELETE'])
@handle_errors
def delete_device(device_id):
    """Delete a device"""
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404

    try:
        if device.data_source == 'emqx':
            emqx_service = _get_emqx_service()
            if emqx_service is not None:
                emqx_service.unsubscribe_device(device.id)

        db.session.delete(device)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Device deleted successfully'
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': f'Failed to delete device: {str(e)}'
        }), 500

def _sync_device_data(device):
    """Core pipeline: fetch → preprocess → transform → send to CTOP"""
    device_id = device.id
    data_source = getattr(device, 'data_source', 'thingspeak') or 'thingspeak'

    # Step 1: Fetch — route by data source platform (previously always
    # ThingSpeak regardless of data_source; see process_device() in
    # utils/scheduler.py for the same fix and its rationale).
    if data_source == 'emqx':
        emqx_service = _get_emqx_service()
        if emqx_service is None:
            return False, 'EMQX service not yet initialized', None
        success, raw_data, error = emqx_service.fetch_data(device_id, device.to_dict_with_credentials())
    else:
        success, raw_data, error = thingspeak_service.fetch_data(device_id)

    if not success:
        if data_source == 'emqx':
            _get_emqx_service().rollback_fetch(device_id)
        return False, f'Failed to fetch data: {error}', None

    # Step 2: Preprocess
    success, processed_data, error = preprocess_service.preprocess_data(device_id, raw_data)
    if not success:
        if data_source == 'emqx':
            _get_emqx_service().rollback_fetch(device_id)
        return False, f'Failed to preprocess data: {error}', None

    # Step 3: Transform to CTOP format
    transformed_data = preprocess_service.transform_to_ctop_format(device_id, processed_data)

    # Store processed data record
    preprocess_service.store_processed_data(device_id, raw_data, processed_data, transformed_data)

    # Step 4: Send each payload to CTOP
    results = []
    success_count = 0
    for payload in transformed_data:
        result = ctop_service.send_to_ctop(device_id, payload=payload)
        results.append(result)
        if isinstance(result, dict) and result.get('success'):
            success_count += 1

    # Update sync timestamp
    device.updated_at = datetime.utcnow()
    db.session.commit()

    if data_source == 'emqx':
        emqx_service = _get_emqx_service()
        if transformed_data:
            if success_count > 0:
                emqx_service.confirm_consumed(device_id)
            else:
                emqx_service.rollback_fetch(device_id)
        else:
            # Fetch succeeded but nothing to send — don't leave the drained
            # buffer stuck pending forever.
            emqx_service.confirm_consumed(device_id)

    return True, None, results

@device_bp.route('/<int:device_id>/fetch', methods=['POST'])
@handle_errors
def manual_fetch(device_id):
    """Manually trigger data fetch for a device"""
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404

    try:
        success, error, results = _sync_device_data(device)

        if not success:
            return jsonify({
                'success': False,
                'error': error
            }), 400

        return jsonify({
            'success': True,
            'message': 'Data fetched, processed, and sent successfully',
            'data': {
                'ctop_results': results
            }
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to process data: {str(e)}'
        }), 500

@device_bp.route('/<int:device_id>/logs', methods=['GET'])
@handle_errors
def get_device_logs(device_id):
    """Get logs for a specific device"""
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404

    limit = request.args.get('limit', 50, type=int)
    log_type = request.args.get('type')

    query = Log.query.filter(Log.device_id == device_id)

    if log_type:
        query = query.filter_by(log_type=log_type)

    logs = query.order_by(Log.created_at.desc()).limit(limit).all()

    return jsonify({
        'success': True,
        'data': [log.to_dict() for log in logs]
    })

@device_bp.route('/<int:device_id>/toggle', methods=['POST'])
@handle_errors
def toggle_device(device_id):
    """Toggle device active status"""
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404

    device.is_active = not device.is_active
    device.updated_at = datetime.utcnow()
    db.session.commit()

    if device.data_source == 'emqx':
        emqx_service = _get_emqx_service()
        if emqx_service is not None:
            if device.is_active:
                emqx_service.subscribe_device(device.id, device.to_dict_with_credentials())
            else:
                emqx_service.unsubscribe_device(device.id)

    return jsonify({
        'success': True,
        'message': f'Device {"activated" if device.is_active else "deactivated"}',
        'data': device.to_dict()
    })
