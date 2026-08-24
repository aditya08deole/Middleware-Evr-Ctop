import requests
import json
import time
from datetime import datetime
from models import db, Device, Log, ProcessedData
from config import Config
import os

class CTOPService:
    """Service for sending data to CTOP API endpoints"""
    
    def __init__(self):
        self.timeout = Config.CTOP_TIMEOUT
        self.max_retries = Config.CTOP_MAX_RETRIES
        self.retry_delay = Config.CTOP_RETRY_DELAY
        self.use_firebase = os.environ.get('USE_FIREBASE', 'false').lower() == 'true'
        
        if self.use_firebase:
            from utils.encryption import get_encryption_service
            self.encryption_service = get_encryption_service()
            
        # Phase 2 Optimization: Persistent HTTP Session for connection pooling
        self.session = requests.Session()
        # Increase pool size to handle parallel requests from scheduler
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20, 
            pool_maxsize=Config.SCHEDULER_MAX_WORKERS
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
    
    def send_to_ctop(self, device_id, device_data=None, payload=None):
        """
        Send data to CTOP endpoints for a specific device
        
        Args:
            device_id: ID of the device
            device_data: Optional device data dict (for Firestore)
            payload: Data payload to send
            
        Returns:
            dict: Results for each endpoint
        """
        # Get device data
        if device_data:
            device = device_data
            ctop_url_1 = device.get('ctop_url_1')
            ctop_url_2 = device.get('ctop_url_2')
            auth_token = device.get('auth_token')
        else:
            # SQLite fallback
            device = db.session.get(Device, device_id)
            if not device:
                return {'error': 'Device not found'}
            ctop_url_1 = device.ctop_url_1
            ctop_url_2 = device.ctop_url_2
            auth_token = device.auth_token
        
        # Decrypt auth token if using Firebase
        if self.use_firebase and auth_token:
            try:
                auth_token = self.encryption_service.decrypt(auth_token)
            except Exception as e:
                return {'error': f'Failed to decrypt auth token: {str(e)}'}
        
        results = {}
        
        if not ctop_url_1:
            return {'error': 'No CTOP URL configured'}
        
        # Ensure auth_token is a full Authorization header value.
        # If user stored only the raw token, prefix with 'Bearer '
        if auth_token and not str(auth_token).lower().startswith('bearer '):
            auth_token = f'Bearer {auth_token}'

        # Only include Authorization header if token is present
        headers = {
            'Content-Type': 'application/json'
        }
        if auth_token:
            headers['Authorization'] = auth_token
        
        # Extract device name and type for better logging
        device_name = None
        device_type = None
        if device_data:
            device_name = device_data.get('name')
            device_type = device_data.get('device_type')
        elif device:
            device_name = device.name
            device_type = device.device_type
            
        success, response_data, error = self._send_with_retry(
            ctop_url_1, payload, headers, device_id, device_name, device_type
        )
        
        results = {
            'url': ctop_url_1,
            'success': success,
            'response': response_data,
            'error': error
        }
        
        # Update device status locally (handled by the caller via local_device_store)
        # We removed the direct Firebase write here to save quota as per Issue #9 fix
        pass
        
        return results
    
    def _send_with_retry(self, url, payload, headers, device_id, device_name=None, device_type=None):
        """
        Send request with retry logic
        """
        last_error = None
        last_response_data = None
        
        for attempt in range(self.max_retries):
            try:
                # Use allow_redirects=True to handle 3xx
                response = self.session.post(
                    url,
                    data=json.dumps(payload),
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True
                )
                
                response_data = {
                    'status_code': response.status_code,
                    'body': response.text,
                    'history': [r.status_code for r in response.history]
                }
                last_response_data = response_data

                # Only 2xx is considered success
                is_success = 200 <= response.status_code < 300
                log_error = None if is_success else f"HTTP {response.status_code}: {response.text}"

                # Log the attempt (accurately, so a 4xx/5xx never reads as "Success")
                self._log_send(device_id, url, response.status_code, str(payload), response.text, log_error, attempt + 1, device_name=device_name, device_type=device_type)

                if is_success:
                    return True, response_data, None
                else:
                    last_error = f"HTTP {response.status_code}: {response.text}"
                    
            except requests.exceptions.Timeout:
                last_error = "Request timeout"
                self._log_send(device_id, url, None, str(payload), None, last_error, attempt + 1, "Timeout", device_name=device_name, device_type=device_type)
                
            except requests.exceptions.ConnectionError:
                last_error = "Connection error"
                self._log_send(device_id, url, None, str(payload), None, last_error, attempt + 1, "ConnectionError", device_name=device_name, device_type=device_type)
                
            except requests.exceptions.RequestException as e:
                last_error = f"Request error: {str(e)}"
                self._log_send(device_id, url, None, str(payload), None, last_error, attempt + 1, "RequestException", device_name=device_name, device_type=device_type)
            
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay)
        
        return False, last_response_data, last_error
    
    def _log_send(self, device_id, endpoint, response_code, request_payload, response_body, error_message, attempt, error_type=None, device_name=None, device_type=None):
        """Log CTOP send attempt locally"""
        import logging
        from utils.memory_logger import memory_logger
        logger = logging.getLogger(__name__)
        
        device_id_str = str(device_id)
        
        # Add to local memory logger for the web dashboard
        memory_logger.add_log(
            device_id=device_id_str,
            endpoint=endpoint,
            status_code=response_code,
            payload=request_payload,
            error_message=error_message,
            device_name=device_name,
            device_type=device_type
        )
        
        if error_message is None:
            logger.info(f"CTOP SEND [Device {device_id_str}]: Successfully sent payload to CTOP (Attempt {attempt}, Code {response_code})")
        else:
            logger.error(f"CTOP SEND ERROR [Device {device_id_str}]: {error_message} (Attempt {attempt}, Code {response_code}) - Payload: {request_payload}")
    
    def send_batch(self, device_id, payloads, device_data=None):
        """
        Send multiple payloads to CTOP endpoints
        
        Args:
            device_id: ID of the device
            payloads: List of payloads to send
            device_data: Optional device data dict (for Firestore mode)
            
        Returns:
            dict: Results for each payload
        """
        results = {}
        
        for i, payload in enumerate(payloads):
            result = self.send_to_ctop(device_id, device_data=device_data, payload=payload)
            results[f'payload_{i}'] = result
        
        return results
