"""The failures a transcript request can end in, and how each reads to a person."""

from __future__ import annotations

_MESSAGES = {
    "invalid_url": "Not a YouTube video link: {detail}",
    "login_required": "YouTube asked the server to sign in — its cookies have expired. "
                      "The operator needs to replace them.",
    "unavailable": "This video is private, removed, age-restricted, or members-only.",
    "timeout": "YouTube took too long. Try again in a minute.",
    "busy": "Another transcript is being made. Try again in a minute.",
    "not_found": "This video has not been transcribed yet — call transcribe_youtube first.",
}


def user_message(kind: str, detail: str = "") -> str:
    if kind == "no_captions":
        return f"This video has no captions ({detail})." if detail else "This video has no captions."
    if kind in _MESSAGES:
        return _MESSAGES[kind].format(detail=detail)
    return f"Could not fetch the transcript: {detail[:300]}."


class TranscriptError(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail

    def __str__(self) -> str:
        return user_message(self.kind, self.detail)
