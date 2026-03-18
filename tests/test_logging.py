"""Tests for helpers_logging.py — Centralized Logging."""
import os
import logging
import pytest
from helpers_logging import setup_logging, get_logger, LOG_DIR


def test_log_dir_created():
    """Log directory should exist after import."""
    assert os.path.isdir(LOG_DIR)


def test_setup_logging_returns_logger():
    logger = setup_logging()
    assert logger is not None
    assert logger.name == 'amilcar'


def test_get_logger_namespaced():
    logger = get_logger('test_module')
    assert logger.name == 'amilcar.test_module'


def test_logging_writes_to_file():
    """Ensure log messages are written to app.log."""
    logger = get_logger('test_write')
    logger.info('Test log entry for verification')
    log_path = os.path.join(LOG_DIR, 'app.log')
    # Flush handlers
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert os.path.exists(log_path)
    with open(log_path, 'r') as f:
        content = f.read()
    assert 'Test log entry for verification' in content


def test_error_log_file():
    """Error messages should appear in errors.log."""
    logger = get_logger('test_error')
    logger.error('Critical test error 12345')
    log_path = os.path.join(LOG_DIR, 'errors.log')
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert os.path.exists(log_path)
    with open(log_path, 'r') as f:
        content = f.read()
    assert 'Critical test error 12345' in content


def test_security_logger():
    """Security logger should have its own handlers."""
    sec = logging.getLogger('amilcar.security')
    assert sec is not None
    sec.info('Security test event 67890')
    log_path = os.path.join(LOG_DIR, 'security.log')
    for handler in sec.handlers:
        handler.flush()
    assert os.path.exists(log_path)
