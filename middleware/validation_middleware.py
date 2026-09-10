from functools import wraps
from flask import request, jsonify
import re
from typing import Dict, Any, List

def validate_device_data(f):
    """Decorator to validate device data (supports ThingSpeak and EMQX platforms)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400
        
        # Determine data source platform (default: thingspeak for backward compatibility)
        data_source = data.get('data_source', 'thingspeak')
        
        if data_source not in ('thingspeak', 'emqx'):
            return jsonify({
                'success': False,
                'error': "Invalid data_source. Must be 'thingspeak' or 'emqx'"
            }), 400
        
        # Common required fields (both platforms need these)
        common_required = ['name', 'ctop_url_1', 'auth_token']
        for field in common_required:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        # Name validation
        if len(data['name']) < 3 or len(data['name']) > 100:
            return jsonify({
                'success': False,
                'error': 'Device name must be between 3 and 100 characters'
            }), 400
        
        # Platform-specific validation
        if data_source == 'thingspeak':
            # ThingSpeak requires channel_id and api_key
            ts_required = ['channel_id', 'api_key']
            for field in ts_required:
                if field not in data or not data[field]:
                    return jsonify({
                        'success': False,
                        'error': f'Missing required field: {field}'
                    }), 400
            
            # Channel ID validation (should be numeric)
            if not str(data['channel_id']).isdigit():
                return jsonify({
                    'success': False,
                    'error': 'Channel ID must be numeric'
                }), 400
            
            # API Key validation (ThingSpeak API keys are typically 16 characters)
            if len(data['api_key']) < 8 or len(data['api_key']) > 32:
                return jsonify({
                    'success': False,
                    'error': 'API key must be between 8 and 32 characters'
                }), 400
        
        elif data_source == 'emqx':
            # EMQX requires broker_url and topic
            emqx_required = ['emqx_broker_url', 'emqx_topic']
            for field in emqx_required:
                if field not in data or not data[field]:
                    return jsonify({
                        'success': False,
                        'error': f'Missing required EMQX field: {field}'
                    }), 400
            
            # Validate broker URL (should be a hostname or IP, not a full URL)
            broker_url = data['emqx_broker_url']
            if len(broker_url) < 3:
                return jsonify({
                    'success': False,
                    'error': 'EMQX broker URL must be at least 3 characters'
                }), 400
            
            # Validate port if provided
            if data.get('emqx_port') is not None:
                try:
                    port = int(data['emqx_port'])
                    if port < 1 or port > 65535:
                        return jsonify({
                            'success': False,
                            'error': 'EMQX port must be between 1 and 65535'
                        }), 400
                except (ValueError, TypeError):
                    return jsonify({
                        'success': False,
                        'error': 'EMQX port must be a valid number'
                    }), 400
            
            # Validate topic (MQTT topic should not be empty and should not start with $)
            topic = data['emqx_topic']
            if topic.startswith('$'):
                return jsonify({
                    'success': False,
                    'error': 'EMQX topic cannot start with $ (reserved for system topics)'
                }), 400

            # Validate QoS if provided (standard MQTT levels: 0, 1, 2)
            if data.get('emqx_qos') is not None:
                try:
                    qos = int(data['emqx_qos'])
                    if qos not in (0, 1, 2):
                        return jsonify({
                            'success': False,
                            'error': 'EMQX QoS must be 0, 1, or 2'
                        }), 400
                except (ValueError, TypeError):
                    return jsonify({
                        'success': False,
                        'error': 'EMQX QoS must be a valid number'
                    }), 400

            # emqx_tls_insecure (skip certificate verification) must only be
            # used together with TLS — it's meaningless, and easy to mistake
            # for "secure" otherwise, when TLS itself is off.
            if data.get('emqx_tls_insecure') and not data.get('emqx_use_tls'):
                return jsonify({
                    'success': False,
                    'error': 'emqx_tls_insecure requires emqx_use_tls to be enabled'
                }), 400

            # Set default empty values for ThingSpeak fields to avoid downstream errors
            if 'channel_id' not in data or not data['channel_id']:
                data['channel_id'] = 'emqx'
            if 'api_key' not in data or not data['api_key']:
                data['api_key'] = 'not_used_emqx'
        
        # URL validation (common)
        if not is_valid_url(data['ctop_url_1']):
            return jsonify({
                'success': False,
                'error': 'Invalid ctop_url_1 format'
            }), 400
        
        # Optional ctop_url_2 validation
        if data.get('ctop_url_2') and not is_valid_url(data['ctop_url_2']):
            return jsonify({
                'success': False,
                'error': 'Invalid ctop_url_2 format'
            }), 400
        
        # Auth token validation (common)
        if len(data['auth_token']) < 8:
            return jsonify({
                'success': False,
                'error': 'Auth token must be at least 8 characters'
            }), 400
        
        # Latitude/Longitude validation if provided
        if data.get('latitude') is not None:
            try:
                lat = float(data['latitude'])
                if not -90 <= lat <= 90:
                    return jsonify({
                        'success': False,
                        'error': 'Latitude must be between -90 and 90'
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    'success': False,
                    'error': 'Invalid latitude format'
                }), 400
        
        if data.get('longitude') is not None:
            try:
                lon = float(data['longitude'])
                if not -180 <= lon <= 180:
                    return jsonify({
                        'success': False,
                        'error': 'Longitude must be between -180 and 180'
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    'success': False,
                    'error': 'Invalid longitude format'
                }), 400

        return f(*args, **kwargs)
    
    return decorated_function


def validate_user_data(f):
    """Decorator to validate user data"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400
        
        # Email validation
        if 'email' in data:
            if not is_valid_email(data['email']):
                return jsonify({
                    'success': False,
                    'error': 'Invalid email format'
                }), 400
        
        # Password validation
        if 'password' in data:
            if len(data['password']) < 6:
                return jsonify({
                    'success': False,
                    'error': 'Password must be at least 6 characters'
                }), 400
        
        # Display name validation
        if 'display_name' in data:
            if len(data['display_name']) < 2 or len(data['display_name']) > 50:
                return jsonify({
                    'success': False,
                    'error': 'Display name must be between 2 and 50 characters'
                }), 400
        
        # Role validation
        if 'role' in data:
            valid_roles = ['admin', 'operator', 'viewer']
            if data['role'] not in valid_roles:
                return jsonify({
                    'success': False,
                    'error': f'Invalid role. Must be one of: {", ".join(valid_roles)}'
                }), 400
        
        return f(*args, **kwargs)
    
    return decorated_function


