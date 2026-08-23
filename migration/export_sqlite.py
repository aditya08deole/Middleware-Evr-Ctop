"""
Export SQLite data to JSON for migration to Firestore
"""
import sys
import os
import json
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import db, Device, Log, ProcessedData
from app import create_app

def export_data():
    """Export all data from SQLite to JSON"""
    app = create_app('development')
    
    with app.app_context():
        export = {
            'export_timestamp': datetime.utcnow().isoformat(),
            'devices': [],
            'logs': [],
            'processed_data': []
        }
        
        # Export devices
        devices = Device.query.all()
        print(f"Exporting {len(devices)} devices...")
        for device in devices:
            device_data = device.to_dict()
            # Convert datetime to ISO string
            if device_data.get('created_at'):
                device_data['created_at'] = device_data['created_at']
            if device_data.get('updated_at'):
                device_data['updated_at'] = device_data['updated_at']
            if device_data.get('last_sync_time'):
                device_data['last_sync_time'] = device_data['last_sync_time']
            export['devices'].append(device_data)
        
        # Export logs
        logs = Log.query.all()
        print(f"Exporting {len(logs)} logs...")
        for log in logs:
            log_data = log.to_dict()
            if log_data.get('created_at'):
                log_data['created_at'] = log_data['created_at']
            export['logs'].append(log_data)
        
        # Export processed data
        processed_data = ProcessedData.query.all()
        print(f"Exporting {len(processed_data)} processed data entries...")
        for pd in processed_data:
            pd_data = pd.to_dict()
            if pd_data.get('created_at'):
                pd_data['created_at'] = pd_data['created_at']
            if pd_data.get('updated_at'):
                pd_data['updated_at'] = pd_data['updated_at']
            if pd_data.get('thingspeak_timestamp'):
                pd_data['thingspeak_timestamp'] = pd_data['thingspeak_timestamp']
            export['processed_data'].append(pd_data)
        
        # Save to JSON file
        output_file = 'migration_data.json'
        with open(output_file, 'w') as f:
            json.dump(export, f, indent=2, default=str)
        
        print(f"\nExport completed successfully!")
        print(f"Devices: {len(export['devices'])}")
        print(f"Logs: {len(export['logs'])}")
        print(f"Processed Data: {len(export['processed_data'])}")
        print(f"Output file: {output_file}")
        
        return export

if __name__ == '__main__':
    export_data()
