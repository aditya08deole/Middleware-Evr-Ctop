import os

# Conditionally import device routes based on USE_FIREBASE setting
use_firebase = os.environ.get('USE_FIREBASE', 'false').lower() == 'true'

if use_firebase:
    from .device_routes_firestore import device_bp
else:
    from .device_routes import device_bp

from .data_routes import data_bp
from .auth_routes import auth_bp
from .analytics_routes import analytics_bp
from .settings_routes import settings_bp

__all__ = ['device_bp', 'data_bp', 'auth_bp', 'analytics_bp', 'settings_bp']
