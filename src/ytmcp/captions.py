"""WebVTT captions to (start_seconds, line) cues."""

from __future__ import annotations

import html
import re

Cue = tuple[float, str]

_NOTE = re.compile(r"^NOTE(\s|$)")
_TAG = re.compile(r"<[^>]+>")


def _seconds(stamp: str) -> float:
    """'01:02:03.250' or '02:03.250' (a comma works too) -> seconds."""
    total = 0.0
    for part in stamp.strip().replace(",", ".").split(":"):
        total = total * 60 + float(part)
    return round(total, 3)


def _is_timing(line: str) -> bool:
    stamp = line.split("-->", 1)[0].strip() if "-->" in line else ""
    return stamp[:1].isdigit() and ":" in stamp  # spoken text may contain an arrow too


def _next_is_timing(lines: list[str], index: int) -> bool:
    for following in lines[index + 1:]:
        if following.strip():
            return _is_timing(following.strip())
    return False


def parse_vtt(vtt: str) -> list[Cue]:
    """Each new caption line with the start of the cue it first appeared in.

    YouTube auto-captions roll: a cue repeats the previous line and adds one,
    so a line equal to the last kept line is skipped. Inline word timings are
    stripped and HTML entities decoded (`&gt;&gt;`, the speaker-change mark,
    becomes `>>`). Everything before the first timing line is the file header;
    a number is a cue id only when a timing line follows it — so spoken
    "Language: …" or "2024" lines are kept."""
    lines = vtt.splitlines()
    cues: list[Cue] = []
    start = 0.0
    last: str | None = None
    started = False
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if _is_timing(line):
            started = True
            try:
                start = _seconds(line.split("-->", 1)[0].split()[0])
            except ValueError:  # malformed: keep the previous start
                pass
            continue
        if not started or _NOTE.match(line):
            continue
        if line.isdigit() and _next_is_timing(lines, index):
            continue
        line = html.unescape(_TAG.sub("", line)).strip()
        if not line or line == last:
            continue
        cues.append((start, line))
        last = line
    return cues
