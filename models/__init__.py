from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Must come after `db = SQLAlchemy()` above — each of these model modules
# does `from models import db` and uses `db.Column` at class-definition
# time, so `db` has to already exist. noqa: E402 is correct here, not
# reordering.
from .device_model import Device  # noqa: E402
from .log_model import Log  # noqa: E402
from .processed_data_model import ProcessedData  # noqa: E402

__all__ = ["db", "Device", "Log", "ProcessedData"]
