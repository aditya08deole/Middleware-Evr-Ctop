from flask import Blueprint, request, jsonify, g
from firebase.firestore_service import FirestoreService
from firebase.auth_service import AuthService
from middleware import auth_required, role_required, handle_errors
from utils.encryption import get_encryption_service
from config import Config
from datetime import datetime

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')
firestore_service = FirestoreService()
auth_service = AuthService()
encryption_service = get_encryption_service()


@settings_bp.route('/', methods=['GET'])
@auth_required
@handle_errors
def get_settings():
    """Get all settings for current user"""
    from middleware import get_current_user
    user = get_current_user()
    
    if not user:
        return jsonify({
            'success': False,
            'error': 'User not authenticated'
        }), 401
    
    uid = user.get('id')
    
    # Get user data from Firestore
    user_data = firestore_service.get_user(uid)
    
    # Get scheduler settings from config
    scheduler_settings = {
        'interval_minutes': Config.SCHEDULER_INTERVAL_MINUTES,
        'enabled': True,  # Could be stored in Firestore
        'timezone': 'UTC'
    }
    
    settings = {
        'profile': {
            'display_name': user_data.get('display_name') if user_data else user.get('display_name'),
            'email': user.get('email'),
            'photo_url': user_data.get('photo_url') if user_data else None
        },
        'preferences': user_data.get('preferences', {}) if user_data else {
            'theme': 'light',
            'language': 'en',
            'timezone': 'UTC',
            'date_format': 'MM/DD/YYYY'
        },
        'notifications': user_data.get('notifications', {}) if user_data else {
            'email': True,
            'browser': True,
            'errors': True,
            'sync': False
        },
        'scheduler': scheduler_settings
    }
    
    return jsonify({
        'success': True,
        'data': settings
    })


@settings_bp.route('/profile', methods=['PUT'])
@auth_required
@handle_errors
def update_profile():
    """Update user profile"""
    from middleware import get_current_user
    user = get_current_user()
    
    if not user:
        return jsonify({
            'success': False,
            'error': 'User not authenticated'
        }), 401
    
    data = request.get_json()
    uid = user.get('id')
    
    updates = {}
    if 'display_name' in data:
        updates['display_name'] = data['display_name']
    if 'photo_url' in data:
        updates['photo_url'] = data['photo_url']
    
    # Update in Firebase Auth
    if 'display_name' in data:
        auth_service.update_user(uid, {'display_name': data['display_name']})
    
    # Update in Firestore
    if updates:
        firestore_service.update_user(uid, updates)
    
    return jsonify({
        'success': True,
        'message': 'Profile updated successfully'
    })


@settings_bp.route('/preferences', methods=['PUT'])
@auth_required
@handle_errors
def update_preferences():
    """Update user preferences"""
    from middleware import get_current_user
    user = get_current_user()
    
    if not user:
        return jsonify({
            'success': False,
            'error': 'User not authenticated'
        }), 401
    
    data = request.get_json()
    uid = user.get('id')
    
    preferences = {
        'theme': data.get('theme', 'light'),
        'language': data.get('language', 'en'),
        'timezone': data.get('timezone', 'UTC'),
        'date_format': data.get('date_format', 'MM/DD/YYYY')
    }
    
    firestore_service.update_user(uid, {'preferences': preferences})
    
    return jsonify({
        'success': True,
        'message': 'Preferences updated successfully'
    })


@settings_bp.route('/notifications', methods=['PUT'])
@auth_required
@handle_errors
def update_notifications():
    """Update notification settings"""
    from middleware import get_current_user
    user = get_current_user()
    
    if not user:
        return jsonify({
            'success': False,
            'error': 'User not authenticated'
        }), 401
    
    data = request.get_json()
    uid = user.get('id')
    
    notifications = {
        'email': data.get('email', True),
        'browser': data.get('browser', True),
        'errors': data.get('errors', True),
        'sync': data.get('sync', False)
    }
    
    firestore_service.update_user(uid, {'notifications': notifications})
    
    return jsonify({
        'success': True,
        'message': 'Notification settings updated successfully'
    })


@settings_bp.route('/password', methods=['PUT'])
@auth_required
@handle_errors
def change_password():
    """Change user password"""
    from middleware import get_current_user
    user = get_current_user()
    
    if not user:
        return jsonify({
            'success': False,
            'error': 'User not authenticated'
        }), 401
    
    data = request.get_json()
    new_password = data.get('new_password')
    
    if not new_password or len(new_password) < 6:
        return jsonify({
            'success': False,
            'error': 'Password must be at least 6 characters'
        }), 400
    
    uid = user.get('id')
    
    try:
        auth_service.update_user(uid, {'password': new_password})
        
        return jsonify({
            'success': True,
            'message': 'Password changed successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to change password: {str(e)}'
        }), 500


@settings_bp.route('/scheduler', methods=['PUT'])
@auth_required
@role_required('admin')
@handle_errors
def update_scheduler_settings():
    """Update scheduler settings (admin only)"""
    data = request.get_json()
    
    # Note: Scheduler interval requires app restart to take effect
    # In production, this would update a configuration store
    
    interval_minutes = data.get('interval_minutes')
    if interval_minutes:
        if not isinstance(interval_minutes, int) or interval_minutes < 1 or interval_minutes > 1440:
            return jsonify({
                'success': False,
                'error': 'Interval must be between 1 and 1440 minutes'
            }), 400
    
    settings = {
        'interval_minutes': interval_minutes,
        'enabled': data.get('enabled', True),
        'timezone': data.get('timezone', 'UTC')
    }
    
    # Store in Firestore settings collection
    firestore_service.create_or_update_settings('scheduler', settings)
    
    return jsonify({
        'success': True,
        'message': 'Scheduler settings updated. Note: Interval changes require app restart.'
    })
