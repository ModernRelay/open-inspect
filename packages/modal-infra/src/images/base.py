"""
Base image definition for Open-Inspect sandboxes.

This image provides a complete development environment with:
- Debian slim base with git, curl, build-essential
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
CACHE_BUSTER = "v43-pre-pull-supabase-images"

# Supabase Docker images to pre-pull during image build.
# These are pulled as tarballs using crane (no Docker daemon needed at build time),
# then loaded into Docker at runtime via `docker load`.
# Versions must match the installed Supabase CLI version (from pkg/config/templates/Dockerfile).
_SUPABASE_IMAGES = [
    "supabase/postgres:15.8.1.085",
    "supabase/gotrue:v2.186.0",
    "postgrest/postgrest:v14.3",
    "supabase/realtime:v2.73.2",
    "supabase/storage-api:v1.35.3",
    "supabase/postgres-meta:v0.95.2",
    "supabase/studio:2026.01.27-sha-2a37755",
    "supabase/edge-runtime:v1.70.0",
    "supabase/logflare:1.30.5",
    "supabase/supavisor:2.7.4",
    "library/kong:2.8.1",
    "darthsim/imgproxy:v3.8.0",
    "timberio/vector:0.28.1-alpine",
    "axllent/mailpit:v1.22.3",
]

# Build crane pull commands for all Supabase images
_CRANE_PULL_COMMANDS = ["mkdir -p /var/lib/supabase-images"]
for _img in _SUPABASE_IMAGES:
    # Derive tarball filename from image name (e.g., "supabase/postgres:15.8.1.085" -> "postgres.tar")
    _tarball_name = _img.split("/")[-1].split(":")[0] + ".tar"
    _CRANE_PULL_COMMANDS.append(
        f"crane pull --platform=linux/amd64 {_img} /var/lib/supabase-images/{_tarball_name}"
    )

# Dockerd startup script for Docker-in-Docker support
# Sets up iptables NAT rules and starts dockerd with legacy iptables
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

# Use full paths for iptables-legacy (update-alternatives may not persist at runtime)
/usr/sbin/iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p tcp
/usr/sbin/iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p udp

# Use vfs storage driver to avoid overlay-on-overlay layer depth issues.
# The Modal sandbox itself uses overlayfs, and images like Supabase Postgres have 50+ layers,
# which exceeds the kernel's mount option page size limit when nested. vfs has no layer limit.
exec /usr/bin/dockerd --iptables=false --ip6tables=false --storage-driver=vfs -D
"""

_START_DOCKERD_B64 = base64.b64encode(_START_DOCKERD_SCRIPT.encode()).decode()

# Base image with all development tools
base_image = (
    modal.Image.debian_slim(python_version="3.12")
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
    # Install Docker CE for Docker-in-Docker support
    .run_commands(
        "install -m 0755 -d /etc/apt/keyrings",
        "curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc",
        "chmod a+r /etc/apt/keyrings/docker.asc",
        'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null',
        "apt-get update",
    )
    .apt_install(
        "docker-ce",
        "docker-ce-cli",
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
    # Set iptables to legacy mode (required for Docker-in-Docker on Modal)
    .run_commands(
        "update-alternatives --set iptables /usr/sbin/iptables-legacy",
        "update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy",
    )
    # Install Supabase CLI
    .run_commands(
        "curl -fsSL $(curl -s https://api.github.com/repos/supabase/cli/releases/latest | grep 'browser_download_url.*linux_amd64.deb' | cut -d '\"' -f 4) -o /tmp/supabase.deb",
        "dpkg -i /tmp/supabase.deb",
        "rm /tmp/supabase.deb",
        "supabase --version || echo 'Supabase CLI installed'",
    )
    # Install crane for pulling Docker images without a daemon (used at build time)
    .run_commands(
        "curl -fsSL https://github.com/google/go-containerregistry/releases/latest/download/go-containerregistry_Linux_x86_64.tar.gz | tar -xzf - -C /usr/local/bin crane",
        "crane version",
    )
    # Pre-pull Supabase Docker images as tarballs (no Docker daemon needed)
    # These are loaded into Docker at runtime via `docker load` in entrypoint.py
    .run_commands(*_CRANE_PULL_COMMANDS)
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
        'echo "export BUN_INSTALL=\"$HOME/.bun\"" >> /etc/profile.d/bun.sh',
        'echo "export PATH=\"$BUN_INSTALL/bin:$PATH\"" >> /etc/profile.d/bun.sh',
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
