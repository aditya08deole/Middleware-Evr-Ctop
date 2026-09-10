import os

# Conditionally import device routes based on USE_FIREBASE setting
use_firebase = os.environ.get("USE_FIREBASE", "false").lower() == "true"

if use_firebase:
    from .device_routes_firestore import device_bp
else:
    from .device_routes import device_bp

# These come after the USE_FIREBASE-gated block above by necessity (device_bp
# must be resolved first) — noqa: E402 is correct here, not reordering.
from .data_routes import data_bp  # noqa: E402
from .auth_routes import auth_bp  # noqa: E402
from .analytics_routes import analytics_bp  # noqa: E402
from .settings_routes import settings_bp  # noqa: E402

__all__ = ["device_bp", "data_bp", "auth_bp", "analytics_bp", "settings_bp"]
