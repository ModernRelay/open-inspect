#!/bin/bash
# Open-Inspect sandbox launch script.
# This is the Modal sandbox entrypoint.
#
# Starts dockerd in the background BEFORE exec'ing the Python supervisor,
# mirroring the working demo approach where networking is configured as the
# very first thing the sandbox does (with enable_docker: True privileges).

# Setup Docker networking and start dockerd if the image has it
if [ -f /start-dockerd.sh ]; then
    echo "[launch] Configuring Docker networking..."

    dev=$(ip route show default 2>/dev/null | awk '/default/ {print $5}' | head -1)
    if [ -n "$dev" ]; then
        addr=$(ip addr show dev "$dev" 2>/dev/null | grep -w inet | awk '{print $2}' | cut -d/ -f1 | head -1)
        if [ -n "$addr" ]; then
            echo 1 > /proc/sys/net/ipv4/ip_forward || true
            iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p tcp 2>/dev/null || true
            iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p udp 2>/dev/null || true
            update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true
            update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true
            echo "[launch] Networking configured: dev=$dev addr=$addr"
        else
            echo "[launch] Warning: no IP found for $dev, skipping networking setup"
        fi
    else
        echo "[launch] Warning: no default route, skipping networking setup"
    fi

    echo "[launch] Starting dockerd in background..."
    # Run dockerd directly (not via exec) so it stays in background
    /usr/bin/dockerd --iptables=false --ip6tables=false &
    echo "[launch] dockerd started (PID=$!)"
fi

# Exec the Python supervisor — replaces this script as the main process
echo "[launch] Starting Python supervisor..."
exec python -m sandbox.entrypoint
