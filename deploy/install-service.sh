#!/usr/bin/env bash
# Install and start the systemd unit. Run once as root:
#   sudo deploy/install-service.sh <user> [<repo dir>]
set -euo pipefail
USER_NAME="${1:?usage: install-service.sh <user> [<repo dir>]}"
DIR="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[ -n "$HOME_DIR" ] || { echo "no such user: $USER_NAME" >&2; exit 1; }
[ -f "$DIR/.env" ] || { echo "missing $DIR/.env (copy .env.example and fill it in)" >&2; exit 1; }
sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" -e "s|__HOME__|$HOME_DIR|g" \
  "$DIR/deploy/ytmcp.service" > /etc/systemd/system/ytmcp.service
systemctl daemon-reload
systemctl enable --now ytmcp.service
systemctl --no-pager status ytmcp.service | head -5
