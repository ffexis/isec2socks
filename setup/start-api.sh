#!/bin/bash
PID_FILE="/var/run/vpn-api.pid"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[API] API server already running (PID: $OLD_PID)"
        exit 0
    fi
fi

nohup python3 /usr/local/bin/vpn-api.py > /var/log/vpn-api.log 2>&1 &
echo $! > "$PID_FILE"
echo "[API] VPN API server started on port 31081 (PID: $!)"
