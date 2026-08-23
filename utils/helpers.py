from datetime import datetime
import re

def format_timestamp(timestamp):
    """
    Format timestamp to human-readable string
    
    Args:
        timestamp: datetime object or ISO string
        
    Returns:
        str: Formatted timestamp string
    """
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            # Return original string if parsing fails
            return timestamp
    
    if timestamp is None:
        return 'N/A'
    
    return timestamp.strftime('%Y-%m-%d %H:%M:%S')

def validate_url(url):
    """
    Validate URL format
    
    Args:
        url: URL string to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not url:
        return False
    
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    
    return url_pattern.match(url) is not None

def sanitize_log_message(message):
    """
    Sanitize log message to prevent injection
    
    Args:
        message: Raw message string
        
    Returns:
        str: Sanitized message
    """
    if not message:
        return ''
    
    # Remove potential dangerous characters
    message = str(message)
    message = message.replace('\n', ' ').replace('\r', ' ')
    message = message[:1000]  # Limit length
    
    return message
