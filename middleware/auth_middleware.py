from functools import wraps
from flask import request, jsonify, g
from firebase.auth_service import AuthService
from firebase.firestore_service import FirestoreService

auth_service = AuthService()
firestore_service = FirestoreService()

def auth_required(f):
    """Decorator to require authentication for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get the ID token from Authorization header
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({
                'success': False,
                'error': 'Authorization header missing'
            }), 401
        
        # Extract token (format: "Bearer <token>")
        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify({
                'success': False,
                'error': 'Invalid authorization header format'
            }), 401
        
        # Verify token
        result = auth_service.verify_token(token)
        
        if not result.get('success'):
            return jsonify({
                'success': False,
                'error': result.get('error', 'Invalid token')
            }), 401
        
        # Get user data
        uid = result['data']['uid']
        user = firestore_service.get_user(uid)
        
        if not user:
            return jsonify({
                'success': False,
                'error': 'User not found'
            }), 404
        
        # Store user in Flask's g object
        g.current_user = user
        g.current_uid = uid
        
        return f(*args, **kwargs)
    
    return decorated_function

def get_current_user():
    """Get the current authenticated user from Flask's g object"""
    return getattr(g, 'current_user', None)

def role_required(*roles):
    """Decorator to require specific role(s)"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            
            if not user:
                return jsonify({
                    'success': False,
                    'error': 'Authentication required'
                }), 401
            
            user_role = user.get('role', 'viewer')
            
            if user_role not in roles and user_role != 'admin':
                return jsonify({
                    'success': False,
                    'error': 'Insufficient permissions'
                }), 403
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator

def permission_required(permission):
    """Decorator to require specific permission"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            
            if not user:
                return jsonify({
                    'success': False,
                    'error': 'Authentication required'
                }), 401
            
            # Admin has all permissions
            if user.get('role') == 'admin':
                return f(*args, **kwargs)
            
            # Check specific permission
            user_permissions = user.get('permissions', [])
            
            if permission not in user_permissions:
                return jsonify({
                    'success': False,
                    'error': f'Permission "{permission}" required'
                }), 403
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator
