from datetime import datetime
from models import db


class Device(db.Model):
    """Device model for storing IoT device configuration"""

    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    channel_id = db.Column(db.String(100), nullable=False)
    api_key = db.Column(db.String(100), nullable=False)
    ctop_url_1 = db.Column(db.String(255), nullable=False)
    ctop_url_2 = db.Column(db.String(255), nullable=True)
    auth_token = db.Column(db.String(255), nullable=False)
    device_type = db.Column(db.String(50), nullable=False, default="EvaraTank")
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    # EvaraTank specific fields
    tank_height = db.Column(db.Float, nullable=True)  # Tank height in cm
    distance_field = db.Column(
        db.String(10), nullable=True
    )  # ThingSpeak field number for distance
    temperature_field = db.Column(
        db.String(10), nullable=True
    )  # ThingSpeak field number for temperature
    filtering_method = db.Column(db.String(50), default="none")  # none, median, average
    filter_window = db.Column(db.Integer, default=5)  # Number of values for filtering

    # EvaraFlow specific fields
    meter_reading_field = db.Column(
        db.String(10), nullable=True
    )  # ThingSpeak field for meter reading
    flow_rate_field = db.Column(
        db.String(10), nullable=True
    )  # ThingSpeak field for flow rate

    # EvaraValve specific fields
    liters_field = db.Column(
        db.String(10), nullable=True
    )  # ThingSpeak field for liters

    # EvaraDeep specific fields (uses distance_field from EvaraTank)

    # EvaraTDS specific fields (uses temperature_field from EvaraTank)
    tds_field = db.Column(
        db.String(10), nullable=True
    )  # ThingSpeak field for TDS in ppm

    # Data source platform: 'thingspeak' or 'emqx'
    data_source = db.Column(db.String(20), default="thingspeak", nullable=False)

    # EMQX specific fields
    emqx_broker_url = db.Column(db.String(255), nullable=True)
    emqx_port = db.Column(db.Integer, default=1883)
    emqx_username = db.Column(db.String(100), nullable=True)
    emqx_password = db.Column(db.String(255), nullable=True)
    emqx_topic = db.Column(db.String(255), nullable=True)
    emqx_use_tls = db.Column(db.Boolean, default=False)
    emqx_qos = db.Column(db.Integer, default=1)
    emqx_ca_cert_path = db.Column(db.String(500), nullable=True)
    emqx_tls_insecure = db.Column(db.Boolean, default=False)

    # Status tracking
    is_active = db.Column(db.Boolean, default=True)
    last_sync_time = db.Column(db.DateTime, nullable=True)
    last_status = db.Column(db.String(20), default="pending")  # pending, success, error
    last_error = db.Column(db.Text, nullable=True)

    # CTOP delivery health tracking — see process_device() in utils/scheduler.py
    consecutive_failures = db.Column(db.Integer, default=0)
    needs_attention = db.Column(db.Boolean, default=False)
    last_ctop_attempt_time = db.Column(db.DateTime, nullable=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    logs = db.relationship(
        "Log", backref="device", lazy=True, cascade="all, delete-orphan"
    )
    processed_data = db.relationship(
        "ProcessedData", backref="device", lazy=True, cascade="all, delete-orphan"
    )

    def to_dict(self):
        """Convert device to dictionary for JSON serialization (safe for API responses)"""
        return {
            "id": self.id,
            "name": self.name,
            "device_type": self.device_type,
            "channel_id": self.channel_id,
            "data_source": self.data_source or "thingspeak",
            "emqx_broker_url": self.emqx_broker_url,
            "emqx_port": self.emqx_port,
            "emqx_username": self.emqx_username,
            "emqx_topic": self.emqx_topic,
            "emqx_use_tls": self.emqx_use_tls,
            "emqx_qos": self.emqx_qos,
            "emqx_ca_cert_path": self.emqx_ca_cert_path,
            "emqx_tls_insecure": self.emqx_tls_insecure,
            # SECURITY: api_key, auth_token, emqx_password intentionally excluded from public API responses
            "ctop_url_1": self.ctop_url_1,
            "ctop_url_2": self.ctop_url_2,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "tank_height": self.tank_height,
            "distance_field": self.distance_field,
            "temperature_field": self.temperature_field,
            "filtering_method": self.filtering_method,
            "filter_window": self.filter_window,
            "meter_reading_field": self.meter_reading_field,
            "flow_rate_field": self.flow_rate_field,
            "liters_field": self.liters_field,
            "tds_field": self.tds_field,
            "is_active": self.is_active,
            "last_sync_time": (
                self.last_sync_time.isoformat() if self.last_sync_time else None
            ),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "needs_attention": self.needs_attention,
            "last_ctop_attempt_time": (
                self.last_ctop_attempt_time.isoformat()
                if self.last_ctop_attempt_time
                else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_dict_with_credentials(self):
        """Convert device to dictionary including credentials (for internal use only)"""
        data = self.to_dict()
        data["api_key"] = self.api_key
        data["auth_token"] = self.auth_token
        data["emqx_password"] = self.emqx_password
        return data

    def __repr__(self):
        return f"<Device {self.name} (Platform: {self.data_source})>"
