"""python -m ytmcp.check [video_id] — one live fetch, for the weekly yt-dlp upgrade.

Uses a temporary copy of the cookies so it never races the running service,
which owns the real file."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from ytmcp.captions import parse_vtt
from ytmcp.config import load_dotenv
from ytmcp.errors import TranscriptError
from ytmcp.youtube import fetch_captions

DEFAULT_VIDEO = "dQw4w9WgXcQ"
MIN_LINES = 10


def run_check(video_id: str, cookies: str | None, *, fetch=fetch_captions) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        cookie_copy = None
        if cookies and Path(cookies).is_file():
            cookie_copy = str(Path(tmp) / "cookies.txt")
            shutil.copyfile(cookies, cookie_copy)
        try:
            fetched = fetch(video_id, None, cookiefile=cookie_copy)
        except TranscriptError as exc:
            return False, f"check failed for {video_id}: {exc}"
        lines = len(parse_vtt(fetched.vtt))
    if lines < MIN_LINES:
        return False, f"check failed for {video_id}: only {lines} caption lines"
    return True, f"ok: {video_id} «{fetched.title}» — {lines} caption lines ({fetched.track.code})"


def main() -> None:
    load_dotenv(Path(".env"))
    video_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("YTM_CHECK_VIDEO", DEFAULT_VIDEO)
    ok, message = run_check(video_id, os.environ.get("YTM_COOKIES") or None)
    print(message)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
