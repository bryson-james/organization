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

if [ "$changed" = "1" ]; then
    chown -R node:node /paperclip
fi

# Auto-onboard if no config exists (non-interactive Docker deployment)
CONFIG_PATH="${PAPERCLIP_CONFIG:-/paperclip/instances/default/config.json}"
if [ ! -f "$CONFIG_PATH" ]; then
    echo "No config found, running onboard..."
    gosu node pnpm paperclipai onboard --yes || true
fi

# Generate bootstrap CEO invite if in authenticated mode
if [ "$PAPERCLIP_DEPLOYMENT_MODE" = "authenticated" ]; then
    echo "Generating bootstrap CEO invite in background..."
    (
        sleep 10
        gosu node pnpm paperclipai auth bootstrap-ceo \
            --base-url "${PAPERCLIP_PUBLIC_URL:-${BETTER_AUTH_BASE_URL:-http://localhost:3100}}" \
            || true
    ) &
fi

exec gosu node "$@"
