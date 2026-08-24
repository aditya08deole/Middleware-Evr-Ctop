from flask import Blueprint, request, jsonify, render_template, current_app
from firebase.firestore_service import FirestoreService
from firebase.auth_service import AuthService
from models.dataclass_models import DeviceModel, LogModel
from services import ThingSpeakService, PreprocessService, CTOPService
from middleware import handle_errors
from middleware.validation_middleware import validate_device_data
from utils.encryption import get_encryption_service
from config import Config
from datetime import datetime
import json
from utils.local_device_store import local_device_store

device_bp = Blueprint('devices_firestore', __name__, url_prefix='/devices')

# Initialize services
firestore_service = FirestoreService()
auth_service = AuthService()
thingspeak_service = ThingSpeakService()
preprocess_service = PreprocessService()
ctop_service = CTOPService()
encryption_service = get_encryption_service()

@device_bp.route('/', methods=['GET'])
@handle_errors
def list_devices():
    """Get all devices (public - no auth required for dashboard)"""
    is_active = request.args.get('is_active')
    # Get devices from Local Mirror Cache (0 Firebase Reads)
    devices = local_device_store.get_devices(active_only=(is_active.lower() == 'true') if is_active else False)

    return jsonify({
        'success': True,
        'data': devices
    })

@device_bp.route('/<device_id>', methods=['GET'])
@handle_errors
def get_device(device_id):
    # Get from Local Mirror Cache (0 Firebase Reads)
    device = local_device_store.get_device_by_id(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 404

    return jsonify({
        'success': True,
        'data': device
    })

@device_bp.route('/', methods=['POST'])
@validate_device_data
@handle_errors
def add_device():
    """Add a new device (public - no auth required)"""
    data = request.get_json()  # retrieve validated JSON payload
    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    if 'device_type' not in data or not data['device_type']:
        return jsonify({
            'success': False,
            'error': 'Missing required field: device_type'
        }), 400

    # Determine which sensitive fields to encrypt based on data source
    data_source = data.get('data_source', 'thingspeak')
    encrypt_fields = ['api_key', 'auth_token']
    if data_source == 'emqx' and data.get('emqx_password'):
        encrypt_fields.append('emqx_password')

    # Encrypt sensitive fields before storing
    encrypted_data = encryption_service.encrypt_dict(data, encrypt_fields)

    device_data = DeviceModel(
        name=encrypted_data['name'],
        channel_id=encrypted_data['channel_id'],
        api_key=encrypted_data['api_key'],
        ctop_url_1=encrypted_data['ctop_url_1'],
        ctop_url_2=encrypted_data.get('ctop_url_2'),
        auth_token=encrypted_data['auth_token'],
        latitude=encrypted_data.get('latitude'),
        longitude=encrypted_data.get('longitude'),
        is_active=encrypted_data.get('is_active', True),
        device_type=encrypted_data['device_type'],
        tank_height=encrypted_data.get('tank_height'),
        distance_field=encrypted_data.get('distance_field'),
        temperature_field=encrypted_data.get('temperature_field'),
        meter_reading_field=encrypted_data.get('meter_reading_field'),
        flow_rate_field=encrypted_data.get('flow_rate_field'),
        liters_field=encrypted_data.get('liters_field'),
        tds_field=encrypted_data.get('tds_field'),
        filtering_method=encrypted_data.get('filtering_method', 'none'),
        filter_window=encrypted_data.get('filter_window', 5),
        data_source=encrypted_data.get('data_source', 'thingspeak'),
        emqx_broker_url=encrypted_data.get('emqx_broker_url'),
        emqx_port=encrypted_data.get('emqx_port', 1883),
        emqx_username=encrypted_data.get('emqx_username'),
        emqx_password=encrypted_data.get('emqx_password'),
        emqx_topic=encrypted_data.get('emqx_topic'),
        emqx_use_tls=encrypted_data.get('emqx_use_tls', False),
    )

    try:
        # Issue #6 fix: Step 1 - Write to Firebase
        device_id = firestore_service.create_device(device_data.to_dict())

        # Issue #6 fix: Step 2 - Write to local mirror
        full_device_data = {'id': device_id, **device_data.to_dict()}
        if not local_device_store.add_device(full_device_data):
            # Issue #6 fix: Rollback - Remove from Firebase if local write fails
            current_app.logger.error(f"Failed to add device {device_id} to local store. Rolling back Firebase write.")
            try:
                firestore_service.delete_device(device_id)
            except Exception as rollback_error:
                current_app.logger.error(f"Rollback failed for device {device_id}: {rollback_error}")
            return jsonify({'success': False, 'error': 'Failed to persist to local cache'}), 500

        # If EMQX device, start MQTT subscription immediately
        if data_source == 'emqx':
            try:
                from services import EMQXService
                from utils.scheduler_firestore import emqx_service
                emqx_service.subscribe_device(device_id, full_device_data)
                current_app.logger.info(f"EMQX subscription started for device {device_id}")
            except Exception as mqtt_err:
                current_app.logger.warning(f"EMQX subscription failed for device {device_id}: {mqtt_err}")

        # Trigger an immediate single pipeline run so status is set right away
        # (success / inactive / error) instead of staying PENDING until the next
        # 15-second scheduler tick.
        try:
            from utils.scheduler_firestore import process_device_safe
            import threading
            t = threading.Thread(
                target=process_device_safe,
                args=(device_id, full_device_data),
                daemon=True,
                name=f"initial-sync-{device_id}"
            )
            t.start()
            current_app.logger.info(f"Initial sync triggered for new device {device_id}")
        except Exception as sync_err:
            current_app.logger.warning(f"Initial sync thread failed for device {device_id}: {sync_err}")

        # Log the creation
        firestore_service.create_log(LogModel(
            device_id=device_id,
            log_type='device',
            status='success',
            message=f'Device created via web form (platform: {data_source})'
        ).to_dict())

        return jsonify({
            'success': True,
            'message': 'Device added successfully',
            'data': {'id': device_id, **device_data.to_dict()}
        }), 201

    except Exception as e:
        current_app.logger.error(f"Failed to add device: {e}")
        return jsonify({
            'success': False,
            'error': f'Failed to add device: {str(e)}'
        }), 500

@device_bp.route('/<device_id>', methods=['PUT'])
@validate_device_data
@handle_errors
def update_device(device_id):
    """Update a device"""
    device = firestore_service.get_device(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 404

    data = request.get_json()

    try:
        # Encrypt sensitive fields if provided
        fields_to_encrypt = []
        if 'api_key' in data:
            fields_to_encrypt.append('api_key')
        if 'auth_token' in data:
            fields_to_encrypt.append('auth_token')
        if 'emqx_password' in data and data['emqx_password']:
            fields_to_encrypt.append('emqx_password')

        if fields_to_encrypt:
            data = encryption_service.encrypt_dict(data, fields_to_encrypt)

        # Build update dict — only include provided fields
        allowed_fields = [
            'name', 'channel_id', 'api_key', 'ctop_url_1', 'ctop_url_2',
            'auth_token', 'latitude', 'longitude', 'is_active',
            'device_type', 'tank_height', 'distance_field', 'temperature_field',
            'meter_reading_field', 'flow_rate_field', 'liters_field', 'tds_field',
            'filtering_method', 'filter_window',
            'data_source', 'emqx_broker_url', 'emqx_port', 'emqx_username',
            'emqx_password', 'emqx_topic', 'emqx_use_tls'
        ]
        updates = {k: data[k] for k in allowed_fields if k in data}

        firestore_service.update_device(device_id, updates)

        # Also update Local Mirror Cache immediately after Firebase succeeds
        if not local_device_store.update_device_fields(device_id, updates):
            current_app.logger.warning(f"Failed to update device {device_id} in local mirror, but Firebase succeeded.")

        # Refresh MQTT subscription if EMQX device
        updated_device = local_device_store.get_device_by_id(device_id) or {**device, **updates}
        if updated_device.get('data_source') == 'emqx':
            try:
                from utils.scheduler_firestore import emqx_service
                if updated_device.get('is_active', True):
                    emqx_service.subscribe_device(device_id, updated_device)
                else:
                    emqx_service.unsubscribe_device(device_id)
            except Exception as mqtt_err:
                current_app.logger.warning(f"EMQX subscription update failed for {device_id}: {mqtt_err}")

        return jsonify({
            'success': True,
            'message': 'Device updated successfully'
        })

    except Exception as e:
        current_app.logger.error(f"Failed to update device {device_id}: {e}")
        return jsonify({
            'success': False,
            'error': f'Failed to update device: {str(e)}'
        }), 500

@device_bp.route('/<device_id>', methods=['DELETE'])
@handle_errors
def delete_device(device_id):
    """Delete a device"""
    device = firestore_service.get_device(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 404

    try:
        # Stop MQTT subscription and clean up background loop/threads if EMQX device
        try:
            from utils.scheduler_firestore import emqx_service
            emqx_service.unsubscribe_device(device_id)
        except Exception as mqtt_err:
            current_app.logger.warning(f"Failed to unsubscribe EMQX device on delete {device_id}: {mqtt_err}")

        firestore_service.delete_device(device_id)

        # Also remove from Local Mirror Cache immediately
        if not local_device_store.delete_device(device_id):
            current_app.logger.warning(f"Failed to delete device {device_id} from local mirror, but Firebase succeeded.")

        return jsonify({
            'success': True,
            'message': 'Device deleted successfully'
        })

    except Exception as e:
        current_app.logger.error(f"Failed to delete device {device_id}: {e}")
        return jsonify({
            'success': False,
            'error': f'Failed to delete device: {str(e)}'
        }), 500

def _sync_device_data(device_id, device):
    """Core pipeline: fetch → preprocess → transform → send to CTOP"""

    # Step 1: Fetch data — route by data source platform
    data_source = device.get('data_source', 'thingspeak')
    if data_source == 'emqx':
        from utils.scheduler_firestore import emqx_service
        success, raw_data, error = emqx_service.fetch_data(device_id, device)
    else:
        success, raw_data, error = thingspeak_service.fetch_data(device_id, device)

    if not success:
        local_device_store.update_device_fields(device_id, {
            'last_status': 'error',
            'last_error': error,
            'last_sync_time': datetime.utcnow().isoformat()
        })
        return False, f'Failed to fetch data: {error}', None

    # Step 2: Preprocess
    success, processed_data, error = preprocess_service.preprocess_data(device_id, raw_data, device_data=device)
    if not success:
        return False, f'Failed to preprocess data: {error}', None

    # Step 3: Transform to CTOP format
    transformed_data = preprocess_service.transform_to_ctop_format(device_id, processed_data, device_data=device)

    # Ensure we only send NEW entries to CTOP to prevent duplicates
    last_entry_id = str(device.get('last_processed_entry_id', ''))
    new_processed_data = []
    new_transformed_data = []
    
    for i, entry in enumerate(processed_data):
        entry_id = str(entry.get('entry_id', ''))
        # Compare as integers to avoid lexicographic string bug ("10" <= "9" is True as string)
        try:
            if last_entry_id and int(entry_id) <= int(last_entry_id):
                continue
        except (ValueError, TypeError):
            if last_entry_id and entry_id <= last_entry_id:
                continue
            
        new_processed_data.append(entry)
        if i < len(transformed_data):
            new_transformed_data.append(transformed_data[i])
            
    if not new_transformed_data:
        # Nothing new to send
        return True, None, []

    # Step 4: Send each payload to CTOP
    results = []
    latest_entry_id = last_entry_id
    
    for i, payload in enumerate(new_transformed_data):
        result = ctop_service.send_to_ctop(device_id, device, payload=payload)
        results.append(result)
        if result.get('success'):
            latest_entry_id = str(new_processed_data[i].get('entry_id', latest_entry_id))

    # Update Local Mirror Cache (0 Firebase Writes - will sync back later)
    local_device_store.update_entry_id(device_id, latest_entry_id, last_status='success')
    local_device_store.increment_device_stats(device_id, send_success=True)

    return True, None, results

@device_bp.route('/<device_id>/fetch', methods=['POST'])
@handle_errors
def manual_fetch(device_id):
    """Manually trigger data fetch for a device"""
    # Use Local Mirror
    device = local_device_store.get_device_by_id(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 400

    try:
        success, error, results = _sync_device_data(device_id, device)

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

@device_bp.route('/<device_id>/logs', methods=['GET'])
@handle_errors
def get_device_logs(device_id):
    """Get logs for a specific device"""
    device = local_device_store.get_device_by_id(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 404

    limit = request.args.get('limit', 50, type=int)
    log_type = request.args.get('type')

    logs = firestore_service.get_logs(device_id=device_id, log_type=log_type, limit=limit)

    return jsonify({
        'success': True,
        'data': logs
    })

@device_bp.route('/<device_id>/toggle', methods=['POST'])
@handle_errors
def toggle_device(device_id):
    """Toggle device active status"""
    device = local_device_store.get_device_by_id(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 404

    new_status = not device.get('is_active', True)

    firestore_service.update_device(device_id, {
        'is_active': new_status
    })

    # Update Local Mirror Cache immediately
    local_device_store.update_device_fields(device_id, {'is_active': new_status})

    return jsonify({
        'success': True,
        'message': f'Device {"activated" if new_status else "deactivated"}',
        'data': {'is_active': new_status}
    })

@device_bp.route('/<device_id>/test-connection', methods=['POST'])
@handle_errors
def test_connection(device_id):
    """Test data source connection for a device (ThingSpeak or EMQX)"""
    device = local_device_store.get_device_by_id(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 404

    data_source = device.get('data_source', 'thingspeak')

    try:
        if data_source == 'emqx':
            from utils.scheduler_firestore import emqx_service
            # Check status of MQTT connection
            status = emqx_service.get_status()
            is_connected = str(device_id) in status.get('connected_devices', [])
            
            if not is_connected:
                # Attempt to subscribe/connect
                connected = emqx_service.subscribe_device(device_id, device)
                if not connected:
                    return jsonify({
                        'success': False,
                        'error': f'Could not connect to EMQX broker at {device.get("emqx_broker_url")}'
                    }), 400

            return jsonify({
                'success': True,
                'message': 'EMQX connection and subscription active',
                'data': {
                    'platform': 'emqx',
                    'broker_url': device.get('emqx_broker_url'),
                    'topic': device.get('emqx_topic'),
                    'port': device.get('emqx_port', 1883)
                }
            })
        else:
            success, data, error = thingspeak_service.fetch_data(device_id, device)

            if success:
                return jsonify({
                    'success': True,
                    'message': 'ThingSpeak connection test successful',
                    'data': {
                        'platform': 'thingspeak',
                        'channel_id': device.get('channel_id'),
                        'entries_found': len(data.get('feeds', [])) if data else 0
                    }
                })
            else:
                return jsonify({
                    'success': False,
                    'error': f'ThingSpeak connection test failed: {error}'
                }), 400

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Connection test error: {str(e)}'
        }), 500

@device_bp.route('/<device_id>/sync', methods=['POST'])
@handle_errors
def trigger_sync(device_id):
    """Manually trigger data sync for a device"""
    device = local_device_store.get_device_by_id(device_id)

    if not device:
        return jsonify({
            'success': False,
            'error': 'Device not found'
        }), 404

    if not device.get('is_active', True):
        return jsonify({
            'success': False,
            'error': 'Device is inactive'
        }), 400

    try:
        success, error, results = _sync_device_data(device_id, device)

        if not success:
            return jsonify({
                'success': False,
                'error': error
            }), 400

        return jsonify({
            'success': True,
            'message': 'Sync completed successfully',
            'data': {
                'ctop_results': results
            }
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Sync failed: {str(e)}'
        }), 500

@device_bp.route('/refresh-mirror', methods=['POST'])
@handle_errors
def refresh_mirror():
    """Force refresh the local mirror cache from Firestore"""
    try:
        local_device_store.sync_from_firebase()
        return jsonify({
            'success': True,
            'message': 'Local mirror refreshed from Firebase successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to refresh mirror: {str(e)}'
        }), 500

@device_bp.route('/sync-stats', methods=['POST'])
@handle_errors
def sync_stats():
    """Force push local stats to Firestore (usually hourly)"""
    try:
        local_device_store.sync_to_firebase()
        return jsonify({
            'success': True,
            'message': 'Local stats pushed to Firebase successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to push stats: {str(e)}'
        }), 500
