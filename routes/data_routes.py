from flask import Blueprint, jsonify
from models import db, Log, ProcessedData
from services import ThingSpeakService, PreprocessService, CTOPService

data_bp = Blueprint('data', __name__, url_prefix='/data')

# Initialize services
thingspeak_service = ThingSpeakService()
preprocess_service = PreprocessService()
ctop_service = CTOPService()

@data_bp.route('/fetch-all', methods=['POST'])
def fetch_all_devices():
    """Trigger the full pipeline (fetch → preprocess → CTOP) for all active devices"""
    import os
    use_firebase = os.environ.get('USE_FIREBASE', 'false').lower() == 'true'

    try:
        if use_firebase:
            from firebase.firestore_service import FirestoreService
            from routes.device_routes_firestore import _sync_device_data
            fs = FirestoreService()
            devices = fs.list_devices(filters={'is_active': True}, limit=1000)

            results = {}
            for device in devices:
                device_id = device.get('id')
                try:
                    success, error, ctop_results = _sync_device_data(device_id, device)
                    results[device_id] = {
                        'success': success,
                        'error': error,
                        'device_name': device.get('name'),
                        'ctop_results': ctop_results
                    }
                except Exception as e:
                    results[device_id] = {
                        'success': False,
                        'error': str(e),
                        'device_name': device.get('name')
                    }
        else:
            from models import Device
            devices = Device.query.filter_by(is_active=True).all()
            results = {}
            for device in devices:
                try:
                    if getattr(device, 'data_source', 'thingspeak') == 'emqx':
                        from services import EMQXService
                        emqx_svc = EMQXService()
                        success, raw_data, error = emqx_svc.fetch_data(device.id)
                    else:
                        success, raw_data, error = thingspeak_service.fetch_data(device.id)
                    results[str(device.id)] = {
                        'success': success,
                        'error': error,
                        'device_name': device.name
                    }
                except Exception as dev_err:
                    results[str(device.id)] = {
                        'success': False,
                        'error': str(dev_err),
                        'device_name': device.name
                    }

        summary = {
            'total_devices': len(results),
            'successful': sum(1 for r in results.values() if r.get('success')),
            'failed': sum(1 for r in results.values() if not r.get('success')),
            'details': results
        }

        return jsonify({
            'success': True,
            'data': summary
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to fetch data: {str(e)}'
        }), 500


@data_bp.route('/logs', methods=['GET'])
def get_all_logs():
    """Get all logs with optional filtering"""
    limit = 100
    log_type = None
    device_id = None
    
    # Get query parameters
    from flask import request
    limit = request.args.get('limit', 100, type=int)
    log_type = request.args.get('type')
    device_id = request.args.get('device_id', type=int)
    
    query = Log.query
    
    if log_type:
        query = query.filter_by(log_type=log_type)
    
    if device_id:
        query = query.filter_by(device_id=device_id)
    
    logs = query.order_by(Log.created_at.desc()).limit(limit).all()
    
    return jsonify({
        'success': True,
        'data': [log.to_dict() for log in logs]
    })

@data_bp.route('/processed', methods=['GET'])
def get_processed_data():
    """Get processed data with optional filtering"""
    from flask import request
    
    limit = request.args.get('limit', 100, type=int)
    device_id = request.args.get('device_id', type=int)
    
    query = ProcessedData.query
    
    if device_id:
        query = query.filter_by(device_id=device_id)
    
    data = query.order_by(ProcessedData.created_at.desc()).limit(limit).all()
    
    return jsonify({
        'success': True,
        'data': [item.to_dict() for item in data]
    })

@data_bp.route('/stats', methods=['GET'])
def get_stats():
    """Get system statistics (supports both SQLite and Firestore)"""
    import os
    use_firebase = os.environ.get('USE_FIREBASE', 'false').lower() == 'true'
    
    if use_firebase:
        # Use Firestore
        from firebase.firestore_service import FirestoreService
        fs = FirestoreService()
        
        if fs.is_initialized():
            devices = fs.list_devices(limit=1000)
            total_devices = len(devices)
            active_devices = sum(1 for d in devices if d.get('is_active', False))
            
            logs = fs.get_logs(limit=1000)
            total_logs = len(logs)
            error_logs = sum(1 for l in logs if l.get('status') == 'error')
            
            return jsonify({
                'success': True,
                'data': {
                    'total_devices': total_devices,
                    'active_devices': active_devices,
                    'total_logs': total_logs,
                    'error_logs': error_logs,
                    'total_processed': 0  # Firestore doesn't have processed data table
                }
            })
    
    # Fallback to SQLite
    from models import Device
    
    total_devices = Device.query.count()
    active_devices = Device.query.filter_by(is_active=True).count()
    
    total_logs = Log.query.count()
    error_logs = Log.query.filter_by(status='error').count()
    
    total_processed = ProcessedData.query.count()
    
    return jsonify({
        'success': True,
        'data': {
            'total_devices': total_devices,
            'active_devices': active_devices,
            'total_logs': total_logs,
            'error_logs': error_logs,
            'total_processed': total_processed
        }
    })
