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
    got = fetch_captions(VID, None, cookiefile="/nonexistent/c.txt", ydl_factory=FakeYDL)
    assert got == Fetched(video_id=VID, title="Birdhouse", channel="Shop Talk", duration_seconds=600,
                          published="2026-10-01",
                          track=Track("en-orig", "en", "auto", "https://cap/en-orig.vtt"),
                          vtt="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n")
    assert FakeYDL.last.opened == ["https://cap/en-orig.vtt"]
    assert FakeYDL.last.options["cookiefile"] == "/nonexistent/c.txt"  # no jar yet: yt-dlp makes it


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


def test_cookie_path_never_leaks_in_error_detail():
    path = "/secret/dir/cookies.txt"
    err = OSError(f"[Errno 13] Permission denied: '{path}'")
    with pytest.raises(TranscriptError) as e:
        fetch_captions(VID, None, cookiefile=path, ydl_factory=lambda o: FakeYDL(o, error=err))
    assert path not in e.value.detail and "<cookies>" in e.value.detail

    def fetch(video_id, language, *, cookiefile):
        return fetch_captions(video_id, language, cookiefile=cookiefile,
                              ydl_factory=lambda o: FakeYDL(o, error=err))
    out = worker.run([VID, "-"], fetch=fetch, cookiefile=path)
    assert path not in json.dumps(out) and "<cookies>" in out["detail"]


def test_fetch_captions_rejects_bad_video_id():
    with pytest.raises(TranscriptError) as e:
        fetch_captions("bad", None, ydl_factory=FakeYDL)
    assert (e.value.kind, e.value.detail) == ("invalid_url", "bad")


def test_main_prints_one_json_line_on_crash(capsys, monkeypatch):
    monkeypatch.delenv("YTM_COOKIES", raising=False)

    def crash(video_id, language, *, cookiefile):
        raise RuntimeError("boom")
    worker.main([VID, "-"], fetch=crash)
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {"ok": False, "kind": "failed", "detail": "boom"}


def test_main_keeps_stray_prints_off_stdout(capsys, monkeypatch):
    monkeypatch.delenv("YTM_COOKIES", raising=False)

    def noisy(video_id, language, *, cookiefile):
        print("noise")
        return Fetched(VID, "T", "C", 1, None, Track("en", "en", "manual", "u"), "vtt")
    worker.main([VID, "-"], fetch=noisy)
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1 and json.loads(captured.out)["ok"] is True
    assert "noise" in captured.err


@pytest.mark.parametrize("env,expected", [("", None), ("/c.txt", "/c.txt")])
def test_main_passes_cookie_env(capsys, monkeypatch, env, expected):
    monkeypatch.setenv("YTM_COOKIES", env)
    seen = {}

    def fetch(video_id, language, *, cookiefile):
        seen["cookiefile"] = cookiefile
        raise TranscriptError("no_captions")
    worker.main([VID, "-"], fetch=fetch)
    assert seen["cookiefile"] == expected


def test_escaped_cookie_path_is_scrubbed():
    path = "C:\\secret\\cookies.txt"
    err = OSError(13, "Permission denied", path)
    assert "C:\\\\secret" in str(err)  # the OS shows the repr form: doubled backslashes
    with pytest.raises(TranscriptError) as e:
        fetch_captions(VID, None, cookiefile=path, ydl_factory=lambda o: FakeYDL(o, error=err))
    assert path not in e.value.detail and "C:\\\\secret" not in e.value.detail
    assert "<cookies>" in e.value.detail

    def fetch(video_id, language, *, cookiefile):
        return fetch_captions(video_id, language, cookiefile=cookiefile,
                              ydl_factory=lambda o: FakeYDL(o, error=err))
    dumped = json.dumps(worker.run([VID, "-"], fetch=fetch, cookiefile=path))
    assert "secret" not in dumped and "<cookies>" in dumped


def test_main_keeps_fd1_clean_from_child_processes():
    code = (
        "import subprocess\n"
        "from ytmcp import worker\n"
        "def f(v, l, *, cookiefile):\n"
        "    subprocess.run(['echo', 'fd-noise'])\n"
        "    raise RuntimeError('x')\n"
        f"worker.main({VID!r}.split() + ['-'], fetch=f)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert proc.stdout.count("\n") == 1
    assert json.loads(proc.stdout)["kind"] == "failed"
    assert "fd-noise" in proc.stderr


class JarYDL(FakeYDL):
    """Like yt-dlp: writes its cookie jar to the file it was given when it closes."""
    def __exit__(self, *exc):
        with open(self.options["cookiefile"], "w") as f:
            f.write("NEW JAR")
        return False


def test_cookie_jar_is_updated_atomically_from_a_copy(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("OLD JAR")
    seen = {}

    def factory(options):
        seen["path"] = options["cookiefile"]
        seen["during"] = open(options["cookiefile"]).read()
        seen["mode"] = oct(__import__("os").stat(options["cookiefile"]).st_mode & 0o777)
        return JarYDL(options)
    fetch_captions(VID, None, cookiefile=str(jar), ydl_factory=factory)
    assert seen["path"] != str(jar) and seen["during"] == "OLD JAR" and seen["mode"] == "0o600"
    assert jar.read_text() == "NEW JAR"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cookies.txt"]


def test_cookie_jar_untouched_and_no_temp_left_on_failure(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("OLD JAR")
    with pytest.raises(TranscriptError) as e:
        fetch_captions(VID, None, cookiefile=str(jar),
                       ydl_factory=lambda o: JarYDL(o, error=RuntimeError(f"HTTP 500 {o['cookiefile']}")))
    assert jar.read_text() == "OLD JAR"
    assert [p.name for p in tmp_path.iterdir()] == ["cookies.txt"]
    assert str(tmp_path) not in e.value.detail and "<cookies>" in e.value.detail


def test_cookie_jar_untouched_when_no_track(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("OLD JAR")
    with pytest.raises(TranscriptError):
        fetch_captions(VID, None, cookiefile=str(jar), ydl_factory=lambda o: JarYDL(o, info={"language": "en"}))
    assert jar.read_text() == "OLD JAR" and [p.name for p in tmp_path.iterdir()] == ["cookies.txt"]
