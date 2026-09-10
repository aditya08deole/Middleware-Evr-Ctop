from datetime import datetime
from models import db


class Log(db.Model):
    """Log model for tracking API responses and errors"""

    __tablename__ = "logs"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False)

    # Log details
    log_type = db.Column(
        db.String(20), nullable=False
    )  # thingspeak_fetch, preprocess, ctop_send
    status = db.Column(db.String(20), nullable=False)  # success, error, warning
    message = db.Column(db.Text, nullable=True)

    # Request/Response details
    endpoint = db.Column(db.String(255), nullable=True)
    request_payload = db.Column(db.Text, nullable=True)
    response_code = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text, nullable=True)

    # Error details
    error_type = db.Column(db.String(100), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    # Timestamp
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        """Convert log to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "device_id": self.device_id,
            "log_type": self.log_type,
            "status": self.status,
            "message": self.message,
            "endpoint": self.endpoint,
            "request_payload": self.request_payload,
            "response_code": self.response_code,
            "response_body": self.response_body,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Log {self.log_type} - {self.status}>"
