from pathlib import Path

from ytmcp.check import run_check
from ytmcp.errors import TranscriptError
from ytmcp.youtube import Fetched, Track

VID = "aB3_-xYz012"
VTT = "WEBVTT\n\n" + "".join(f"00:00:{i:02d}.000 --> 00:00:{i:02d}.500\nline {i}\n\n" for i in range(12))


def test_check_ok_uses_a_cookie_copy(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    seen = {}

    def fake(video_id, language, *, cookiefile):
        seen["path"] = cookiefile
        Path(cookiefile).write_text("rewritten")
        return Fetched(VID, "T", "C", 1, None, Track("en", "en", "manual", "u"), VTT)

    ok, message = run_check(VID, str(cookies), fetch=fake)
    assert ok is True and "12 caption lines" in message
    assert seen["path"] != str(cookies)
    assert cookies.read_text() == "# Netscape HTTP Cookie File\n"


def test_check_fails_on_error_and_on_too_few_lines():
    def err(video_id, language, *, cookiefile):
        raise TranscriptError("login_required")

    ok, message = run_check(VID, None, fetch=err)
    assert ok is False and "cookies" in message

    def few(video_id, language, *, cookiefile):
        return Fetched(VID, "T", "C", 1, None, Track("en", "en", "manual", "u"), "WEBVTT\n")

    assert run_check(VID, None, fetch=few)[0] is False
