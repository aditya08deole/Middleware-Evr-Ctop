from flask import Blueprint, request, jsonify, render_template
from models import db, Device, Log
from services import ThingSpeakService, PreprocessService, CTOPService
from middleware import handle_errors
from datetime import datetime

device_bp = Blueprint('devices_sqlite', __name__, url_prefix='/devices')

# Initialize services
thingspeak_service = ThingSpeakService()
preprocess_service = PreprocessService()
ctop_service = CTOPService()

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

@device_bp.route('/', methods=['POST'])
@handle_errors
def add_device():
    """Add a new device"""
    data = request.get_json()
    
    # Validation
    required_fields = ['name', 'device_type', 'channel_id', 'api_key', 'ctop_url_1', 'auth_token']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({
                'success': False,
                'error': f'Missing required field: {field}'
            }), 400
    
    try:
        device = Device(
            name=data['name'],
            device_type=data['device_type'],
            channel_id=data['channel_id'],
            api_key=data['api_key'],
            ctop_url_1=data['ctop_url_1'],
            ctop_url_2=data.get('ctop_url_2'),
            auth_token=data['auth_token'],
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            is_active=data.get('is_active', True),
            # EvaraTank specific fields
            tank_height=data.get('tank_height'),
            distance_field=data.get('distance_field'),
            temperature_field=data.get('temperature_field'),
            filtering_method=data.get('filtering_method', 'none'),
            filter_window=data.get('filter_window', 5),
            # EvaraFlow specific fields
            meter_reading_field=data.get('meter_reading_field'),
            flow_rate_field=data.get('flow_rate_field'),
            # EvaraValve specific fields
            liters_field=data.get('liters_field'),
            # EvaraTDS specific fields
            tds_field=data.get('tds_field')
        )
        
        db.session.add(device)
        db.session.commit()
        
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
        
        device.updated_at = datetime.utcnow()
        db.session.commit()
        
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

    # Step 1: Fetch from ThingSpeak
    success, raw_data, error = thingspeak_service.fetch_data(device_id)
    if not success:
        return False, f'Failed to fetch data: {error}', None

    # Step 2: Preprocess
    success, processed_data, error = preprocess_service.preprocess_data(device_id, raw_data)
    if not success:
        return False, f'Failed to preprocess data: {error}', None

    # Step 3: Transform to CTOP format
    transformed_data = preprocess_service.transform_to_ctop_format(device_id, processed_data)

    # Store processed data record
    preprocess_service.store_processed_data(device_id, raw_data, processed_data, transformed_data)

    # Step 4: Send each payload to CTOP
    results = []
    for payload in transformed_data:
        result = ctop_service.send_to_ctop(device_id, payload=payload)
        results.append(result)

    # Update sync timestamp
    device.updated_at = datetime.utcnow()
    db.session.commit()

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
    
    return jsonify({
        'success': True,
        'message': f'Device {"activated" if device.is_active else "deactivated"}',
        'data': device.to_dict()
    })
