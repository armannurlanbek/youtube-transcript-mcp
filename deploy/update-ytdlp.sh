#!/usr/bin/env bash
# Weekly: upgrade yt-dlp in the venv, check one live fetch, roll back if it fails.
# Run as the service user from cron, e.g.  17 2 * * 1  /path/to/repo/deploy/update-ytdlp.sh
# No restart is needed: every fetch runs in a fresh worker process.
set -uo pipefail
export PATH="$HOME/.deno/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
set -a; . "$REPO/.env" 2>/dev/null || true; set +a

version() { .venv/bin/python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo unknown; }

OLD="$(version)"
.venv/bin/pip install -q -U yt-dlp || echo "update-ytdlp: pip install failed; staying at $OLD" >&2
NEW="$(version)"
echo "update-ytdlp: yt-dlp $OLD -> $NEW"

if .venv/bin/python -m ytmcp.check; then exit 0; fi
if [ "$NEW" = "$OLD" ] || [ "$OLD" = "unknown" ]; then
  echo "update-ytdlp: check failed on $NEW; nothing to roll back to" >&2
  exit 1
fi
echo "update-ytdlp: check failed on $NEW; rolling back to $OLD" >&2
.venv/bin/pip install -q "yt-dlp==$OLD"
.venv/bin/python -m ytmcp.check
