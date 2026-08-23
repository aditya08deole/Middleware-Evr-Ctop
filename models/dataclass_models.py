from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime

@dataclass
class DeviceModel:
    """Dataclass for Device validation"""
    name: str
    channel_id: str
    api_key: str
    ctop_url_1: str
    auth_token: str
    ctop_url_2: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool = True
    created_by: Optional[str] = None
    
    # Device-specific configuration fields
    device_type: str = 'EvaraTank'
    tank_height: Optional[float] = None
    distance_field: Optional[str] = None
    temperature_field: Optional[str] = None
    meter_reading_field: Optional[str] = None
    flow_rate_field: Optional[str] = None
    liters_field: Optional[str] = None
    tds_field: Optional[str] = None
    filtering_method: str = 'none'
    filter_window: int = 5
    
    # Data source platform: 'thingspeak' (default) or 'emqx'
    data_source: str = 'thingspeak'
    
    # EMQX-specific fields (used only when data_source == 'emqx')
    emqx_broker_url: Optional[str] = None
    emqx_port: Optional[int] = 1883
    emqx_username: Optional[str] = None
    emqx_password: Optional[str] = None
    emqx_topic: Optional[str] = None
    emqx_use_tls: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'name': self.name,
            'channel_id': self.channel_id,
            'api_key': self.api_key,
            'ctop_url_1': self.ctop_url_1,
            'ctop_url_2': self.ctop_url_2,
            'auth_token': self.auth_token,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'is_active': self.is_active,
            'created_by': self.created_by,
            'device_type': self.device_type,
            'tank_height': self.tank_height,
            'distance_field': self.distance_field,
            'temperature_field': self.temperature_field,
            'meter_reading_field': self.meter_reading_field,
            'flow_rate_field': self.flow_rate_field,
            'liters_field': self.liters_field,
            'tds_field': self.tds_field,
            'filtering_method': self.filtering_method,
            'filter_window': self.filter_window,
            'data_source': self.data_source,
            'emqx_broker_url': self.emqx_broker_url,
            'emqx_port': self.emqx_port,
            'emqx_username': self.emqx_username,
            'emqx_password': self.emqx_password,
            'emqx_topic': self.emqx_topic,
            'emqx_use_tls': self.emqx_use_tls,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DeviceModel':
        """Create from dictionary"""
        return cls(
            name=data.get('name', ''),
            channel_id=data.get('channel_id', ''),
            api_key=data.get('api_key', ''),
            ctop_url_1=data.get('ctop_url_1', ''),
            auth_token=data.get('auth_token', ''),
            ctop_url_2=data.get('ctop_url_2'),
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            is_active=data.get('is_active', True),
            created_by=data.get('created_by'),
            device_type=data.get('device_type', 'EvaraTank'),
            tank_height=data.get('tank_height'),
            distance_field=data.get('distance_field'),
            temperature_field=data.get('temperature_field'),
            meter_reading_field=data.get('meter_reading_field'),
            flow_rate_field=data.get('flow_rate_field'),
            liters_field=data.get('liters_field'),
            tds_field=data.get('tds_field'),
            filtering_method=data.get('filtering_method', 'none'),
            filter_window=data.get('filter_window', 5),
            data_source=data.get('data_source', 'thingspeak'),
            emqx_broker_url=data.get('emqx_broker_url'),
            emqx_port=data.get('emqx_port', 1883),
            emqx_username=data.get('emqx_username'),
            emqx_password=data.get('emqx_password'),
            emqx_topic=data.get('emqx_topic'),
            emqx_use_tls=data.get('emqx_use_tls', False),
        )

@dataclass
class LogModel:
    """Dataclass for Log validation"""
    device_id: str
    log_type: str
    status: str
    message: Optional[str] = None
    endpoint: Optional[str] = None
    request_payload: Optional[str] = None
    response_code: Optional[int] = None
    response_body: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    retry_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'device_id': self.device_id,
            'log_type': self.log_type,
            'status': self.status,
            'message': self.message,
            'endpoint': self.endpoint,
            'request_payload': self.request_payload,
            'response_code': self.response_code,
            'response_body': self.response_body,
            'error_type': self.error_type,
            'error_message': self.error_message,
            'duration_ms': self.duration_ms,
            'retry_count': self.retry_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LogModel':
        """Create from dictionary"""
        return cls(
            device_id=data.get('device_id', ''),
            log_type=data.get('log_type', ''),
            status=data.get('status', ''),
            message=data.get('message'),
            endpoint=data.get('endpoint'),
            request_payload=data.get('request_payload'),
            response_code=data.get('response_code'),
            response_body=data.get('response_body'),
            error_type=data.get('error_type'),
            error_message=data.get('error_message'),
            duration_ms=data.get('duration_ms'),
            retry_count=data.get('retry_count', 0)
        )

@dataclass
class ProcessedDataModel:
    """Dataclass for Processed Data validation"""
    device_id: str
    thingspeak_entry_id: Optional[str] = None
    thingspeak_timestamp: Optional[datetime] = None
    raw_data: Optional[str] = None
    processed_data: Optional[str] = None
    transformed_data: Optional[str] = None
    processing_status: str = 'pending'
    ctop_sent: bool = False
    ctop_response: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'device_id': self.device_id,
            'thingspeak_entry_id': self.thingspeak_entry_id,
            'thingspeak_timestamp': self.thingspeak_timestamp.isoformat() if self.thingspeak_timestamp else None,
            'raw_data': self.raw_data,
            'processed_data': self.processed_data,
            'transformed_data': self.transformed_data,
            'processing_status': self.processing_status,
            'ctop_sent': self.ctop_sent,
            'ctop_response': self.ctop_response
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProcessedDataModel':
        """Create from dictionary"""
        ts = data.get('thingspeak_timestamp')
        if ts and isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        
        return cls(
            device_id=data.get('device_id', ''),
            thingspeak_entry_id=data.get('thingspeak_entry_id'),
            thingspeak_timestamp=ts,
            raw_data=data.get('raw_data'),
            processed_data=data.get('processed_data'),
            transformed_data=data.get('transformed_data'),
            processing_status=data.get('processing_status', 'pending'),
            ctop_sent=data.get('ctop_sent', False),
            ctop_response=data.get('ctop_response')
        )

@dataclass
class UserModel:
    """Dataclass for User validation"""
    id: str
    email: str
    display_name: Optional[str] = None
    photo_url: Optional[str] = None
    role: str = 'viewer'
    permissions: List[str] = field(default_factory=list)
    theme: str = 'light'
    notifications: Optional[Dict[str, bool]] = None
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'email': self.email,
            'display_name': self.display_name,
            'photo_url': self.photo_url,
            'role': self.role,
            'permissions': self.permissions,
            'theme': self.theme,
            'notifications': self.notifications or {},
            'is_active': self.is_active
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserModel':
        """Create from dictionary"""
        return cls(
            id=data.get('id', ''),
            email=data.get('email', ''),
            display_name=data.get('display_name'),
            photo_url=data.get('photo_url'),
            role=data.get('role', 'viewer'),
            permissions=data.get('permissions', []),
            theme=data.get('theme', 'light'),
            notifications=data.get('notifications'),
            is_active=data.get('is_active', True)
        )
    
    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission"""
        return permission in self.permissions or self.role == 'admin'
