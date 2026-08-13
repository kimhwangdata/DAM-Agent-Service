#!/usr/bin/env bash
# Deploy the agent to a Pi and restart the service (CLAUDE.md deploy loop).
#   scripts/deploy.sh <pi-host>        e.g. scripts/deploy.sh 192.168.70.109
# Prereq: scripts/provision-pi.sh ran once; /opt/dam-agent/.env.dev exists.
set -euo pipefail

HOST=${1:?usage: deploy.sh <pi-host>}
USER=cskim
DEST=/opt/dam-agent

scp -q -r agent "$USER@$HOST:$DEST/"
scp -q systemd/dam-agent.service "$USER@$HOST:/tmp/dam-agent.service"
ssh -t "$USER@$HOST" "
    sudo install -m 644 /tmp/dam-agent.service /etc/systemd/system/dam-agent.service &&
    rm /tmp/dam-agent.service &&
    sudo systemctl daemon-reload &&
    sudo systemctl enable dam-agent --quiet &&
    sudo systemctl restart dam-agent &&
    sleep 3 &&
    systemctl --no-pager -l status dam-agent | head -8 &&
    echo '--- recent log ---' &&
    journalctl -u dam-agent -n 5 --no-pager
"
