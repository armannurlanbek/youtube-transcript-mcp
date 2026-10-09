from pathlib import Path

import pytest

from ytmcp.config import ConfigError, load_dotenv, load_settings
from ytmcp.errors import TranscriptError

KEY = "k" * 43


def test_minimal_env():
    s = load_settings({"YTM_SECRET_KEY": KEY, "YTM_PUBLIC_URL": "https://transcripts.example.com/"})
    assert s.public_url == "https://transcripts.example.com"
    assert s.public_host == "transcripts.example.com"
    assert s.base_url == f"https://transcripts.example.com/{KEY}"
    assert (s.host, s.port, s.data_dir, s.cookies, s.fetch_timeout) == (
        "127.0.0.1", 8090, Path("data"), None, 200.0)


def test_all_knobs():
    s = load_settings({"YTM_SECRET_KEY": KEY, "YTM_PUBLIC_URL": "http://localhost:8090",
                       "YTM_HOST": "0.0.0.0", "YTM_PORT": "9000", "YTM_DATA_DIR": "/tmp/x",
                       "YTM_COOKIES": " /c.txt ", "YTM_FETCH_TIMEOUT": "60"})
    assert (s.host, s.port, s.data_dir, s.cookies, s.fetch_timeout) == (
        "0.0.0.0", 9000, Path("/tmp/x"), "/c.txt", 60.0)
    assert s.public_host == "localhost:8090"


@pytest.mark.parametrize("key", ["", "short", "k" * 31, "k" * 40 + "/"])
def test_bad_key(key):
    with pytest.raises(ConfigError, match="YTM_SECRET_KEY"):
        load_settings({"YTM_SECRET_KEY": key, "YTM_PUBLIC_URL": "https://a.example.com"})


@pytest.mark.parametrize("url", ["", "transcripts.example.com", "ftp://a.example.com"])
def test_bad_public_url(url):
    with pytest.raises(ConfigError, match="YTM_PUBLIC_URL"):
        load_settings({"YTM_SECRET_KEY": KEY, "YTM_PUBLIC_URL": url})


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nYTM_A=from_file\nYTM_B='quoted'\nexport YTM_C=x\n\nBROKEN\n")
    monkeypatch.setenv("YTM_A", "real")
    monkeypatch.delenv("YTM_B", raising=False)
    monkeypatch.delenv("YTM_C", raising=False)
    load_dotenv(env)
    import os
    assert os.environ["YTM_A"] == "real"
    assert os.environ["YTM_B"] == "quoted"
    assert os.environ["YTM_C"] == "x"


def test_load_dotenv_missing_file_is_fine(tmp_path):
    load_dotenv(tmp_path / "nope.env")


def test_transcript_error_messages():
    assert str(TranscriptError("busy")) == "Another transcript is being made. Try again in a minute."
    assert str(TranscriptError("invalid_url", "https://x.com")) == "Not a YouTube video link: https://x.com"
    assert str(TranscriptError("no_captions")) == "This video has no captions."
    assert str(TranscriptError("no_captions", "in fr; available: en, ru")) == \
        "This video has no captions (in fr; available: en, ru)."
    assert str(TranscriptError("failed", "x" * 500)).endswith("x" * 300 + ".")
    assert len(str(TranscriptError("failed", "x" * 500))) < 360
    assert TranscriptError("timeout").kind == "timeout"
    assert "cookies" in str(TranscriptError("login_required"))
    assert "transcribe_youtube" in str(TranscriptError("not_found"))
