"""One fetch in a child process: `python -m ytmcp.worker <video_id> <language|->`.

Prints exactly one JSON object on stdout and exits 0 (errors are JSON too).
The parent kills this process on timeout — a thread could not be killed."""

from __future__ import annotations

import json
import os
import sys

from ytmcp.errors import TranscriptError
from ytmcp.youtube import VIDEO_ID, fetch_captions


def run(argv: list[str], *, fetch=fetch_captions, cookiefile: str | None = None) -> dict:
    if len(argv) != 2 or not VIDEO_ID.fullmatch(argv[0]):
        return {"ok": False, "kind": "invalid_url", "detail": argv[0] if argv else ""}
    video_id, language = argv[0], (None if argv[1] == "-" else argv[1])
    try:
        fetched = fetch(video_id, language, cookiefile=cookiefile)
    except TranscriptError as exc:
        return {"ok": False, "kind": exc.kind, "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001 - always answer in JSON
        return {"ok": False, "kind": "failed", "detail": str(exc)}
    return {"ok": True, **fetched.to_json()}


def main() -> None:
    result = run(sys.argv[1:], cookiefile=os.environ.get("YTM_COOKIES") or None)
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
