from datetime import datetime
from models import db

class ProcessedData(db.Model):
    """Model for storing processed/transformed data (optional storage)"""
    __tablename__ = 'processed_data'
    
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=False)
    
    # Original ThingSpeak data
    thingspeak_entry_id = db.Column(db.String(50), nullable=True)
    thingspeak_timestamp = db.Column(db.DateTime, nullable=True)
    
    # Processed data (stored as JSON string)
    raw_data = db.Column(db.Text, nullable=True)  # Original ThingSpeak feed
    processed_data = db.Column(db.Text, nullable=True)  # After preprocessing
    transformed_data = db.Column(db.Text, nullable=True)  # Final CTOP format
    
    # Status
    processing_status = db.Column(db.String(20), default='pending')  # pending, processed, sent, failed
    ctop_sent = db.Column(db.Boolean, default=False)
    ctop_response = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        """Convert processed data to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'device_id': self.device_id,
            'thingspeak_entry_id': self.thingspeak_entry_id,
            'thingspeak_timestamp': self.thingspeak_timestamp.isoformat() if self.thingspeak_timestamp else None,
            'raw_data': self.raw_data,
            'processed_data': self.processed_data,
            'transformed_data': self.transformed_data,
            'processing_status': self.processing_status,
            'ctop_sent': self.ctop_sent,
            'ctop_response': self.ctop_response,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def __repr__(self):
        return f'<ProcessedData {self.id} - Device {self.device_id}>'
