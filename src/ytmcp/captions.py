"""WebVTT captions to (start_seconds, line) cues."""

from __future__ import annotations

import html
import re

Cue = tuple[float, str]

_NOTE = re.compile(r"^NOTE(\s|$)")
_TAG = re.compile(r"<[^>]+>")
_STAMP = re.compile(r"^(\d+:)?\d{1,2}:\d{2}[.,]\d{3}$")


def _seconds(stamp: str) -> float:
    """'01:02:03.250' or '02:03.250' (a comma works too) -> seconds."""
    total = 0.0
    for part in stamp.strip().replace(",", ".").split(":"):
        total = total * 60 + float(part)
    return round(total, 3)


def _is_timing(line: str) -> bool:
    """A cue timing line: a full WebVTT timestamp before the arrow."""
    return "-->" in line and bool(_STAMP.match(line.split("-->", 1)[0].strip()))


def _is_malformed_timing(line: str) -> bool:
    """Timing-shaped garbage ('00:xx:01.000 --> 00:00:02.000'): skipped, not spoken."""
    if "-->" not in line:
        return False
    left, right = line.split("-->", 1)
    left, right = left.strip(), right.split()
    return left[:1].isdigit() and ":" in left and bool(right) and bool(_STAMP.match(right[0]))


def parse_vtt(vtt: str) -> list[Cue]:
    """Each new caption line with the start of the cue it first appeared in.

    YouTube auto-captions roll: a cue repeats the previous line and adds one,
    so a line equal to the last kept line is skipped. Inline word timings are
    stripped and HTML entities decoded (`&gt;&gt;`, the speaker-change mark,
    becomes `>>`). Everything before the first timing line is the file header;
    a number is a cue id only when a timing line is the very next line — so spoken
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
            start = _seconds(line.split("-->", 1)[0])
            continue
        if _is_malformed_timing(line):  # keep the previous start
            started = True
            continue
        if not started:
            continue
        if _NOTE.match(line) and not lines[index - 1].strip():
            continue  # a NOTE block starts after a blank line; cue text never does
        if line.isdigit() and index + 1 < len(lines) and (
                _is_timing(lines[index + 1].strip())
                or _is_malformed_timing(lines[index + 1].strip())):
            continue  # a cue id: the very next line is its timing
        line = html.unescape(_TAG.sub("", line)).strip()
        if not line or line == last:
            continue
        cues.append((start, line))
        last = line
    return cues
