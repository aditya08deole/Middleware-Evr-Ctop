from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from .device_model import Device
from .log_model import Log
from .processed_data_model import ProcessedData

__all__ = ['db', 'Device', 'Log', 'ProcessedData']
