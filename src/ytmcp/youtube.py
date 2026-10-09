"""YouTube: links to video ids, choosing a caption track, and (Task 6) fetching it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from ytmcp.errors import TranscriptError

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_PATH_ID = re.compile(r"/(?:shorts|embed|live|v)/([^/]+)/?")
_LANG = re.compile(r"[a-z]{2,3}")


def video_id_from_url(value: str) -> str | None:
    """The 11-character id of a single-video YouTube link (or a bare id), else None."""
    value = value.strip()
    if VIDEO_ID.fullmatch(value):
        return value
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https"):
        return None
    if host == "youtu.be":
        candidate = parsed.path.strip("/")
    elif host in _HOSTS:
        if parsed.path.rstrip("/") == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = _PATH_ID.fullmatch(parsed.path)
            candidate = match.group(1) if match else ""
    else:
        return None
    return candidate if VIDEO_ID.fullmatch(candidate) else None


@dataclass(frozen=True)
class Track:
    code: str
    language: str
    kind: str  # "manual" | "auto" | "translated"
    url: str


def base_language(code: str) -> str:
    return code.split("-")[0].lower()


def _vtt_tracks(tracks: dict | None) -> dict[str, str]:
    """{code: vtt url} for usable tracks (a VTT format and a plain language base)."""
    out: dict[str, str] = {}
    for code, formats in (tracks or {}).items():
        if not _LANG.fullmatch(base_language(code)):
            continue
        for fmt in formats or []:
            if fmt.get("ext") == "vtt" and fmt.get("url"):
                out[code] = fmt["url"]
                break
    return out


def _pick(tracks: dict[str, str], language: str) -> str | None:
    """Exact code first, then any code with the same base (in yt-dlp's order)."""
    if language in tracks:
        return language
    return next((code for code in tracks if base_language(code) == language), None)


def choose_track(info: dict, language: str | None) -> Track:
    manual = _vtt_tracks(info.get("subtitles"))
    auto = _vtt_tracks(info.get("automatic_captions"))
    orig = next((code for code in auto if code.endswith("-orig")), None)
    language = (language or "").strip() or None
    if orig:
        spoken = base_language(orig)
    else:
        spoken = base_language(info["language"]) if info.get("language") else None

    def track(code: str, kind: str, source: dict[str, str]) -> Track:
        return Track(code=code, language=base_language(code), kind=kind, url=source[code])

    if language is None:
        video_lang = base_language(info["language"]) if info.get("language") else None
        if video_lang:
            if code := _pick(manual, video_lang):
                return track(code, "manual", manual)
            if orig:
                return track(orig, "auto", auto)
            if code := _pick(auto, video_lang):
                return track(code, "auto", auto)
        else:
            if len(manual) == 1:
                return track(next(iter(manual)), "manual", manual)
            if orig:
                return track(orig, "auto", auto)
        raise TranscriptError("no_captions")

    wanted = base_language(language)
    if code := _pick(manual, wanted):
        return track(code, "manual", manual)
    if orig and spoken == wanted:
        return track(orig, "auto", auto)
    if code := _pick(auto, wanted):
        return track(code, "auto" if spoken in (None, wanted) else "translated", auto)
    available = sorted({base_language(c) for c in manual} | ({spoken} if spoken else set()))
    raise TranscriptError("no_captions", f"in {wanted}; available: {', '.join(available) or 'none'}")


_UNAVAILABLE = ("private video", "video unavailable", "confirm your age", "members-only",
                "members only", "this video has been removed", "this video is not available")


def classify_error(message: str) -> str:
    low = message.lower()
    if "not a bot" in low:
        return "login_required"
    if any(marker in low for marker in _UNAVAILABLE):
        return "unavailable"
    return "failed"
