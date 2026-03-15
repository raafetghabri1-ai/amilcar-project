#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  AMILCAR Auto Care — Production Startup Script
# ═══════════════════════════════════════════════════════════
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════╗"
echo "║     🚗  AMILCAR Auto Care  🏍️              ║"
echo "║     Production Server                        ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# Activate virtual environment
source .venv/bin/activate

# Run backup before starting
echo -e "${YELLOW}📦 Running database backup...${NC}"
python backup.py

# Get local IP
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Server starting...${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo ""
echo -e "  💻 Computer:  ${CYAN}http://localhost:5000${NC}"
echo -e "  📱 Phone:     ${CYAN}http://${LOCAL_IP}:5000${NC}"
echo ""
echo -e "  ${YELLOW}📱 Pour installer sur téléphone:${NC}"
echo -e "     1. Ouvrir ${CYAN}http://${LOCAL_IP}:5000${NC} dans Chrome/Safari"
echo -e "     2. Menu → 'Ajouter à l'écran d'accueil'"
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo ""

# Start with Gunicorn (production)
exec gunicorn -c gunicorn_config.py app:app
