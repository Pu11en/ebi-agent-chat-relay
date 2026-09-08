#!/bin/bash
# Restart a user service only after its local ccdb health endpoint repeatedly fails.
set -u

HEALTH_URL="${CCDB_HEALTH_URL:-http://127.0.0.1:9876/api/health}"
SERVICE="${CCDB_SERVICE:-ebi-agent-chat-relay.service}"
RETRIES="${CCDB_HEALTH_RETRIES:-3}"
RETRY_DELAY="${CCDB_HEALTH_RETRY_DELAY:-5}"

if ! [[ "$RETRIES" =~ ^[1-9][0-9]*$ ]] || ! [[ "$RETRY_DELAY" =~ ^[0-9]+$ ]]; then
    echo "[health-check] Invalid retry configuration" >&2
    exit 2
fi

for ((attempt = 1; attempt <= RETRIES; attempt++)); do
    response=$(curl --fail --silent --show-error --max-time 10 "$HEALTH_URL" 2>/dev/null || true)
    if printf '%s' "$response" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        exit 0
    fi
    if [ "$attempt" -lt "$RETRIES" ]; then
        sleep "$RETRY_DELAY"
    fi
done

echo "[health-check] Health failed $RETRIES time(s); restarting $SERVICE" >&2
systemctl --user restart "$SERVICE"
