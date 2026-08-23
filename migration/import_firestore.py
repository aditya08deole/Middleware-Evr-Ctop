"""
Import data from JSON to Firestore
"""
import sys
import os
import json
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from firebase.firestore_service import FirestoreService

def import_data(json_file='migration_data.json'):
    """Import data from JSON to Firestore"""
    firestore_service = FirestoreService()
    
    if not firestore_service.is_initialized():
        print("ERROR: Firestore is not initialized. Please set FIREBASE_CREDENTIALS_PATH")
        return
    
    with open(json_file, 'r') as f:
        data = json.load(f)
    
    print(f"Importing data exported at: {data.get('export_timestamp')}")
    
    # Import devices
    print(f"\nImporting {len(data['devices'])} devices...")
    device_id_map = {}  # Map SQLite ID to Firestore ID
    for device in data['devices']:
        # Remove SQLite-specific fields
        device_copy = device.copy()
        device_copy.pop('id', None)
        
        # Convert datetime strings to proper format
        if device_copy.get('created_at'):
            device_copy['created_at'] = device_copy['created_at']
        if device_copy.get('updated_at'):
            device_copy['updated_at'] = device_copy['updated_at']
        if device_copy.get('last_sync_time'):
            device_copy['last_sync_time'] = device_copy['last_sync_time']
        
        # Add statistics fields
        device_copy['total_fetches'] = 0
        device_copy['successful_sends'] = 0
        device_copy['failed_sends'] = 0
        
        firestore_id = firestore_service.create_device(device_copy)
        device_id_map[device['id']] = firestore_id
        print(f"  Imported device: {device['name']} -> {firestore_id}")
    
    # Import logs
    print(f"\nImporting {len(data['logs'])} logs...")
    for log in data['logs']:
        log_copy = log.copy()
        log_copy.pop('id', None)
        
        # Map device ID
        old_device_id = log_copy.get('device_id')
        if old_device_id in device_id_map:
            log_copy['device_id'] = device_id_map[old_device_id]
        
        # Convert datetime
        if log_copy.get('created_at'):
            log_copy['created_at'] = log_copy['created_at']
        
        # Add date and hour for indexing
        log_copy['date'] = datetime.now().strftime('%Y-%m-%d')
        log_copy['hour'] = datetime.now().hour
        
        log_id = firestore_service.create_log(log_copy)
        print(f"  Imported log: {log_id}")
    
    # Import processed data
    print(f"\nImporting {len(data['processed_data'])} processed data entries...")
    for pd in data['processed_data']:
        pd_copy = pd.copy()
        pd_copy.pop('id', None)
        
        # Map device ID
        old_device_id = pd_copy.get('device_id')
        if old_device_id in device_id_map:
            pd_copy['device_id'] = device_id_map[old_device_id]
        
        # Convert datetime
        if pd_copy.get('created_at'):
            pd_copy['created_at'] = pd_copy['created_at']
        if pd_copy.get('updated_at'):
            pd_copy['updated_at'] = pd_copy['updated_at']
        if pd_copy.get('thingspeak_timestamp'):
            pd_copy['thingspeak_timestamp'] = pd_copy['thingspeak_timestamp']
        
        pd_id = firestore_service.create_processed_data(pd_copy)
        print(f"  Imported processed data: {pd_id}")
    
    print(f"\nImport completed successfully!")

if __name__ == '__main__':
    import_data()
