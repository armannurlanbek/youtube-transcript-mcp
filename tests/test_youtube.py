import pytest

from ytmcp.errors import TranscriptError
from ytmcp.youtube import base_language, choose_track, classify_error, video_id_from_url

VID = "aB3_-xYz012"


@pytest.mark.parametrize("value", [
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtube.com/watch?v={VID}&t=42s",
    f"https://www.youtube.com/watch?v={VID}&list=PL123&index=2",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://music.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?si=abc&t=10",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/live/{VID}?feature=share",
    f"https://www.youtube.com/v/{VID}",
    f"www.youtube.com/watch?v={VID}",
    f"youtu.be/{VID}",
    f"  https://youtu.be/{VID}  ",
    VID,
])
def test_video_id_from_url_accepts(value):
    assert video_id_from_url(value) == VID


@pytest.mark.parametrize("value", [
    "", "hello", "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/@somechannel", "https://www.youtube.com/channel/UC123",
    "https://vimeo.com/123456", f"https://evil.example.com/watch?v={VID}",
    f"https://youtube.com.evil.example.com/watch?v={VID}",
    "https://www.youtube.com/watch?v=short", "https://youtu.be/",
    "https://www.youtube.com/watch?v=aB3_-xYz012extra",
    "aB3_-xYz01", "javascript:alert(1)",
])
def test_video_id_from_url_rejects(value):
    assert video_id_from_url(value) is None


def fmts(code):
    return [{"ext": "json3", "url": f"https://cap/{code}.json3"}, {"ext": "vtt", "url": f"https://cap/{code}.vtt"}]


def info(language=None, manual=(), auto=()):
    return {"language": language,
            "subtitles": {c: fmts(c) for c in manual},
            "automatic_captions": {c: fmts(c) for c in auto}}


def test_base_language():
    assert base_language("en-US") == "en"
    assert base_language("en-orig") == "en"
    assert base_language("RU") == "ru"


def test_original_prefers_creator_track_in_video_language():
    t = choose_track(info("en", manual=["de", "en-US"], auto=["en-orig", "en", "fr"]), None)
    assert (t.code, t.language, t.kind, t.url) == ("en-US", "en", "manual", "https://cap/en-US.vtt")


def test_original_exact_code_beats_regional():
    t = choose_track(info("en", manual=["en-GB", "en"]), None)
    assert t.code == "en"


def test_original_falls_back_to_orig_asr():
    t = choose_track(info("ru", manual=["en"], auto=["ru-orig", "ru", "en"]), None)
    assert (t.code, t.language, t.kind) == ("ru-orig", "ru", "auto")


def test_original_auto_in_video_language_without_orig():
    t = choose_track(info("kk", auto=["kk", "en"]), None)
    assert (t.code, t.kind) == ("kk", "auto")


def test_original_unknown_language_single_manual():
    t = choose_track(info(None, manual=["es"], auto=["en-orig", "en"]), None)
    assert (t.code, t.kind) == ("es", "manual")


def test_original_unknown_language_several_manual_uses_orig():
    t = choose_track(info(None, manual=["es", "de"], auto=["en-orig", "en"]), None)
    assert t.code == "en-orig"


def test_original_ignores_live_chat_and_tracks_without_vtt():
    data = info(None, auto=["en-orig"])
    data["subtitles"] = {"live_chat": [{"ext": "json", "url": "x"}], "de": [{"ext": "json3", "url": "y"}]}
    assert choose_track(data, None).code == "en-orig"


def test_original_nothing():
    with pytest.raises(TranscriptError) as e:
        choose_track(info("en"), None)
    assert e.value.kind == "no_captions"


def test_requested_language_manual_first():
    t = choose_track(info("en", manual=["ru"], auto=["en-orig", "ru"]), "ru")
    assert (t.code, t.kind) == ("ru", "manual")


def test_requested_language_same_as_spoken_uses_orig():
    t = choose_track(info("en", auto=["en-orig", "en", "ru"]), "en")
    assert (t.code, t.kind) == ("en-orig", "auto")


def test_requested_language_translation():
    t = choose_track(info("en", auto=["en-orig", "en", "ru"]), "RU")
    assert (t.code, t.language, t.kind) == ("ru", "ru", "translated")


def test_requested_language_missing_lists_available():
    with pytest.raises(TranscriptError) as e:
        choose_track(info("en", manual=["de"], auto=["en-orig"]), "fr")
    assert e.value.kind == "no_captions"
    assert e.value.detail == "in fr; available: de, en"


@pytest.mark.parametrize("message,kind", [
    ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", "login_required"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "unavailable"),
    ("ERROR: [youtube] x: Video unavailable", "unavailable"),
    ("ERROR: [youtube] x: Sign in to confirm your age", "unavailable"),
    ("ERROR: Join this channel to get access to members-only content", "unavailable"),
    ("HTTP Error 429: Too Many Requests", "failed"),
])
def test_classify_error(message, kind):
    assert classify_error(message) == kind


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_language_means_original(blank):
    t = choose_track(info("en", auto=["en-orig", "en"]), blank)
    assert (t.code, t.kind) == ("en-orig", "auto")


def test_translation_without_orig_track_is_labelled_translated():
    t = choose_track(info("en", auto=["en", "ru"]), "ru")
    assert (t.code, t.language, t.kind) == ("ru", "ru", "translated")


def test_spoken_language_without_orig_track_is_auto():
    t = choose_track(info("en", auto=["en", "ru"]), "en")
    assert (t.code, t.kind) == ("en", "auto")


def test_unknown_spoken_language_keeps_auto():
    t = choose_track(info(None, auto=["ru"]), "ru")
    assert (t.code, t.kind) == ("ru", "auto")
