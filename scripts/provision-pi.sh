#!/usr/bin/env bash
# Provision a Pi for dam-agent (idempotent). Run ON the Pi:
#   ssh -t cskim@<pi> 'bash -s' < scripts/provision-pi.sh
# Installs picamera2 (apt), creates /opt/dam-agent with a
# --system-site-packages venv (so apt's picamera2 is visible) and the
# agent's small runtime deps (no boto3 on devices — ADR-0003).
set -euo pipefail

DEST=/opt/dam-agent

sudo apt-get update -qq
sudo apt-get install -y -qq python3-picamera2 python3-venv

sudo mkdir -p "$DEST"
sudo chown "$USER":"$USER" "$DEST"

if [ ! -x "$DEST/.venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$DEST/.venv"
fi
# typing-extensions: python-ulid needs it on Python 3.11 (Bookworm)
"$DEST/.venv/bin/pip" install --quiet --upgrade \
    python-dotenv python-ulid typing-extensions

echo "provisioned: $DEST (python: $("$DEST/.venv/bin/python" --version))"
echo "next: put the stage env at $DEST/.env.dev, then scripts/deploy.sh <pi>"
