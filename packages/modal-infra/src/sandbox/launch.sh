#!/bin/bash
# Open-Inspect sandbox launch script.
# This is the Modal sandbox entrypoint.
#
# Starts dockerd in the background BEFORE exec'ing the Python supervisor,
# mirroring the working demo approach where networking is configured as the
# very first thing the sandbox does (with enable_docker: True privileges).

# Start dockerd via the canonical image-baked script.
# Redirecting to a file avoids Python subprocess pipe backpressure.
if [ -x /start-dockerd.sh ]; then
    echo "[launch] Starting dockerd via /start-dockerd.sh..."
    /start-dockerd.sh >/tmp/dockerd.log 2>&1 &
    echo "[launch] dockerd started (PID=$!) logs=/tmp/dockerd.log"
else
    echo "[launch] Warning: /start-dockerd.sh not found"
fi

# Exec the Python supervisor — replaces this script as the main process
echo "[launch] Starting Python supervisor..."
exec python -m sandbox.entrypoint
