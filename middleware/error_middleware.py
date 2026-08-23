from flask import jsonify
from functools import wraps

def handle_errors(f):
    """Decorator to handle errors consistently"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValueError as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 400
        except KeyError as e:
            return jsonify({
                'success': False,
                'error': f'Missing required field: {str(e)}'
            }), 400
        except PermissionError as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 403
        except Exception as e:
            return jsonify({
                'success': False,
                'error': 'An unexpected error occurred',
                'details': str(e)
            }), 500
    
    return decorated_function
