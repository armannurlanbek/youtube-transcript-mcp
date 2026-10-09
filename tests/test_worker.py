import json
import subprocess
import sys

import pytest

from ytmcp import worker
from ytmcp.errors import TranscriptError
from ytmcp.youtube import Fetched, Track, fetch_captions, ytdlp_options

VID = "aB3_-xYz012"
INFO = {"title": "Birdhouse", "channel": "Shop Talk", "uploader": "shoptalk", "duration": 600.7,
        "upload_date": "20261001", "language": "en",
        "subtitles": {}, "automatic_captions": {"en-orig": [{"ext": "vtt", "url": "https://cap/en-orig.vtt"}]}}


class FakeResponse:
    def __init__(self, body): self.body = body
    def read(self): return self.body


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: records options, returns canned info/captions."""
    last = None

    def __init__(self, options, info=INFO, error=None):
        self.options, self.info, self.error, self.opened = options, info, error, []
        FakeYDL.last = self

    def __enter__(self): return self
    def __exit__(self, *exc): return False

    def extract_info(self, url, download):
        assert url == f"https://www.youtube.com/watch?v={VID}" and download is False
        if self.error:
            raise self.error
        return self.info

    def urlopen(self, url):
        self.opened.append(url)
        return FakeResponse("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n".encode())


def test_ytdlp_options():
    opts = ytdlp_options("/c.txt")
    assert opts["skip_download"] is True and opts["quiet"] is True
    assert opts["extractor_args"] == {"youtube": {"player_client": ["web_embedded"]}}
    assert opts["remote_components"] == ["ejs:github"]
    assert opts["cookiefile"] == "/c.txt"
    assert "cookiefile" not in ytdlp_options(None)


def test_fetch_captions_success():
    got = fetch_captions(VID, None, cookiefile="/c.txt", ydl_factory=FakeYDL)
    assert got == Fetched(video_id=VID, title="Birdhouse", channel="Shop Talk", duration_seconds=600,
                          published="2026-10-01",
                          track=Track("en-orig", "en", "auto", "https://cap/en-orig.vtt"),
                          vtt="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n")
    assert FakeYDL.last.opened == ["https://cap/en-orig.vtt"]
    assert FakeYDL.last.options["cookiefile"] == "/c.txt"


def test_fetch_captions_missing_metadata():
    info = {"automatic_captions": INFO["automatic_captions"], "uploader": "up"}
    got = fetch_captions(VID, None, ydl_factory=lambda o: FakeYDL(o, info=info))
    assert (got.title, got.channel, got.duration_seconds, got.published) == (VID, "up", None, None)


@pytest.mark.parametrize("message,kind", [
    ("Sign in to confirm you’re not a bot", "login_required"),
    ("Video unavailable", "unavailable"),
    ("HTTP Error 500", "failed"),
])
def test_fetch_captions_errors(message, kind):
    with pytest.raises(TranscriptError) as e:
        fetch_captions(VID, None, ydl_factory=lambda o: FakeYDL(o, error=RuntimeError(message)))
    assert e.value.kind == kind


def test_fetch_captions_no_track():
    with pytest.raises(TranscriptError) as e:
        fetch_captions(VID, None, ydl_factory=lambda o: FakeYDL(o, info={"language": "en"}))
    assert e.value.kind == "no_captions"


def test_worker_run_ok_roundtrips():
    def fake(video_id, language, *, cookiefile):
        assert (video_id, language, cookiefile) == (VID, "ru", "/c.txt")
        return Fetched(VID, "T", "C", 1, None, Track("ru", "ru", "translated", "u"), "vtt")
    out = worker.run([VID, "ru"], fetch=fake, cookiefile="/c.txt")
    assert out["ok"] is True
    out.pop("ok")
    assert Fetched.from_json(json.loads(json.dumps(out))).track.kind == "translated"


def test_worker_run_dash_means_original_and_errors_become_json():
    def fake(video_id, language, *, cookiefile):
        assert language is None
        raise TranscriptError("no_captions", "x")
    assert worker.run([VID, "-"], fetch=fake) == {"ok": False, "kind": "no_captions", "detail": "x"}

    def crash(video_id, language, *, cookiefile):
        raise ValueError("boom")
    assert worker.run([VID, "-"], fetch=crash) == {"ok": False, "kind": "failed", "detail": "boom"}


def test_worker_rejects_bad_args():
    assert worker.run(["not-an-id", "-"])["kind"] == "invalid_url"
    assert worker.run([])["kind"] == "invalid_url"


def test_worker_module_prints_one_json_line():
    proc = subprocess.run([sys.executable, "-m", "ytmcp.worker", "bad", "-"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"ok": False, "kind": "invalid_url", "detail": "bad"}
