"""Cues to readable, timestamped Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ytmcp.captions import Cue
from ytmcp.youtube import Fetched

KIND_LABEL = {"manual": "creator captions", "auto": "auto-generated captions",
              "translated": "auto-translated captions"}
_SPEAKER = re.compile(r"\s*>>\s*")
_SENTENCE_END = (".", "?", "!", "…")


@dataclass(frozen=True)
class Paragraph:
    start: float
    text: str


def format_clock(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def watch_url(video_id: str, seconds: float | None = None) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    return url if seconds is None else f"{url}&t={int(seconds)}s"


def paragraphs(cues: list[Cue], *, soft: float = 45.0, hard: float = 90.0) -> list[Paragraph]:
    """Group cues into paragraphs: a `>>` speaker mark always starts one; otherwise
    one closes at the first sentence end after `soft` seconds, and at `hard` regardless."""
    out: list[Paragraph] = []
    parts: list[str] = []
    start = 0.0

    def flush() -> None:
        if parts:
            out.append(Paragraph(start, re.sub(r"\s+", " ", " ".join(parts)).strip()))
            parts.clear()

    for cue_start, line in cues:
        segments = _SPEAKER.split(line)
        for index, segment in enumerate(segments):
            segment = segment.strip()
            new_speaker = index > 0
            if parts:
                elapsed = cue_start - start
                if (new_speaker or elapsed >= hard
                        or (elapsed >= soft and parts[-1].endswith(_SENTENCE_END))):
                    flush()
            if not segment:
                continue
            if not parts:
                start = cue_start
            parts.append(segment)
    flush()
    return out


def _one_line(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return "\\" + text if text.startswith("#") else text


def to_markdown(fetched: Fetched, cues: list[Cue], *, transcribed: date) -> str:
    vid = fetched.video_id
    lines = [f"# {_one_line(fetched.title) or vid}", ""]
    if fetched.channel.strip():
        lines.append(f"- **Channel:** {_one_line(fetched.channel)}")
    lines.append(f"- **Video:** {watch_url(vid)}")
    if fetched.duration_seconds:
        lines.append(f"- **Length:** {format_clock(fetched.duration_seconds)}")
    if fetched.published:
        lines.append(f"- **Published:** {fetched.published}")
    label = KIND_LABEL.get(fetched.track.kind, fetched.track.kind)
    lines.append(f"- **Language:** {fetched.track.language} ({label})")
    lines.append(f"- **Transcribed:** {transcribed.isoformat()}")
    lines += ["", "---", ""]
    for p in paragraphs(cues):
        lines += [f"[{format_clock(p.start)}]({watch_url(vid, p.start)}) {p.text}", ""]
    return "\n".join(lines).rstrip("\n") + "\n"
