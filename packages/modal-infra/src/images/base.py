"""
Base image definition for Open-Inspect sandboxes.

This image provides a complete development environment with:
- Ubuntu 22.04 (Jammy) base with git, curl, build-essential
- Node.js 22 LTS, pnpm, Bun runtime
- Python 3.12 with uv
- OpenCode CLI pre-installed
- Playwright with headless Chrome for visual verification
- Docker CE for Docker-in-Docker support (Supabase, etc.)
- Supabase CLI
- Sandbox entrypoint and bridge code
"""

import base64
from pathlib import Path

import modal

# Get the path to the sandbox code
SANDBOX_DIR = Path(__file__).parent.parent / "sandbox"

# Plugin is now bundled with sandbox code at /app/sandbox/inspect-plugin.js

# OpenCode version to install
OPENCODE_VERSION = "latest"

# Cache buster - change this to force Modal image rebuild
# v39: Docker-in-Docker + Supabase CLI support
CACHE_BUSTER = "v50-iptables-legacy-path-fix"

# Dockerd startup script for Docker-in-Docker support
# Sets up iptables NAT rules and starts dockerd
# Uses Docker 5:27.5.0 on Ubuntu 22.04 which supports overlay2 natively on Modal.
_START_DOCKERD_SCRIPT = r"""#!/bin/bash
set -xe -o pipefail

dev=$(ip route show default | awk '/default/ {print $5}')
if [ -z "$dev" ]; then
    echo "Error: No default device found."
    ip route show
    exit 1
fi
echo "Default device: $dev"

addr=$(ip addr show dev "$dev" | grep -w inet | awk '{print $2}' | cut -d/ -f1)
if [ -z "$addr" ]; then
    echo "Error: No IP address found for device $dev."
    ip addr show dev "$dev"
    exit 1
fi
echo "IP address for $dev: $addr"

echo 1 > /proc/sys/net/ipv4/ip_forward

iptables_cmd=""
if [ -x /usr/sbin/iptables-legacy ]; then
    iptables_cmd="/usr/sbin/iptables-legacy"
elif command -v iptables-legacy >/dev/null 2>&1; then
    iptables_cmd="$(command -v iptables-legacy)"
elif command -v iptables >/dev/null 2>&1; then
    iptables_cmd="$(command -v iptables)"
fi

if [ -n "$iptables_cmd" ]; then
    "$iptables_cmd" -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p tcp || true
    "$iptables_cmd" -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p udp || true
else
    echo "Warning: no iptables binary found, skipping NAT setup"
fi

if [ -x /usr/sbin/iptables-legacy ]; then
    /usr/sbin/update-alternatives --set iptables /usr/sbin/iptables-legacy || true
fi
if [ -x /usr/sbin/ip6tables-legacy ]; then
    /usr/sbin/update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy || true
fi

exec /usr/bin/dockerd --iptables=false --ip6tables=false -D
"""

_START_DOCKERD_B64 = base64.b64encode(_START_DOCKERD_SCRIPT.encode()).decode()

