import os
from flask import Blueprint, request, jsonify
from firebase.firestore_service import FirestoreService
from middleware import handle_errors
from config import Config

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")
firestore_service = FirestoreService()

# User accounts (profile/preferences/notifications/password) were removed
# from this blueprint: they depended on a logged-in Firebase user, but the
# dashboard's login page authenticates against a different Firebase project
# than the server verifies against, so no session here can ever be valid —
# see the full-stack audit (2 Oct 2026) for the underlying cross-project
# mismatch. Only the scheduler control survives, since it doesn't need a
# user identity and can be made to actually work.


def _get_active_scheduler_module():
    """Whichever scheduler backend is actually running (USE_FIREBASE)."""
    if os.environ.get("USE_FIREBASE", "false").lower() == "true":
        from utils import scheduler_firestore as module
    else:
        from utils import scheduler as module
    return module


@settings_bp.route("/", methods=["GET"])
@handle_errors
def get_settings():
    """Get the current scheduler configuration."""
    return jsonify(
        {
            "success": True,
            "data": {
                "scheduler": {
                    "interval_seconds": Config.SCHEDULER_INTERVAL_SECONDS,
                    "interval_minutes": round(Config.SCHEDULER_INTERVAL_SECONDS / 60, 2),
                }
            },
        }
    )


@settings_bp.route("/scheduler", methods=["PUT"])
@handle_errors
def update_scheduler_settings():
    """
    Update the scheduler's polling interval and apply it immediately —
    previously this only wrote a value to Firestore that nothing ever read
    back; reschedule_job() is now actually called on the live scheduler.
    """
    data = request.get_json() or {}

    interval_minutes = data.get("interval_minutes")
    if interval_minutes is None:
        return (
            jsonify({"success": False, "error": "interval_minutes is required"}),
            400,
        )

    try:
        interval_minutes = float(interval_minutes)
    except (TypeError, ValueError):
        return (
            jsonify({"success": False, "error": "interval_minutes must be a number"}),
            400,
        )

    if interval_minutes < (1 / 60) or interval_minutes > 1440:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Interval must be between 1 second and 1440 minutes",
                }
            ),
            400,
        )

    module = _get_active_scheduler_module()
    interval_seconds = interval_minutes * 60

    if os.environ.get("USE_FIREBASE", "false").lower() == "true":
        ok = module.reschedule_job(interval_seconds)
    else:
        ok = module.reschedule_job(interval_minutes)

    if not ok:
        return (
            jsonify({"success": False, "error": "Failed to reschedule the job"}),
            500,
        )

    # Persist for visibility across restarts (best-effort; the live
    # schedule change above already took effect regardless of this).
    try:
        firestore_service.create_or_update_settings(
            "scheduler", {"interval_minutes": interval_minutes}
        )
    except Exception:
        pass

    return jsonify(
        {
            "success": True,
            "message": f"Scheduler interval updated to {interval_minutes} minute(s) and applied immediately.",
        }
    )
