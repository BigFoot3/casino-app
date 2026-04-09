import os
import logging
from datetime import timedelta

from flask import Flask
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

csrf    = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])


def create_app():
    app = Flask(__name__)

    # Load .env manually (no python-dotenv dependency)
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

    app.config['SECRET_KEY']                = os.environ.get('CASINO_SECRET_KEY', 'dev')
    is_prod = os.environ.get('FLASK_ENV', 'production') == 'production'
    app.config['SESSION_COOKIE_SECURE']     = is_prod  # False on HTTP, True on HTTPS/prod
    app.config['SESSION_COOKIE_HTTPONLY']   = True
    app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
    app.config['WTF_CSRF_TIME_LIMIT']       = None   # no expiry on CSRF token

    csrf.init_app(app)
    limiter.init_app(app)

    # Register blueprints
    from routes.auth   import auth_bp
    from routes.player import player_bp
    from routes.admin  import admin_bp
    from routes.api    import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    @app.route('/')
    def index():
        from flask import redirect, url_for, session as s
        if 'user_id' in s:
            return redirect(url_for('admin.index') if s.get('role') == 'admin' else url_for('player.dashboard'))
        return redirect(url_for('auth.login'))

    # CLI
    from cli import register_cli
    register_cli(app)

    # DB init + startup recovery
    with app.app_context():
        from db import init_db, startup_check
        init_db()
        startup_check()

    # Scheduler: one process only (gunicorn master with preload_app=True)
    if os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn'):
        from scheduler import start_scheduler
        start_scheduler(app)

    return app


# Re-apply CSRF on API: require X-CSRFToken header for all POST/PUT/DELETE
# (flask-wtf CSRFProtect honours X-CSRFToken by default when Content-Type is JSON)
