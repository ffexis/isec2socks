#!/bin/bash

echo "========================================="
echo "  isec2socks container starting..."
echo "========================================="

# ==================== VPN Client ====================
if [ ! -f /opt/iSecSP/vpn_cmdline ]; then
    VPN_DEB_URL="${VPN_DEB_URL:-https://its.pku.edu.cn/software/iSecSP_ubuntu_2.4.0.deb}"
    echo "[INIT] Downloading VPN client from $VPN_DEB_URL..."
    if wget -q --timeout=30 "$VPN_DEB_URL" -O /tmp/iSecSP.deb 2>/dev/null; then
        dpkg -i /tmp/iSecSP.deb
        rm -f /tmp/iSecSP.deb
        echo "[INIT] VPN client installed."
    else
        echo "[WARN] Failed to download VPN client. Will retry on next start."
    fi
fi

# ==================== Start VPN API ====================
if [ -f /usr/local/bin/vpn-api.py ]; then
    /usr/local/bin/start-api.sh
fi

# ==================== Keep Running ====================
exec tail -f /dev/null
