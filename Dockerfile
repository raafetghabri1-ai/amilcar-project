# ── AMILCAR Auto Care — Production Dockerfile (multi-stage) ──

# ── Stage 1: Build dependencies ──
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Production image ──
FROM python:3.12-slim

# System deps needed by weasyprint / xhtml2pdf / Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -r amilcar && useradd -r -g amilcar -d /app -s /sbin/nologin amilcar

WORKDIR /app

# Copy app code
COPY --chown=amilcar:amilcar . .

# Create data directory for SQLite + uploads persistence
RUN mkdir -p /data/uploads && chown -R amilcar:amilcar /data && ln -sf /data/uploads static/uploads

# Expose port
EXPOSE 8080

# Switch to non-root user
USER amilcar

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Start with gunicorn + gevent for SocketIO support
CMD ["gunicorn", "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "-w", "1", \
     "--bind", "0.0.0.0:8080", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "app:app"]