# Base image with all development tools
# Ubuntu 22.04 + Docker CE 5:27.5.0 is the tested combination that supports
# overlay2 storage driver and bridge networking inside Modal sandboxes.
base_image = (
    modal.Image.from_registry("ubuntu:22.04", add_python="3.12")
    .env({"DEBIAN_FRONTEND": "noninteractive"})
    # System packages
    .apt_install(
        "git",
        "curl",
        "build-essential",
        "ca-certificates",
        "gnupg",
        "openssh-client",
        "jq",
        "unzip",  # Required for Bun installation
        "wget",  # For downloading binaries
        # Networking tools for Docker-in-Docker
        "iproute2",
        "iptables",
        "net-tools",
        # For Playwright
        "libnss3",
        "libnspr4",
        "libatk1.0-0",
        "libatk-bridge2.0-0",
        "libcups2",
        "libdrm2",
        "libxkbcommon0",
        "libxcomposite1",
        "libxdamage1",
        "libxfixes3",
        "libxrandr2",
        "libgbm1",
        "libasound2",
        "libpango-1.0-0",
        "libcairo2",
    )
    # Install Docker CE 5:27.5.0 for Docker-in-Docker support.
    # This specific version is pinned because it works with Modal's sandbox seccomp
    # profile: overlay2 storage driver and bridge networking both function correctly.
    .run_commands(
        "install -m 0755 -d /etc/apt/keyrings",
        "curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc",
        "chmod a+r /etc/apt/keyrings/docker.asc",
        'echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null',
        "apt-get update",
    )
    .apt_install(
        "docker-ce=5:27.5.0-1~ubuntu.22.04~jammy",
        "docker-ce-cli=5:27.5.0-1~ubuntu.22.04~jammy",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
    )
    # Upgrade runc for Modal compatibility
    .run_commands(
        "rm -f $(which runc)",
        "wget -q https://github.com/opencontainers/runc/releases/download/v1.3.0/runc.amd64",
        "chmod +x runc.amd64",
        "mv runc.amd64 /usr/local/bin/runc",
    )
    # Install Supabase CLI
    .run_commands(
        "curl -fsSL $(curl -s https://api.github.com/repos/supabase/cli/releases/latest | grep 'browser_download_url.*linux_amd64.deb' | cut -d '\"' -f 4) -o /tmp/supabase.deb",
        "dpkg -i /tmp/supabase.deb",
        "rm /tmp/supabase.deb",
        "supabase --version || echo 'Supabase CLI installed'",
    )
    # Bake dockerd startup script into image
    .run_commands(
        f"echo '{_START_DOCKERD_B64}' | base64 -d > /start-dockerd.sh",
        "chmod +x /start-dockerd.sh",
    )
    # Install Node.js 22 LTS
    .run_commands(
        # Add NodeSource repository for Node.js 22
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y nodejs",
        # Verify installation
        "node --version",
        "npm --version",
    )
    # Install pnpm and Bun
    .run_commands(
        # Install pnpm globally
        "npm install -g pnpm@latest",
        "pnpm --version",
        # Install Bun
        "curl -fsSL https://bun.sh/install | bash",
        # Add Bun to PATH for subsequent commands
        'echo "export BUN_INSTALL="$HOME/.bun"" >> /etc/profile.d/bun.sh',
        'echo "export PATH="$BUN_INSTALL/bin:$PATH"" >> /etc/profile.d/bun.sh',
    )
    # Install Python tools
    .pip_install(
        "uv",
        "httpx",
        "websockets",
        "playwright",
        "pydantic>=2.0",  # Required for sandbox types
        "PyJWT[crypto]",  # For GitHub App token generation (includes cryptography)
    )
    # Install OpenCode CLI and plugin for custom tools
    # CACHE_BUSTER is embedded in a no-op echo so Modal invalidates this layer on bump.
    .run_commands(
        f"echo 'cache: {CACHE_BUSTER}' > /dev/null",
        "npm install -g opencode-ai@latest",
        "opencode --version || echo 'OpenCode installed'",
        # Install @opencode-ai/plugin globally for custom tools
        # This ensures tools can import the plugin without needing to run bun add
        "npm install -g @opencode-ai/plugin@latest zod",
        # Install Anthropic OAuth plugin for OpenCode
        "npm install -g opencode-anthropic-auth@0.0.7",
    )
    # Install Playwright browsers (Chromium only to save space)
    .run_commands(
        "playwright install chromium",
        "playwright install-deps chromium",
    )
    # Create working directories
    .run_commands(
        "mkdir -p /workspace",
        "mkdir -p /app/plugins",
        "mkdir -p /tmp/opencode",
        "echo 'Image rebuilt at: v21-force-rebuild' > /app/image-version.txt",
    )
    # Set environment variables (including cache buster to force rebuild)
    .env(
        {
            "HOME": "/root",
            "NODE_ENV": "development",
            "PNPM_HOME": "/root/.local/share/pnpm",
            "PATH": "/root/.bun/bin:/root/.local/share/pnpm:/usr/local/bin:/usr/bin:/bin",
            "PLAYWRIGHT_BROWSERS_PATH": "/root/.cache/ms-playwright",
            "PYTHONPATH": "/app",
            "SANDBOX_VERSION": CACHE_BUSTER,
            # NODE_PATH for globally installed modules (used by custom tools)
            "NODE_PATH": "/usr/lib/node_modules",
        }
    )
    # Add sandbox code to the image (includes plugin at /app/sandbox/inspect-plugin.js)
    .add_local_dir(
        str(SANDBOX_DIR),
        remote_path="/app/sandbox",
    )
)

# Image variant optimized for Node.js/TypeScript projects
node_image = base_image.run_commands(
    # Pre-cache common Node.js development dependencies
    "npm cache clean --force",
)

# Image variant optimized for Python projects
python_image = base_image.run_commands(
    # Pre-create virtual environment
    "uv venv /workspace/.venv",
)
