import os

# Conditionally import scheduler based on USE_FIREBASE setting
use_firebase = os.environ.get("USE_FIREBASE", "false").lower() == "true"

if use_firebase:
    from .scheduler_firestore import scheduler, init_scheduler
else:
    from .scheduler import scheduler, init_scheduler

# Comes after the USE_FIREBASE-gated block above by necessity — noqa: E402
# is correct here, not reordering.
from .helpers import format_timestamp, validate_url  # noqa: E402

__all__ = ["scheduler", "init_scheduler", "format_timestamp", "validate_url"]
