#!/bin/sh
set -e

# Capture runtime UID/GID from environment variables, defaulting to 1000
PUID=${USER_UID:-1000}
PGID=${USER_GID:-1000}

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

exec gosu node "$@"
