from flask import Blueprint, jsonify, request
from middleware.auth_middleware import auth_required
from firebase.firestore_service import FirestoreService
from datetime import datetime, timedelta
import os

analytics_bp = Blueprint('analytics', __name__, url_prefix='/analytics')

# Initialize Firestore service
firestore_service = None

def get_firestore_service():
    """Get or initialize Firestore service"""
    global firestore_service
    if firestore_service is None:
        use_firebase = os.environ.get('USE_FIREBASE', 'false').lower() == 'true'
        if use_firebase:
            credentials_path = os.environ.get('FIREBASE_CREDENTIALS_PATH')
            if credentials_path and os.path.exists(credentials_path):
                firestore_service = FirestoreService(credentials_path)
    return firestore_service


@analytics_bp.route('/device-activity', methods=['GET'])
@auth_required
def get_device_activity():
    """Get device activity data for charts"""
    try:
        fs = get_firestore_service()
        
        if not fs:
            # Return sample data if Firestore not available
            return jsonify({
                'success': True,
                'data': {
                    'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                    'values': [12, 19, 3, 5, 2, 3, 15]
                }
            })
        
        # Get devices with last sync times
        devices = fs.list_devices(limit=100)
        
        # Aggregate activity by day (last 7 days)
        activity_data = {}
        for i in range(7):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            activity_data[date] = 0
        
        for device in devices:
            if device.get('last_sync_time'):
                sync_date = device['last_sync_time'][:10] if isinstance(device['last_sync_time'], str) else device['last_sync_time'].strftime('%Y-%m-%d')
                if sync_date in activity_data:
                    activity_data[sync_date] += 1
        
        # Sort by date
        sorted_dates = sorted(activity_data.keys(), reverse=True)
        sorted_values = [activity_data[date] for date in sorted_dates]
        
        return jsonify({
            'success': True,
            'data': {
                'labels': sorted_dates,
                'values': sorted_values
            }
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@analytics_bp.route('/success-failure-rate', methods=['GET'])
@auth_required
def get_success_failure_rate():
    """Get success/failure rate for charts"""
    try:
        fs = get_firestore_service()
        
        if not fs:
            # Return sample data if Firestore not available
            return jsonify({
                'success': True,
                'data': {
                    'success': 75,
                    'failure': 15,
                    'pending': 10
                }
            })
        
        # Get logs to calculate success/failure rate
        logs = fs.get_logs(limit=1000)
        
        success_count = 0
        failure_count = 0
        pending_count = 0
        
        for log in logs:
            status = log.get('status', '').lower()
            if status == 'success':
                success_count += 1
            elif status == 'error':
                failure_count += 1
            else:
                pending_count += 1
        
        return jsonify({
            'success': True,
            'data': {
                'success': success_count,
                'failure': failure_count,
                'pending': pending_count
            }
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@analytics_bp.route('/data-volume', methods=['GET'])
@auth_required
def get_data_volume():
    """Get data volume by device for charts"""
    try:
        fs = get_firestore_service()
        
        if not fs:
            # Return sample data if Firestore not available
            return jsonify({
                'success': True,
                'data': {
                    'labels': ['Device 1', 'Device 2', 'Device 3', 'Device 4', 'Device 5'],
                    'values': [120, 190, 30, 50, 20]
                }
            })
        
        # Get devices with their fetch counts
        devices = fs.list_devices(limit=20)
        
        labels = []
        values = []
        
        for device in devices:
            labels.append(device.get('name', 'Unknown'))
            values.append(device.get('total_fetches', 0))
        
        return jsonify({
            'success': True,
            'data': {
                'labels': labels,
                'values': values
            }
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@analytics_bp.route('/device-status', methods=['GET'])
@auth_required
def get_device_status():
    """Get device status distribution for charts"""
    try:
        fs = get_firestore_service()
        
        if not fs:
            # Return sample data if Firestore not available
            return jsonify({
                'success': True,
                'data': {
                    'active': 8,
                    'inactive': 2
                }
            })
        
        # Get devices and count active/inactive
        devices = fs.list_devices(limit=100)
        
        active_count = 0
        inactive_count = 0
        
        for device in devices:
            if device.get('is_active', False):
                active_count += 1
            else:
                inactive_count += 1
        
        return jsonify({
            'success': True,
            'data': {
                'active': active_count,
                'inactive': inactive_count
            }
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@analytics_bp.route('/device/<device_id>/activity', methods=['GET'])
@auth_required
def get_device_activity_detail(device_id):
    """Get activity data for a specific device"""
    try:
        fs = get_firestore_service()
        
        if not fs:
            return jsonify({
                'success': False,
                'error': 'Firestore not available'
            }), 500
        
        # Get device
        device = fs.get_device(device_id)
        if not device:
            return jsonify({
                'success': False,
                'error': 'Device not found'
            }), 404
        
        # Get logs for this device
        logs = fs.get_logs(device_id=device_id, limit=100)
        
        # Aggregate by day
        activity_data = {}
        for i in range(30):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            activity_data[date] = 0
        
        for log in logs:
            if log.get('created_at'):
                log_date = log['created_at'][:10] if isinstance(log['created_at'], str) else log['created_at'].strftime('%Y-%m-%d')
                if log_date in activity_data:
                    activity_data[log_date] += 1
        
        # Sort by date
        sorted_dates = sorted(activity_data.keys(), reverse=True)
        sorted_values = [activity_data[date] for date in sorted_dates]
        
        return jsonify({
            'success': True,
            'data': {
                'device_name': device.get('name'),
                'labels': sorted_dates,
                'values': sorted_values
            }
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