def validate_log_query(f):
    """Decorator to validate log query parameters"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Validate limit parameter
        limit = request.args.get('limit', 100, type=int)
        if limit < 1 or limit > 1000:
            return jsonify({
                'success': False,
                'error': 'Limit must be between 1 and 1000'
            }), 400
        
        # Validate log_type if provided
        log_type = request.args.get('log_type')
        if log_type:
            valid_types = ['thingspeak_fetch', 'preprocess', 'ctop_send', 'scheduler', 'device']
            if log_type not in valid_types:
                return jsonify({
                    'success': False,
                    'error': f'Invalid log_type. Must be one of: {", ".join(valid_types)}'
                }), 400
        
        return f(*args, **kwargs)
    
    return decorated_function


def validate_settings_data(f):
    """Decorator to validate settings data"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400
        
        # Scheduler interval validation
        if 'scheduler' in data and 'interval_minutes' in data['scheduler']:
            interval = data['scheduler']['interval_minutes']
            if not isinstance(interval, int) or interval < 1 or interval > 1440:
                return jsonify({
                    'success': False,
                    'error': 'Scheduler interval must be between 1 and 1440 minutes'
                }), 400
        
        # Timeout validation
        if 'thingspeak' in data and 'timeout' in data['thingspeak']:
            timeout = data['thingspeak']['timeout']
            if not isinstance(timeout, int) or timeout < 5 or timeout > 300:
                return jsonify({
                    'success': False,
                    'error': 'ThingSpeak timeout must be between 5 and 300 seconds'
                }), 400
        
        if 'ctop' in data and 'timeout' in data['ctop']:
            timeout = data['ctop']['timeout']
            if not isinstance(timeout, int) or timeout < 5 or timeout > 300:
                return jsonify({
                    'success': False,
                    'error': 'CTOP timeout must be between 5 and 300 seconds'
                }), 400
        
        # Retention days validation
        if 'retention' in data:
            if 'logs_days' in data['retention']:
                days = data['retention']['logs_days']
                if not isinstance(days, int) or days < 1 or days > 365:
                    return jsonify({
                        'success': False,
                        'error': 'Logs retention must be between 1 and 365 days'
                    }), 400
            
            if 'processed_data_days' in data['retention']:
                days = data['retention']['processed_data_days']
                if not isinstance(days, int) or days < 1 or days > 90:
                    return jsonify({
                        'success': False,
                        'error': 'Processed data retention must be between 1 and 90 days'
                    }), 400
        
        return f(*args, **kwargs)
    
    return decorated_function


# Helper functions
def is_valid_url(url: str) -> bool:
    """Validate URL format"""
    if not url:
        return False
    
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    
    return re.match(url_pattern, url) is not None


def is_valid_email(email: str) -> bool:
    """Validate email format"""
    if not email:
        return False
    
    email_pattern = re.compile(
        r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    )
    
    return re.match(email_pattern, email) is not None


def sanitize_string(input_string: str, max_length: int = 1000) -> str:
    """Sanitize string input to prevent injection"""
    if not input_string:
        return ""
    
    # Remove potentially dangerous characters
    sanitized = re.sub(r'[<>"\']', '', str(input_string))
    
    # Truncate to max length
    return sanitized[:max_length]


def validate_pagination(f):
    """Decorator to validate pagination parameters"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        if page < 1:
            return jsonify({
                'success': False,
                'error': 'Page must be greater than 0'
            }), 400
        
        if per_page < 1 or per_page > 100:
            return jsonify({
                'success': False,
                'error': 'Per page must be between 1 and 100'
            }), 400
        
        return f(*args, **kwargs)
    
    return decorated_function
