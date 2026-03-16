#!/bin/bash
LOGFILE="/home/raaft/amilcar/logs/startup.log"
mkdir -p /home/raaft/amilcar/logs

if pgrep -f "python app.py" > /dev/null; then
    echo "[$(date)] AMILCAR already running"
else
    cd /home/raaft/amilcar && source .venv/bin/activate
    nohup python app.py >> "$LOGFILE" 2>&1 &
    echo "[$(date)] Flask started (PID: $!)" >> "$LOGFILE"
fi

sleep 4

if pgrep -f "ngrok http" > /dev/null; then
    echo "[$(date)] ngrok already running"
else
    nohup /home/raaft/.local/bin/ngrok http 5000 --log=stdout >> "$LOGFILE" 2>&1 &
    sleep 4
    URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for t in d['tunnels']:
        if t['proto']=='https': print(t['public_url']); break
except: pass
")
    if [ -n "$URL" ]; then
        echo "$URL" > /home/raaft/amilcar/logs/current_url.txt
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  🚗 AMILCAR — LIVE"
        echo "  🌐  $URL"
        echo "  📅  $URL/book"
        echo "  👤  admin / admin123"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    fi
fi
