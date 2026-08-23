from flask import Blueprint, request, jsonify, g
from firebase.auth_service import AuthService
from firebase.firestore_service import FirestoreService
from models.dataclass_models import UserModel
from middleware import auth_required, role_required, handle_errors

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')
auth_service = AuthService()
firestore_service = FirestoreService()

@auth_bp.route('/register', methods=['POST'])
@handle_errors
def register():
    """Register a new user"""
    data = request.get_json()
    
    # Validation
    required_fields = ['email', 'password', 'display_name']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({
                'success': False,
                'error': f'Missing required field: {field}'
            }), 400
    
    # Create user in Firebase Auth
    result = auth_service.create_user(
        email=data['email'],
        password=data['password'],
        display_name=data['display_name']
    )
    
    if not result.get('success'):
        return jsonify({
            'success': False,
            'error': result.get('error', 'Failed to create user')
        }), 400
    
    # Create user document in Firestore
    user_data = UserModel(
        id=result['uid'],
        email=result['email'],
        display_name=data['display_name'],
        role='viewer',  # Default role
        permissions=['devices:read', 'logs:read']
    )
    
    firestore_service.create_user(user_data.to_dict())
    
    return jsonify({
        'success': True,
        'message': 'User registered successfully',
        'data': {
            'uid': result['uid'],
            'email': result['email'],
            'display_name': result['display_name']
        }
    }), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    """
    Login endpoint
    Note: Actual authentication is handled by Firebase Client SDK on frontend
    This endpoint is for token verification
    """
    return jsonify({
        'success': True,
        'message': 'Use Firebase Auth SDK on client-side for login',
        'info': 'After client-side login, use /auth/verify to validate token'
    })

@auth_bp.route('/verify', methods=['POST'])
@handle_errors
def verify_token():
    """Verify Firebase ID token"""
    data = request.get_json()
    id_token = data.get('id_token')
    
    if not id_token:
        return jsonify({
            'success': False,
            'error': 'ID token is required'
        }), 400
    
    result = auth_service.verify_token(id_token)
    
    if not result.get('success'):
        return jsonify({
            'success': False,
            'error': result.get('error', 'Invalid token')
        }), 401
    
    # Get user data from Firestore
    uid = result['data']['uid']
    user = firestore_service.get_user(uid)
    
    return jsonify({
        'success': True,
        'data': {
            'uid': uid,
            'email': result['data'].get('email'),
            'user': user
        }
    })

@auth_bp.route('/me', methods=['GET'])
@auth_required
@handle_errors
def get_current_user_info():
    """Get current user information"""
    user = g.current_user
    
    return jsonify({
        'success': True,
        'data': user
    })

@auth_bp.route('/users', methods=['GET'])
@auth_required
@role_required('admin')
@handle_errors
def list_users():
    """List all users (admin only)"""
    users = auth_service.list_users(limit=100)
    
    return jsonify({
        'success': True,
        'data': users
    })

@auth_bp.route('/users/<uid>', methods=['GET'])
@auth_required
@role_required('admin')
@handle_errors
def get_user(uid):
    """Get a specific user (admin only)"""
    user = auth_service.get_user(uid)
    
    if not user:
        return jsonify({
            'success': False,
            'error': 'User not found'
        }), 404
    
    return jsonify({
        'success': True,
        'data': user
    })

@auth_bp.route('/users/<uid>', methods=['PUT'])
@auth_required
@role_required('admin')
@handle_errors
def update_user(uid):
    """Update a user (admin only)"""
    data = request.get_json()
    
    # Update in Firebase Auth
    auth_updates = {}
    if 'display_name' in data:
        auth_updates['display_name'] = data['display_name']
    if 'disabled' in data:
        auth_updates['disabled'] = data['disabled']
    
    if auth_updates:
        auth_service.update_user(uid, auth_updates)
    
    # Update in Firestore
    firestore_updates = {}
    if 'role' in data:
        firestore_updates['role'] = data['role']
    if 'permissions' in data:
        firestore_updates['permissions'] = data['permissions']
    if 'theme' in data:
        firestore_updates['theme'] = data['theme']
    if 'notifications' in data:
        firestore_updates['notifications'] = data['notifications']
    
    if firestore_updates:
        firestore_service.update_user(uid, firestore_updates)
    
    return jsonify({
        'success': True,
        'message': 'User updated successfully'
    })

@auth_bp.route('/users/<uid>', methods=['DELETE'])
@auth_required
@role_required('admin')
@handle_errors
def delete_user(uid):
    """Delete a user (admin only)"""
    # Delete from Firebase Auth
    auth_service.delete_user(uid)
    
    # Delete from Firestore (this would need to be implemented in FirestoreService)
    # For now, we'll just mark as inactive
    firestore_service.update_user(uid, {'is_active': False})
    
    return jsonify({
        'success': True,
        'message': 'User deleted successfully'
    })

@auth_bp.route('/reset-password', methods=['POST'])
@handle_errors
def reset_password():
    """Send password reset email"""
    data = request.get_json()
    email = data.get('email')
    
    if not email:
        return jsonify({
            'success': False,
            'error': 'Email is required'
        }), 400
    
    result = auth_service.reset_password(email)
    
    if result:
        return jsonify({
            'success': True,
            'message': 'Password reset email sent'
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Failed to send password reset email'
        }), 500
