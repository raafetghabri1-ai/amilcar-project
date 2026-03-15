"""Gunicorn Production Configuration for AMILCAR Auto Care"""
import multiprocessing
import os

# Server
bind = "0.0.0.0:5000"
workers = min(multiprocessing.cpu_count() * 2 + 1, 4)
worker_class = "sync"
timeout = 120
keepalive = 5

# Logging
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Security
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# Process
pidfile = "amilcar.pid"
daemon = False
