"""
AMILCAR — Centralized Logging Configuration
=============================================
Structured file + console logging for all modules.
"""
import logging
import logging.handlers
import os

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)


def setup_logging(app=None):
    """Configure application-wide logging. Call once at startup."""

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s — %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # ── Main app log (rotating, 5MB x 5 files) ──
    app_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, 'app.log'),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    app_handler.setFormatter(formatter)
    app_handler.setLevel(logging.INFO)

    # ── Error-only log ──
    error_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, 'errors.log'),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    # ── Security / audit log ──
    security_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, 'security.log'),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    security_handler.setFormatter(formatter)
    security_handler.setLevel(logging.INFO)

    # ── Console (stderr) ──
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(app_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)

    # Security logger gets its own file
    sec_logger = logging.getLogger('amilcar.security')
    sec_logger.addHandler(security_handler)

    # Reduce noise from third-party libs
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)

    # Attach to Flask app if provided
    if app:
        app.logger.addHandler(app_handler)
        app.logger.addHandler(error_handler)
        app.logger.setLevel(logging.INFO)

    return logging.getLogger('amilcar')


def get_logger(name):
    """Get a named child logger under the amilcar namespace."""
    return logging.getLogger(f'amilcar.{name}')
