import os
import re
import uuid
from flask import Blueprint, request, jsonify
from firebase.firestore_service import FirestoreService
from middleware import handle_errors
from config import Config
from services.ctop_service import set_ctop_hosts
from services.preprocess_service import (
    DEFAULT_CTOP_FIELD_MAP,
    get_ctop_field_map,
    set_ctop_field_map_overrides,
)

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")
firestore_service = FirestoreService()

# Seeded the first time /settings/ctop-hosts is read and no registry exists
# yet — keeps the production host that every existing device already
# points at (via ctop_url_1) registered and visible, even though those
# legacy URL fields don't themselves reference it by host_id.
_DEFAULT_CTOP_HOSTS = [
    {"id": "production", "name": "Production", "base_url": "https://ctop.iiit.ac.in"}
]


def _slugify_host_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


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
                    "interval_minutes": round(
                        Config.SCHEDULER_INTERVAL_SECONDS / 60, 2
                    ),
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


@settings_bp.route("/ctop-hosts", methods=["GET"])
@handle_errors
def get_ctop_hosts_route():
    """List registered CTOP host presets (name + base URL) that a device's
    ctop_targets reference by id instead of hardcoding a full URL. Seeds
    the current production host on first call if no registry exists yet,
    so it's there in the UI from the start rather than silently missing."""
    doc = firestore_service.get_named_settings("ctop_hosts")
    hosts = doc.get("hosts")
    if hosts is None:
        hosts = _DEFAULT_CTOP_HOSTS
        firestore_service.create_or_update_settings("ctop_hosts", {"hosts": hosts})
    set_ctop_hosts(
        hosts
    )  # keep the live send-time cache in sync with what we just read
    return jsonify({"success": True, "data": {"hosts": hosts}})


@settings_bp.route("/ctop-hosts", methods=["POST"])
@handle_errors
def add_ctop_host():
    """Register a new CTOP host (e.g. a dev/staging server) that devices
    can target via ctop_targets without hardcoding its URL."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    base_url = (data.get("base_url") or "").strip()

    if not name or not base_url:
        return (
            jsonify({"success": False, "error": "name and base_url are required"}),
            400,
        )
    if not re.match(r"^https?://", base_url, re.IGNORECASE):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "base_url must start with http:// or https://",
                }
            ),
            400,
        )

    doc = firestore_service.get_named_settings("ctop_hosts")
    hosts = doc.get("hosts")
    if hosts is None:
        hosts = _DEFAULT_CTOP_HOSTS

    host_id = _slugify_host_id(name)
    existing_ids = {h["id"] for h in hosts}
    if host_id in existing_ids:
        host_id = f"{host_id}-{uuid.uuid4().hex[:4]}"

    new_host = {"id": host_id, "name": name, "base_url": base_url.rstrip("/")}
    hosts = hosts + [new_host]

    firestore_service.create_or_update_settings("ctop_hosts", {"hosts": hosts})
    set_ctop_hosts(hosts)

    return jsonify({"success": True, "data": {"hosts": hosts}}), 201


@settings_bp.route("/ctop-hosts/<host_id>", methods=["DELETE"])
@handle_errors
def delete_ctop_host(host_id):
    """Remove a registered CTOP host. Any device whose ctop_targets still
    reference this host_id will simply have that one target skipped at
    send time (logged as a warning), not fail outright — see
    services/ctop_service.py send_to_ctop()."""
    doc = firestore_service.get_named_settings("ctop_hosts")
    hosts = doc.get("hosts")
    if hosts is None:
        hosts = _DEFAULT_CTOP_HOSTS
    remaining = [h for h in hosts if h["id"] != host_id]

    if len(remaining) == len(hosts):
        return jsonify({"success": False, "error": "Host not found"}), 404

    firestore_service.create_or_update_settings("ctop_hosts", {"hosts": remaining})
    set_ctop_hosts(remaining)

    return jsonify({"success": True, "data": {"hosts": remaining}})


@settings_bp.route("/ctop-field-mapping", methods=["GET"])
@handle_errors
def get_ctop_field_mapping_route():
    """Current effective CTOP output-key mapping per device type —
    defaults merged with any saved override, so the UI always shows the
    real current values, not just whatever was explicitly overridden."""
    return jsonify({"success": True, "data": {"mapping": get_ctop_field_map()}})


@settings_bp.route("/ctop-field-mapping", methods=["PUT"])
@handle_errors
def update_ctop_field_mapping():
    """Save CTOP output-key overrides and apply them immediately (the
    live scheduler calls get_ctop_field_map() on every send, so this takes
    effect on the very next tick, no restart needed). Only device
    types/fields already present in DEFAULT_CTOP_FIELD_MAP are accepted —
    this renames an existing outbound key, it doesn't invent new internal
    fields that preprocess_service doesn't actually produce."""
    data = request.get_json() or {}
    overrides = data.get("mapping")

    if not isinstance(overrides, dict):
        return jsonify({"success": False, "error": "mapping must be an object"}), 400

    cleaned = {}
    for device_type, fields in overrides.items():
        if device_type not in DEFAULT_CTOP_FIELD_MAP:
            return (
                jsonify(
                    {"success": False, "error": f"Unknown device type: {device_type}"}
                ),
                400,
            )
        if not isinstance(fields, dict):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Mapping for {device_type} must be an object",
                    }
                ),
                400,
            )
        cleaned_fields = {}
        for internal_field, ctop_key in fields.items():
            if internal_field not in DEFAULT_CTOP_FIELD_MAP[device_type]:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"Unknown field '{internal_field}' for {device_type}",
                        }
                    ),
                    400,
                )
            ctop_key = str(ctop_key or "").strip()
            if not ctop_key:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"CTOP key for {device_type}.{internal_field} cannot be empty",
                        }
                    ),
                    400,
                )
            cleaned_fields[internal_field] = ctop_key
        cleaned[device_type] = cleaned_fields

    firestore_service.create_or_update_settings(
        "ctop_field_mapping", {"overrides": cleaned}
    )
    set_ctop_field_map_overrides(cleaned)

    return jsonify({"success": True, "data": {"mapping": get_ctop_field_map()}})
