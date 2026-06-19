FROM debian:12-slim

# USTC apt mirror (HTTP, no ca-certificates needed)
RUN echo "deb http://mirrors.ustc.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb http://mirrors.ustc.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.ustc.edu.cn/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# System dependencies + Python packages
RUN apt-get update -qq && \
    apt-get install -y -qq python3 python3-pip jq procps iproute2 libxml2 libgtk2.0-0 libxss1 wget && \
    pip3 install --break-system-packages bottle cheroot && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Gost proxy (GitHub multi-source fallback)
RUN GOST_VERSION="2.11.5" && \
    GOST_FILE="gost-linux-amd64-${GOST_VERSION}.gz" && \
    (wget -q --timeout=30 "https://gh-proxy.com/https://github.com/ginuerzh/gost/releases/download/v${GOST_VERSION}/${GOST_FILE}" -O /tmp/gost.gz || \
     wget -q --timeout=30 "https://ghfast.top/https://github.com/ginuerzh/gost/releases/download/v${GOST_VERSION}/${GOST_FILE}" -O /tmp/gost.gz || \
     wget -q --timeout=30 "https://github.com/ginuerzh/gost/releases/download/v${GOST_VERSION}/${GOST_FILE}" -O /tmp/gost.gz) && \
    gzip -d /tmp/gost.gz && \
    mv /tmp/gost /usr/local/bin/gost && \
    chmod +x /usr/local/bin/gost && \
    rm -f /tmp/gost*

# Application files
COPY setup/vpn /usr/local/bin/vpn
COPY setup/vpn-api.py /usr/local/bin/vpn-api.py
COPY setup/start-api.sh /usr/local/bin/start-api.sh
COPY setup/vpn-conf.json /etc/vpn-conf.json
COPY setup/index.html /etc/isec2socks/index.html

RUN chmod +x /usr/local/bin/vpn /usr/local/bin/start-api.sh /usr/local/bin/vpn-api.py
