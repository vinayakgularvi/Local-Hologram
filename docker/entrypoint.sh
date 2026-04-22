#!/bin/sh
set -e
mkdir -p /app/backend/data /app/backend/outputs
# Named volumes are often root-owned on first mount; fix ownership before dropping privileges.
chown -R appuser:appuser /app/backend/data /app/backend/outputs 2>/dev/null || true
exec gosu appuser "$@"
