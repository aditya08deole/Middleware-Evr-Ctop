from datetime import datetime, timezone
import os
from dotenv import load_dotenv

# Load environment variables FIRST before any other imports
load_dotenv()

from flask import Flask, render_template, redirect, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from models import db
from routes import device_bp, data_bp, auth_bp, analytics_bp, settings_bp
from utils import init_scheduler
from config import config

def create_app(config_name='default'):
    """Application factory"""
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(config[config_name])
    
    # Initialize database
    db.init_app(app)
    
    # Database creation is now handled by entrypoint.sh in production
    # to avoid race conditions between Gunicorn workers.
    if config_name == 'development':
        with app.app_context():
            db.create_all()
    
    # Initialize rate limiter
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["20000 per hour", "1000 per minute"],
        storage_uri=os.environ.get('RATELIMIT_STORAGE_URL', 'memory://')
    )
    
    # Configure CORS
    # SECURITY: Update allowed origins for production
    # Default allows localhost for development
    allowed_origins = os.environ.get('ALLOWED_ORIGINS', 'http://localhost:3000,http://localhost:8080').split(',')
    
    CORS(app, resources={
        r"/api/*": {
            "origins": allowed_origins,
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        },
        r"/auth/*": {
            "origins": allowed_origins,
            "methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    })
    
    # Register blueprints
    app.register_blueprint(device_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(settings_bp)
    
    # Initialize the scheduler for this process. This app runs a live
    # BackgroundScheduler and persistent EMQX MQTT clients, so app.run() below
    # is called with use_reloader=False — there is only ever one process, so
    # no reloader-related dedup guard is needed here.
    #
    # NOTE: in production this module is imported once per Gunicorn worker.
    # If multiple workers are configured, each one starts its own scheduler
    # against the same local SQLite mirror — that's a pre-existing condition,
    # not addressed here.
    init_scheduler(app)

    # 2. PERFORM STARTUP SYNC: Pull master data from Firebase into Local Mirror
    from utils.local_device_store import local_device_store
    logger = app.logger
    logger.info("Performing startup sync from Firebase...")
    local_device_store.sync_from_firebase()

    # 3. Ensure device cache is warmed up
    from utils.device_cache import device_cache
    device_cache.get_devices()
    
    # Routes
    @app.route('/')
    def index():
        """Render dashboard as landing page"""
        return render_template('dashboard.html')
    
    @app.route('/dashboard')
    def dashboard():
        """Render dashboard page"""
        return render_template('dashboard.html')
    
    @app.route('/logs')
    def logs():
        """Render logs page"""
        return render_template('logs.html')
    
    @app.route('/add-device')
    def add_device():
        """Render add device page"""
        return render_template('add_device.html')
    
    @app.route('/health')
    def health():
        """Health check for Docker/Monitoring"""
        return jsonify({
            "status": "healthy",
            "environment": os.environ.get('FLASK_ENV', 'development'),
            "scheduler": "running"
        }), 200

    @app.route('/add-device-multi')
    def add_device_multi():
        """Render multi-step add device page"""
        return render_template('add_device_multi.html')
    
    @app.route('/devices/<device_id>')
    def device_detail(device_id):
        """Render device detail page"""
        # Try to get device from Local Mirror Cache (0 Firebase Reads)
        from utils.local_device_store import local_device_store
        device = local_device_store.get_device_by_id(device_id)
        
        if device:
            # For logs, we still fetch from Firebase as logs are not mirrored locally
            # (they are write-only / fetch-on-demand)
            from firebase.firestore_service import FirestoreService
            fs = FirestoreService()
            logs = fs.get_logs(device_id=device_id, limit=10)
            return render_template('device_detail.html', device=device, logs=logs)
        
        # Fallback to SQLite
        from models import Device, Log
        device = db.session.get(Device, device_id)
        logs = Log.query.filter_by(device_id=device_id).order_by(Log.created_at.desc()).limit(10).all()
        return render_template('device_detail.html', device=device, logs=logs)
    
    
    @app.route('/api/local-logs')
    def local_logs():
        """Get local in-memory logs for the dashboard with real-time stats"""
        from utils.memory_logger import memory_logger
        from utils.device_cache import device_cache
        from flask import request, jsonify
        
        device_id = request.args.get('device_id')
        limit = request.args.get('limit', 20, type=int)
        
        # Optionally force refresh cache if browser is refreshed (first load)
        if request.args.get('refresh_cache') == 'true':
            device_cache.refresh()
            
        logs = memory_logger.get_logs(limit=limit, device_id=device_id)
        # Get real-time stats from the logger
        log_stats = memory_logger.get_stats()

        # Get device stats from cache. active_only=False so a device the
        # user disabled via the dashboard's Toggle button still shows up
        # (as disabled) instead of silently disappearing — with the default
        # active_only=True this list was already pre-filtered to is_active,
        # which made "active_devices" always equal "total_devices" below by
        # construction, and made the disabled-device count unrecoverable.
        all_devices = device_cache.get_devices(active_only=False)

        return jsonify({
            'success': True,
            'data': logs,
            'stats': {
                'total_logs': log_stats['total_logs'],
                'error_logs': log_stats['error_logs'],
                'total_devices': len(all_devices),
                'active_devices': sum(1 for d in all_devices if d.get('is_active', True)),
                'active_devices_list': all_devices
            }
        })

    @app.route('/api/refresh-cache', methods=['POST'])
    def refresh_cache():
        """Force a manual sync between Local Mirror and Firebase Master"""
        from utils.local_device_store import local_device_store
        success = local_device_store.sync_from_firebase()
        if success:
            return jsonify({'success': True, 'message': 'Local mirror synchronized with Firebase Master'})
        else:
            return jsonify({'success': False, 'message': 'Sync failed. Check logs for details.'}), 500

    @app.route('/settings')
    def settings():
        """Render settings page"""
        return render_template('settings.html')
    
    
    @app.route('/api/test-connection', methods=['POST'])
    def test_connection():
        """Test CTOP connection endpoint (used by frontend)"""
        from flask import request
        data = request.get_json()
        
        url = data.get('url')
        auth_token = data.get('auth_token')
        
        if not url or not auth_token:
            return jsonify({
                'success': False,
                'error': 'URL and auth_token are required'
            }), 400
        
        try:
            # Simple HEAD request to test connectivity
            import requests
            headers = {'Authorization': f'Bearer {auth_token}'}
            response = requests.head(url, headers=headers, timeout=10)
            
            if response.status_code in [200, 201, 204, 405]:
                return jsonify({
                    'success': True,
                    'message': 'Connection successful',
                    'status_code': response.status_code
                })
            else:
                return jsonify({
                    'success': False,
                    'error': f'Server returned error: {response.status_code}',
                    'status_code': response.status_code
                }), 400
                
        except requests.exceptions.Timeout:
            return jsonify({
                'success': False,
                'error': 'Connection timed out'
            }), 400
        except requests.exceptions.ConnectionError:
            return jsonify({
                'success': False,
                'error': 'Could not connect to server'
            }), 400
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Connection test failed: {str(e)}'
            }), 400
    
    @app.route('/api/diagnostics')
    def get_diagnostics():
        """Returns system performance telemetry."""
        try:
            from utils.local_device_store import local_device_store
            from utils.memory_logger import memory_logger
            
            telemetry = local_device_store.get_telemetry()
            
            # Calculate reduction ratio
            updates = telemetry.get('in_memory_updates', 0)
            flushes = telemetry.get('disk_flushes', 0)
            reduction = 0
            if updates > 0:
                reduction = (1 - (flushes / updates)) * 100
                
            return jsonify({
                "success": True,
                "telemetry": telemetry,
                "io_reduction_percent": round(reduction, 2),
                "memory_logs_count": len(memory_logger.get_logs()),
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    
    return app


# Create the application instance for production servers (like Gunicorn)
config_name = os.environ.get('FLASK_ENV', 'production')
app = create_app(config_name)

if __name__ == '__main__':
    # SECURITY: Disable debug mode in production
    is_production = config_name == 'production'
    debug_mode = not is_production  # Debug enabled only in development
    
    print("=" * 60)
    print("CTOP IoT Data Pipeline System")
    print("=" * 60)
    print(f"Environment: {config_name}")
    print(f"Debug Mode: {debug_mode}")
    print(f"Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print(f"Scheduler Interval: {app.config['SCHEDULER_INTERVAL_SECONDS']} seconds")
    print("=" * 60)
    print("Starting server on http://127.0.0.1:8080")
    print("=" * 60)
    
    # Use 127.0.0.1 for development, 0.0.0.0 for production
    host = '0.0.0.0' if is_production else '127.0.0.1'
    # use_reloader=False: this process owns a live BackgroundScheduler and
    # persistent EMQX MQTT clients. Werkzeug's auto-reloader spawning extra
    # processes means multiple schedulers race on the same SQLite mirror and
    # the same MQTT client_id at once. debug=True still gives the interactive
    # debugger; it just won't auto-restart the process anymore.
    app.run(host=host, port=8080, debug=debug_mode, use_reloader=False)
