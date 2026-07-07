#!/bin/sh
set -e

# Capture runtime UID/GID from environment variables, defaulting to 1000
PUID=${USER_UID:-1000}
PGID=${USER_GID:-1000}

# Without root we can neither remap the node user (usermod/groupmod/chown)
# nor switch users (gosu needs CAP_SETUID/CAP_SETGID), so exec directly.
# This covers Kubernetes restricted PodSecurity (runAsNonRoot + runAsUser)
# as well as platforms that assign arbitrary UIDs (e.g. OpenShift); for the
# latter a UID/GID mismatch is unfixable here, so warn instead of letting
# usermod fail cryptically and keep volume-permission issues diagnosable.
if [ "$(id -u)" -ne 0 ]; then
    if [ "$(id -u)" -ne "$PUID" ] || [ "$(id -g)" -ne "$PGID" ]; then
        echo "docker-entrypoint.sh: running unprivileged as $(id -u):$(id -g); cannot remap to requested ${PUID}:${PGID}" >&2
    fi
    exec "$@"
fi

# Adjust the node user's UID/GID if they differ from the runtime request
# and fix volume ownership only when a remap is needed
changed=0

if [ "$(id -u node)" -ne "$PUID" ]; then
    echo "Updating node UID to $PUID"
    usermod -o -u "$PUID" node
    changed=1
fi

if [ "$(id -g node)" -ne "$PGID" ]; then
    echo "Updating node GID to $PGID"
    groupmod -o -g "$PGID" node
    usermod -g "$PGID" node
    changed=1
fi

# Always ensure /paperclip is writable by the node user
# (Railway volumes mount as root-owned on first attach)
chown -R node:node /paperclip

# Auto-onboard if no config exists (non-interactive Docker deployment)
CONFIG_PATH="${PAPERCLIP_CONFIG:-/paperclip/instances/default/config.json}"
if [ ! -f "$CONFIG_PATH" ]; then
    echo "No config found, running onboard..."
    gosu node pnpm paperclipai onboard --yes || true
fi

# Install Anthropic document-skills plugin once per volume.
# Lives at /paperclip/.claude/plugins which is on organization-volume,
# so this runs only on first boot per volume (or after a volume reset).
if [ ! -d "/paperclip/.claude/plugins/document-skills" ]; then
    echo "Installing document-skills plugin..."
    gosu node claude /plugin marketplace add anthropics/skills || true
    gosu node claude /plugin install document-skills@anthropic-agent-skills || true
fi

# Register the Google Drive MCP server (stdio subprocess of claude).
# Skipped if GOOGLE_SERVICE_ACCOUNT_JSON is not set, since the MCP can't auth without it.
# Registration is at user scope so it persists in /paperclip/.claude across deploys.
if [ -n "$GOOGLE_SERVICE_ACCOUNT_JSON" ] && [ -f /app/mcp-gdrive/main.py ]; then
    if ! gosu node claude mcp get gdrive >/dev/null 2>&1; then
        echo "Registering gdrive MCP..."
        gosu node claude mcp add -s user gdrive -- \
            /opt/mcp-gdrive-venv/bin/python /app/mcp-gdrive/main.py --transport stdio || true
    fi
fi

exec gosu node "$@"
