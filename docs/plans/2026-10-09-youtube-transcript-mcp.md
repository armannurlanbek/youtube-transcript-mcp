# YouTube Transcript MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted remote MCP server that turns a YouTube link into a timestamped Markdown transcript, served at a secret URL, with a read-only history page.

**Architecture:** One Starlette app (uvicorn, loopback) with the MCP Streamable HTTP endpoint, the history page and file downloads all under `/<secret-key>/`. yt-dlp runs in a child process per fetch (killable on timeout), one fetch at a time. SQLite holds the history; `.md` files live on disk.

**Tech Stack:** Python ≥ 3.12, `mcp` 2.3.x (`mcp.server.mcpserver.MCPServer`), Starlette 1.x, uvicorn, yt-dlp, SQLite (stdlib), pytest + anyio.

**Spec:** `docs/specs/2026-10-09-youtube-transcript-mcp-design.md` — read it before starting any task.

## Global Constraints

- Repo is public and generic: **no organisation names, real hostnames, keys, or server details** anywhere in code, docs, tests, or commit messages. Use `transcripts.example.com` in examples.
- **No network in tests.** Every yt-dlp call goes through an injectable boundary.
- **Test captions are synthetic** (written for this repo in YouTube's VTT format) — never copied from a real video.
- Package `ytmcp` under `src/`; run everything with the repo venv: `.venv/bin/python -m pytest -q`.
- MCP SDK 2.3 API (verified 2026-10-09): `from mcp.server.mcpserver import MCPServer`; `from mcp.server.mcpserver.exceptions import ToolError`; `from mcp.server.transport_security import TransportSecuritySettings`; `mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=...)`; the parent app's lifespan must run `async with mcp.session_manager.run():` (only after `streamable_http_app()` was called; `run()` works once per manager — build a fresh app per TestClient); in-memory tests: `from mcp import Client` → `async with Client(mcp) as client: r = await client.call_tool(name, args)`, read `r.is_error`, `r.content[0].text`. `ToolError("x")` reaches the client as `"Error executing tool <name>: x"`. Starlette's `TestClient` uses `httpx2` (installed with `mcp`).
- Limits: tool result ≤ 150,000 chars (we page at 100,000); fetch deadline 200 s; lock wait 30 s.
- Error kinds (exact strings): `invalid_url`, `login_required`, `unavailable`, `no_captions`, `timeout`, `busy`, `failed`, `not_found`.
- Commit after every task with a conventional message; **no `Co-Authored-By` or any Claude/AI attribution** in commits.

## Review Focus

1. A YouTube link with extra parameters (`&t=42s`, `&list=…`, `?si=…`), a `youtube.com/...` link with no scheme, or a bare 11-char id → recognised as that video (Task 2 tests pin these).
2. A video whose title/channel contain `<script>`, `"`, `#`, newlines, or Cyrillic → page escapes it, `.md` header stays one line, download filename is safe, search finds Cyrillic case-insensitively (Tasks 4, 5, 8).
3. Two requests for the same new video at once → the second waits, then gets the cached result without a second fetch (Task 7).
4. A worker that hangs or prints garbage → killed / mapped to `timeout` / `failed`, the lock released, nothing stored (Task 7).
5. A requested key prefix match like `/<key>x/` or `/<key-without-last-char>/` and path traversal `t/../transcripts.db` → 404 (Task 8).

---

## File Structure

```
pyproject.toml            package metadata, deps, pytest config
.gitignore, .env.example
README.md                 what it is, local run, deploy, connect in Claude
src/ytmcp/
  __init__.py
  __main__.py             python -m ytmcp → uvicorn
  config.py               Settings, load_settings, load_dotenv
  errors.py               TranscriptError + user-facing messages
  youtube.py              URL → id; Track/choose_track; Fetched; fetch_captions (yt-dlp boundary)
  captions.py             parse_vtt → cues
  render.py               paragraphs, format_clock, to_markdown
  store.py                SQLite + files: TranscriptRecord, Store
  worker.py               child-process entry: python -m ytmcp.worker <id> <lang|->
  service.py              subprocess_runner, Transcriber (cache, lock, deadline)
  tools.py                build_mcp: the three MCP tools
  web.py                  history page, style.css, file download
  app.py                  build_app: key gate, headers, MCP mount, lifespan
  check.py                python -m ytmcp.check → one live fetch (deploy health check)
deploy/ytmcp.service, deploy/install-service.sh, deploy/update-ytdlp.sh
tests/ test_config.py test_youtube.py test_captions.py test_render.py test_store.py
       test_worker.py test_service.py test_tools.py test_app.py
       fixtures/auto_captions.vtt fixtures/manual_captions.vtt
```

---

### Task 1: Scaffold, config, errors

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/ytmcp/__init__.py`, `src/ytmcp/config.py`, `src/ytmcp/errors.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` (frozen dataclass: `secret_key: str`, `public_url: str`, `host: str = "127.0.0.1"`, `port: int = 8090`, `data_dir: Path = Path("data")`, `cookies: str | None = None`, `fetch_timeout: float = 200.0`, `lock_wait: float = 30.0`; properties `public_host -> str` (netloc of public_url) and `base_url -> str` = `f"{public_url}/{secret_key}"`); `ConfigError(Exception)`; `load_settings(env: Mapping[str, str]) -> Settings`; `load_dotenv(path: Path) -> None`; `TranscriptError(kind: str, detail: str = "")` with `.kind`, `.detail`, `str()` = user message; `user_message(kind, detail) -> str`.

- [ ] **Step 1: Create the scaffold**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "youtube-transcript-mcp"
version = "0.1.0"
description = "A remote MCP server that turns YouTube links into timestamped Markdown transcripts."
readme = "README.md"
requires-python = ">=3.12"
license = "MIT"
dependencies = ["mcp>=2.3,<3", "starlette>=1.0", "uvicorn>=0.30", "yt-dlp"]

[project.optional-dependencies]
dev = ["pytest>=8", "anyio>=4"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/
.env
*.egg-info/
cookies*.txt
```

`.env.example`:
```bash
# Required. The secret path segment: every URL is https://<host>/<key>/...
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
YTM_SECRET_KEY=
# Required. The public base URL (no trailing slash), used for links and the allowed Host.
YTM_PUBLIC_URL=https://transcripts.example.com
# Bind address (put a reverse proxy or tunnel in front).
YTM_HOST=127.0.0.1
YTM_PORT=8090
# Where the database and .md files live.
YTM_DATA_DIR=./data
# Path to a Netscape cookies.txt owned by THIS service alone (yt-dlp rewrites it). Optional.
YTM_COOKIES=
# Seconds a single fetch may take before it is killed.
YTM_FETCH_TIMEOUT=200
```

`src/ytmcp/__init__.py`: `"""YouTube transcripts as a remote MCP server."""`

Create the venv and install: `python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"` (use `python3.12`+; on macOS `python3` may be 3.14 — fine).

- [ ] **Step 2: Write the failing tests** — `tests/test_config.py`

```python
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
```

- [ ] **Step 3: Run** `.venv/bin/python -m pytest tests/test_config.py -q` → FAIL (ModuleNotFoundError).

- [ ] **Step 4: Implement** `src/ytmcp/errors.py`:

```python
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
```

`src/ytmcp/config.py`:

```python
"""Settings from the environment (and an optional .env file)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_KEY = re.compile(r"[A-Za-z0-9_-]{32,}")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    secret_key: str
    public_url: str
    host: str = "127.0.0.1"
    port: int = 8090
    data_dir: Path = Path("data")
    cookies: str | None = None
    fetch_timeout: float = 200.0
    lock_wait: float = 30.0

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc

    @property
    def base_url(self) -> str:
        return f"{self.public_url}/{self.secret_key}"


def load_settings(env: Mapping[str, str]) -> Settings:
    key = env.get("YTM_SECRET_KEY", "").strip()
    if not _KEY.fullmatch(key):
        raise ConfigError("YTM_SECRET_KEY must be at least 32 characters of A-Z a-z 0-9 _ -")
    public_url = env.get("YTM_PUBLIC_URL", "").strip().rstrip("/")
    parsed = urlparse(public_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError("YTM_PUBLIC_URL must be an http(s) URL, e.g. https://transcripts.example.com")
    return Settings(
        secret_key=key,
        public_url=public_url,
        host=env.get("YTM_HOST", "").strip() or "127.0.0.1",
        port=int(env.get("YTM_PORT", "").strip() or 8090),
        data_dir=Path(env.get("YTM_DATA_DIR", "").strip() or "data"),
        cookies=env.get("YTM_COOKIES", "").strip() or None,
        fetch_timeout=float(env.get("YTM_FETCH_TIMEOUT", "").strip() or 200),
    )


def load_dotenv(path: Path) -> None:
    """Set variables from a KEY=VALUE file; real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)
```

- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/test_config.py -q` → PASS.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: scaffold, settings and error messages"`

---

### Task 2: YouTube URLs, track choice, error classification

**Files:**
- Create: `src/ytmcp/youtube.py` (pure part only; Task 6 adds the network part)
- Test: `tests/test_youtube.py`

**Interfaces:**
- Consumes: `TranscriptError` (Task 1).
- Produces: `video_id_from_url(value: str) -> str | None`; `Track` (frozen dataclass: `code: str` yt-dlp key, `language: str` base code, `kind: str` in `manual|auto|translated`, `url: str` VTT url); `choose_track(info: dict, language: str | None) -> Track` (raises `TranscriptError("no_captions", detail)`); `base_language(code: str) -> str`; `classify_error(message: str) -> str` returning `login_required|unavailable|failed`.

- [ ] **Step 1: Write the failing tests** — `tests/test_youtube.py`

```python
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
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_youtube.py -q` → FAIL.

- [ ] **Step 3: Implement** `src/ytmcp/youtube.py`:

```python
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
    spoken = base_language(orig) if orig else None

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

    wanted = base_language(language.strip())
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
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_youtube.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: YouTube link parsing and caption track choice"`

---

### Task 3: Caption parsing

**Files:**
- Create: `src/ytmcp/captions.py`, `tests/fixtures/auto_captions.vtt`, `tests/fixtures/manual_captions.vtt`
- Test: `tests/test_captions.py`

**Interfaces:**
- Produces: `Cue = tuple[float, str]`; `parse_vtt(vtt: str) -> list[Cue]`.

- [ ] **Step 1: Write the synthetic fixtures** (YouTube auto-caption format: each cue repeats the previous line, inline word timings, 10 ms transition cues, `&gt;&gt;` speaker marks, a blank-with-space line).

`tests/fixtures/auto_captions.vtt`:
```
WEBVTT
Kind: captions
Language: en

00:00:00.160 --> 00:00:02.869 align:start position:0%
 
welcome<00:00:00.480><c> back</c><00:00:00.640><c> to</c><00:00:00.800><c> the</c><00:00:01.000><c> workshop</c>

00:00:02.869 --> 00:00:02.879 align:start position:0%
welcome back to the workshop
 

00:00:02.879 --> 00:00:05.990 align:start position:0%
welcome back to the workshop
today<00:00:03.120><c> we</c><00:00:03.300><c> build</c><00:00:03.500><c> a</c><00:00:03.700><c> birdhouse.</c>

00:00:05.990 --> 00:00:06.000 align:start position:0%
today we build a birdhouse.
 

00:00:06.000 --> 00:00:08.430 align:start position:0%
today we build a birdhouse.
&gt;&gt;<00:00:06.300><c> sounds</c><00:00:06.500><c> great</c><00:00:06.700><c> &amp;</c><00:00:06.900><c> simple</c>

00:00:08.430 --> 00:00:08.440 align:start position:0%
&gt;&gt; sounds great &amp; simple
 
```

`tests/fixtures/manual_captions.vtt`:
```
WEBVTT
Kind: captions
Language: en

1
00:00:01.000 --> 00:00:03.500
Measure twice,

2
00:00:03.500 --> 00:00:06.000
cut once.

3
01:02:03.250 --> 01:02:05.000
<i>That's the rule</i> --> always.

NOTE this is a comment

4
01:02:05.000 --> 01:02:07.000
cut once.
```

- [ ] **Step 2: Write the failing tests** — `tests/test_captions.py`

```python
from pathlib import Path

from ytmcp.captions import parse_vtt

FIX = Path(__file__).parent / "fixtures"


def test_auto_captions_rolling_repeats_removed():
    cues = parse_vtt((FIX / "auto_captions.vtt").read_text(encoding="utf-8"))
    assert cues == [
        (0.16, "welcome back to the workshop"),
        (2.879, "today we build a birdhouse."),
        (6.0, ">> sounds great & simple"),
    ]


def test_manual_captions_keep_timings_and_text():
    cues = parse_vtt((FIX / "manual_captions.vtt").read_text(encoding="utf-8"))
    assert cues == [
        (1.0, "Measure twice,"),
        (3.5, "cut once."),
        (3723.25, "That's the rule --> always."),
        (3725.0, "cut once."),
    ]


def test_empty_and_header_only():
    assert parse_vtt("") == []
    assert parse_vtt("WEBVTT\nKind: captions\nLanguage: en\n") == []


def test_spoken_lines_that_look_like_headers_are_kept():
    vtt = ("WEBVTT\nKind: captions\nLanguage: en\n\n"
           "00:00:01.000 --> 00:00:02.000\nLanguage: it matters\n\n"
           "00:00:02.000 --> 00:00:03.000\n2024\n\n"
           "7\n00:00:03.000 --> 00:00:04.000\nwas the year\n")
    assert parse_vtt(vtt) == [(1.0, "Language: it matters"), (2.0, "2024"), (3.0, "was the year")]


def test_malformed_timing_keeps_previous_start():
    vtt = "WEBVTT\n\n00:00:05.000 --> 00:00:06.000\nfirst\n\n00:xx:01.000 --> 00:00:02.000\nsecond\n"
    assert parse_vtt(vtt) == [(5.0, "first"), (5.0, "second")]
```

- [ ] **Step 3: Run** `.venv/bin/python -m pytest tests/test_captions.py -q` → FAIL.

- [ ] **Step 4: Implement** `src/ytmcp/captions.py`:

```python
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
```

Note: only a one-line `NOTE` is skipped after the header; YouTube emits none inside the cues.

- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/test_captions.py -q` → PASS.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: WebVTT caption parsing with rolling-repeat removal"`

---

### Task 4: Markdown rendering

**Files:**
- Modify: `src/ytmcp/youtube.py` (add `Fetched` dataclass only)
- Create: `src/ytmcp/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Track` (Task 2), `Cue` (Task 3).
- Produces: in `youtube.py`: `Fetched` (frozen dataclass: `video_id: str`, `title: str`, `channel: str`, `duration_seconds: int | None`, `published: str | None` (`YYYY-MM-DD`), `track: Track`, `vtt: str`; methods `to_json() -> dict`, classmethod `from_json(data: dict) -> Fetched`). In `render.py`: `Paragraph` (frozen: `start: float`, `text: str`); `paragraphs(cues, *, soft: float = 45.0, hard: float = 90.0) -> list[Paragraph]`; `format_clock(seconds: float) -> str`; `watch_url(video_id: str, seconds: float | None = None) -> str`; `to_markdown(fetched: Fetched, cues: list[Cue], *, transcribed: date) -> str`; `KIND_LABEL: dict[str, str]`.

- [ ] **Step 1: Add `Fetched` to `src/ytmcp/youtube.py`** (append; add `from dataclasses import asdict` to imports):

```python
@dataclass(frozen=True)
class Fetched:
    video_id: str
    title: str
    channel: str
    duration_seconds: int | None
    published: str | None  # YYYY-MM-DD
    track: Track
    vtt: str

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Fetched":
        fields = {k: data[k] for k in ("video_id", "title", "channel", "duration_seconds", "published", "vtt")}
        return cls(track=Track(**data["track"]), **fields)
```

- [ ] **Step 2: Write the failing tests** — `tests/test_render.py`

```python
from datetime import date

from ytmcp.render import Paragraph, format_clock, paragraphs, to_markdown, watch_url
from ytmcp.youtube import Fetched, Track

VID = "aB3_-xYz012"


def fetched(**over):
    base = dict(video_id=VID, title="Building a Birdhouse", channel="Shop Talk",
                duration_seconds=9667, published="2026-10-01",
                track=Track(code="en-orig", language="en", kind="auto", url="u"), vtt="")
    base.update(over)
    return Fetched(**base)


def test_format_clock():
    assert format_clock(0) == "0:00"
    assert format_clock(47.9) == "0:47"
    assert format_clock(605) == "10:05"
    assert format_clock(3600) == "1:00:00"
    assert format_clock(9667) == "2:41:07"


def test_watch_url():
    assert watch_url(VID) == f"https://www.youtube.com/watch?v={VID}"
    assert watch_url(VID, 47.9) == f"https://www.youtube.com/watch?v={VID}&t=47s"


def test_speaker_mark_starts_paragraph_and_is_dropped():
    cues = [(0.0, "hello there."), (2.0, ">> hi, how are you"), (4.0, "fine")]
    assert paragraphs(cues) == [Paragraph(0.0, "hello there."), Paragraph(2.0, "hi, how are you fine")]


def test_speaker_mark_mid_line_splits():
    cues = [(0.0, "so that's it. >> right >> yes")]
    assert paragraphs(cues) == [Paragraph(0.0, "so that's it."), Paragraph(0.0, "right"), Paragraph(0.0, "yes")]


def test_bare_speaker_mark_line():
    cues = [(0.0, "one."), (3.0, ">>"), (4.0, "two")]
    assert paragraphs(cues) == [Paragraph(0.0, "one."), Paragraph(4.0, "two")]


def test_soft_break_waits_for_sentence_end():
    cues = [(0.0, "a"), (30.0, "b"), (46.0, "c without end"), (50.0, "d."), (55.0, "e")]
    assert paragraphs(cues) == [Paragraph(0.0, "a b c without end d."), Paragraph(55.0, "e")]


def test_hard_break_at_90_seconds():
    cues = [(0.0, "a"), (60.0, "b"), (90.0, "c"), (100.0, "d")]
    assert paragraphs(cues) == [Paragraph(0.0, "a b"), Paragraph(90.0, "c d")]


def test_whitespace_collapsed():
    assert paragraphs([(0.0, "  a   b "), (1.0, "c")]) == [Paragraph(0.0, "a b c")]


def test_empty():
    assert paragraphs([]) == []


def test_to_markdown_full():
    md = to_markdown(fetched(), [(0.0, "hello."), (47.5, ">> next part")], transcribed=date(2026, 10, 9))
    assert md == (
        "# Building a Birdhouse\n\n"
        "- **Channel:** Shop Talk\n"
        f"- **Video:** https://www.youtube.com/watch?v={VID}\n"
        "- **Length:** 2:41:07\n"
        "- **Published:** 2026-10-01\n"
        "- **Language:** en (auto-generated captions)\n"
        "- **Transcribed:** 2026-10-09\n\n"
        "---\n\n"
        f"[0:00](https://www.youtube.com/watch?v={VID}&t=0s) hello.\n\n"
        f"[0:47](https://www.youtube.com/watch?v={VID}&t=47s) next part\n"
    )


def test_to_markdown_missing_fields_and_odd_title():
    md = to_markdown(fetched(title="# Big\nNews <b>", channel="", duration_seconds=None, published=None,
                             track=Track("ru", "ru", "translated", "u")),
                     [(0.0, "x")], transcribed=date(2026, 10, 9))
    assert md.startswith("# \\# Big News <b>\n\n")
    assert "Length" not in md and "Published" not in md and "Channel" not in md
    assert "- **Language:** ru (auto-translated captions)\n" in md
```

- [ ] **Step 3: Run** `.venv/bin/python -m pytest tests/test_render.py -q` → FAIL.

- [ ] **Step 4: Implement** `src/ytmcp/render.py`:

```python
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
```

Note: the soft rule checks `parts[-1]` (the last segment added) — "the text so far ends a sentence".

- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/test_render.py -q` → PASS. Also run the whole suite.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: render cues as timestamped Markdown paragraphs"`

---

### Task 5: Store (SQLite + files)

**Files:**
- Create: `src/ytmcp/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Fetched` (Task 4).
- Produces: `TranscriptRecord` (frozen dataclass: `video_id: str`, `language: str`, `is_original: bool`, `track_kind: str`, `title: str`, `channel: str`, `duration_seconds: int | None`, `published: str | None`, `file_name: str`, `chars: int`, `words: int`, `created_at: str`); `FILE_NAME: re.Pattern` = `^[A-Za-z0-9_-]{11}\.[a-z]{2,3}\.md$`; `Store(data_dir: Path)` with `find(video_id: str, language: str | None) -> TranscriptRecord | None`, `latest(video_id: str) -> TranscriptRecord | None`, `by_file_name(file_name: str) -> TranscriptRecord | None`, `save(*, fetched: Fetched, markdown: str, is_original: bool, now: datetime) -> TranscriptRecord`, `list(search: str | None = None, limit: int = 20) -> list[TranscriptRecord]`, `read_markdown(record: TranscriptRecord) -> str`.

- [ ] **Step 1: Write the failing tests** — `tests/test_store.py`

```python
from datetime import UTC, datetime

from ytmcp.store import Store
from ytmcp.youtube import Fetched, Track

VID = "aB3_-xYz012"
VID2 = "Zz9_-xYz012"


def fetched(video_id=VID, language="en", kind="auto", title="Birdhouse", channel="Shop Talk"):
    return Fetched(video_id=video_id, title=title, channel=channel, duration_seconds=600,
                   published="2026-10-01", track=Track(f"{language}-orig", language, kind, "u"), vtt="")


def at(hour):
    return datetime(2026, 10, 9, hour, tzinfo=UTC)


def test_save_writes_file_and_row(tmp_path):
    store = Store(tmp_path)
    rec = store.save(fetched=fetched(), markdown="# T\n\nhello world\n", is_original=True, now=at(1))
    assert rec.file_name == f"{VID}.en.md"
    assert (tmp_path / "transcripts" / rec.file_name).read_text(encoding="utf-8") == "# T\n\nhello world\n"
    assert (rec.chars, rec.words, rec.is_original, rec.track_kind) == (17, 4, True, "auto")
    assert rec.created_at == "2026-10-09T01:00:00+00:00"
    assert store.read_markdown(rec) == "# T\n\nhello world\n"
    assert not list((tmp_path / "transcripts").glob(".*"))  # no temp files left


def test_find_original_vs_language(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(language="en"), markdown="en", is_original=True, now=at(1))
    store.save(fetched=fetched(language="ru", kind="translated"), markdown="ru", is_original=False, now=at(2))
    assert store.find(VID, None).language == "en"
    assert store.find(VID, "ru").language == "ru"
    assert store.find(VID, "en").language == "en"
    assert store.find(VID, "de") is None
    assert store.find(VID2, None) is None
    assert store.latest(VID).language == "ru"


def test_save_replaces_and_keeps_original_flag(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(), markdown="old", is_original=True, now=at(1))
    rec = store.save(fetched=fetched(title="New title"), markdown="new text", is_original=False, now=at(2))
    assert rec.is_original is True and rec.title == "New title"
    assert store.read_markdown(rec) == "new text"
    assert len(store.list()) == 1


def test_list_newest_first_search_and_limit(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(VID, title="Обзор рынка", channel="Канал"), markdown="a", is_original=True, now=at(1))
    store.save(fetched=fetched(VID2, title="Birdhouse"), markdown="b", is_original=True, now=at(2))
    assert [r.video_id for r in store.list()] == [VID2, VID]
    assert [r.video_id for r in store.list("ОБЗОР")] == [VID]
    assert [r.video_id for r in store.list("канал")] == [VID]
    assert [r.video_id for r in store.list("shop talk")] == [VID2]
    assert [r.video_id for r in store.list("%")] == []
    assert len(store.list(limit=1)) == 1


def test_by_file_name(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(), markdown="x", is_original=True, now=at(1))
    assert store.by_file_name(f"{VID}.en.md").video_id == VID
    assert store.by_file_name("../transcripts.db") is None
    assert store.by_file_name(f"{VID}.de.md") is None


def test_reopen_keeps_data(tmp_path):
    Store(tmp_path).save(fetched=fetched(), markdown="x", is_original=True, now=at(1))
    assert Store(tmp_path).find(VID, None) is not None
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_store.py -q` → FAIL.

- [ ] **Step 3: Implement** `src/ytmcp/store.py`:

```python
"""The transcript history: one SQLite table plus one .md file per transcript."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ytmcp.youtube import Fetched

FILE_NAME = re.compile(r"^[A-Za-z0-9_-]{11}\.[a-z]{2,3}\.md$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    language TEXT NOT NULL,
    is_original INTEGER NOT NULL,
    track_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    duration_seconds INTEGER,
    published TEXT,
    file_name TEXT NOT NULL,
    chars INTEGER NOT NULL,
    words INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (video_id, language)
);
"""

_COLUMNS = ("video_id, language, is_original, track_kind, title, channel, duration_seconds, "
            "published, file_name, chars, words, created_at")


@dataclass(frozen=True)
class TranscriptRecord:
    video_id: str
    language: str
    is_original: bool
    track_kind: str
    title: str
    channel: str
    duration_seconds: int | None
    published: str | None
    file_name: str
    chars: int
    words: int
    created_at: str


def _record(row: tuple) -> TranscriptRecord:
    values = list(row)
    values[2] = bool(values[2])
    return TranscriptRecord(*values)


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.files = Path(data_dir) / "transcripts"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(data_dir) / "transcripts.db"
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _one(self, where: str, params: tuple) -> TranscriptRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM transcripts WHERE {where} ORDER BY created_at DESC, id DESC LIMIT 1",
                params).fetchone()
        return _record(row) if row else None

    def find(self, video_id: str, language: str | None) -> TranscriptRecord | None:
        if language is None:
            return self._one("video_id = ? AND is_original = 1", (video_id,))
        return self._one("video_id = ? AND language = ?", (video_id, language))

    def latest(self, video_id: str) -> TranscriptRecord | None:
        return self._one("video_id = ?", (video_id,))

    def by_file_name(self, file_name: str) -> TranscriptRecord | None:
        if not FILE_NAME.fullmatch(file_name):
            return None
        return self._one("file_name = ?", (file_name,))

    def save(self, *, fetched: Fetched, markdown: str, is_original: bool, now: datetime) -> TranscriptRecord:
        language = fetched.track.language
        file_name = f"{fetched.video_id}.{language}.md"
        if not FILE_NAME.fullmatch(file_name):
            raise ValueError(f"unsafe file name: {file_name!r}")
        self._write_atomic(self.files / file_name, markdown)
        values = (fetched.video_id, language, int(is_original), fetched.track.kind, fetched.title,
                  fetched.channel, fetched.duration_seconds, fetched.published, file_name,
                  len(markdown), len(markdown.split()), now.isoformat())
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO transcripts ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (video_id, language) DO UPDATE SET "
                "is_original = MAX(transcripts.is_original, excluded.is_original), "
                "track_kind = excluded.track_kind, title = excluded.title, channel = excluded.channel, "
                "duration_seconds = excluded.duration_seconds, published = excluded.published, "
                "file_name = excluded.file_name, chars = excluded.chars, words = excluded.words, "
                "created_at = excluded.created_at",
                values)
        found = self.find(fetched.video_id, language)
        assert found is not None
        return found

    def list(self, search: str | None = None, limit: int = 20) -> list[TranscriptRecord]:
        """Newest first. Search is a case-insensitive substring of title or channel,
        done in Python so non-ASCII titles match too (SQLite's lower() is ASCII-only)."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM transcripts ORDER BY created_at DESC, id DESC").fetchall()
        records = [_record(r) for r in rows]
        if search and search.strip():
            needle = search.strip().casefold()
            records = [r for r in records if needle in r.title.casefold() or needle in r.channel.casefold()]
        return records[:max(limit, 0)]

    def read_markdown(self, record: TranscriptRecord) -> str:
        return (self.files / record.file_name).read_text(encoding="utf-8")

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_store.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: transcript store (SQLite history + Markdown files)"`

---

### Task 6: The yt-dlp fetch and the worker process

**Files:**
- Modify: `src/ytmcp/youtube.py` (add `ytdlp_options`, `fetch_captions`)
- Create: `src/ytmcp/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `choose_track`, `classify_error`, `Fetched`, `Track` (Tasks 2, 4); `TranscriptError` (Task 1).
- Produces: `ytdlp_options(cookiefile: str | None) -> dict`; `fetch_captions(video_id: str, language: str | None, *, cookiefile: str | None = None, ydl_factory=None) -> Fetched` (raises `TranscriptError`); in `worker.py`: `run(argv: list[str], *, fetch=fetch_captions, cookiefile: str | None = None) -> dict` returning `{"ok": True, **Fetched.to_json()}` or `{"ok": False, "kind": str, "detail": str}`; `main() -> None` (reads `YTM_COOKIES` from env, prints `json.dumps(run(sys.argv[1:]))`, exit 0). Protocol: `python -m ytmcp.worker <video_id> <language or ->`.

- [ ] **Step 1: Write the failing tests** — `tests/test_worker.py`

```python
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
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_worker.py -q` → FAIL.

- [ ] **Step 3: Implement** — append to `src/ytmcp/youtube.py`:

```python
def ytdlp_options(cookiefile: str | None) -> dict:
    options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        # YouTube withholds captions from datacenter IPs unless the request
        # (a) solves a player-JS challenge and (b) uses a client that does not
        # need a PO token. `web_embedded` returns captions without a PO token;
        # the EJS remote component solves the JS challenge in deno (deno must
        # be on PATH).
        "extractor_args": {"youtube": {"player_client": ["web_embedded"]}},
        "remote_components": ["ejs:github"],
    }
    if cookiefile:
        # Datacenter IPs need a signed-in session. yt-dlp writes the cookie jar
        # back to this file on close, so it must belong to this service alone.
        options["cookiefile"] = cookiefile
    return options


def _published(upload_date: object) -> str | None:
    text = str(upload_date or "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if re.fullmatch(r"\d{8}", text) else None


def fetch_captions(video_id: str, language: str | None, *, cookiefile: str | None = None,
                   ydl_factory=None) -> Fetched:
    """One metadata request, then exactly one caption track — never several languages
    at once (that triggers HTTP 429)."""
    if ydl_factory is None:
        import yt_dlp
        ydl_factory = yt_dlp.YoutubeDL
    try:
        with ydl_factory(ytdlp_options(cookiefile)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False) or {}
            track = choose_track(info, language)
            vtt = ydl.urlopen(track.url).read().decode("utf-8", errors="replace")
    except TranscriptError:
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types; the message decides
        raise TranscriptError(classify_error(str(exc)), str(exc)) from exc
    duration = info.get("duration")
    return Fetched(
        video_id=video_id,
        title=str(info.get("title") or video_id),
        channel=str(info.get("channel") or info.get("uploader") or ""),
        duration_seconds=int(duration) if isinstance(duration, (int, float)) else None,
        published=_published(info.get("upload_date")),
        track=track,
        vtt=vtt,
    )
```

`src/ytmcp/worker.py`:

```python
"""One fetch in a child process: `python -m ytmcp.worker <video_id> <language|->`.

Prints exactly one JSON object on stdout and exits 0 (errors are JSON too).
The parent kills this process on timeout — a thread could not be killed."""

from __future__ import annotations

import json
import os
import sys

from ytmcp.errors import TranscriptError
from ytmcp.youtube import VIDEO_ID, fetch_captions


def run(argv: list[str], *, fetch=fetch_captions, cookiefile: str | None = None) -> dict:
    if len(argv) != 2 or not VIDEO_ID.fullmatch(argv[0]):
        return {"ok": False, "kind": "invalid_url", "detail": argv[0] if argv else ""}
    video_id, language = argv[0], (None if argv[1] == "-" else argv[1])
    try:
        fetched = fetch(video_id, language, cookiefile=cookiefile)
    except TranscriptError as exc:
        return {"ok": False, "kind": exc.kind, "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001 - always answer in JSON
        return {"ok": False, "kind": "failed", "detail": str(exc)}
    return {"ok": True, **fetched.to_json()}


def main() -> None:
    result = run(sys.argv[1:], cookiefile=os.environ.get("YTM_COOKIES") or None)
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_worker.py -q` → PASS; then the whole suite.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: yt-dlp caption fetch run in a killable worker process"`

---

### Task 7: Transcriber service (cache, single flight, deadline)

**Files:**
- Create: `src/ytmcp/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `Settings` (1), `TranscriptError` (1), `parse_vtt` (3), `to_markdown` (4), `Fetched` (4), `Store`, `TranscriptRecord` (5).
- Produces: `Runner = Callable[[str, str | None], Awaitable[dict]]`; `async subprocess_runner(video_id: str, language: str | None, *, timeout: float, cookies: str | None, command: list[str] | None = None) -> dict` (raises `TranscriptError("timeout")` after killing the child, `TranscriptError("failed", …)` on bad output); `Transcriber(store: Store, settings: Settings, runner: Runner | None = None, today: Callable[[], datetime] | None = None)` with `async transcribe(video_id: str, language: str | None) -> tuple[TranscriptRecord, bool]` (bool = served from cache).

- [ ] **Step 1: Write the failing tests** — `tests/test_service.py`

```python
import asyncio
import sys
from datetime import UTC, datetime

import pytest

from ytmcp.config import Settings
from ytmcp.errors import TranscriptError
from ytmcp.service import Transcriber, subprocess_runner
from ytmcp.store import Store
from ytmcp.youtube import Fetched, Track

pytestmark = pytest.mark.anyio
VID = "aB3_-xYz012"
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello there.\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def ok_result(language="en", vtt=VTT):
    return {"ok": True, **Fetched(VID, "Birdhouse", "Shop Talk", 600, "2026-10-01",
                                  Track(f"{language}-orig", language, "auto", "u"), vtt).to_json()}


def make(tmp_path, runner, lock_wait=30.0):
    settings = Settings(secret_key="k" * 32, public_url="https://t.example.com", data_dir=tmp_path,
                        lock_wait=lock_wait)
    return Transcriber(Store(tmp_path), settings, runner=runner,
                       today=lambda: datetime(2026, 10, 9, 12, tzinfo=UTC))


async def test_miss_then_hit(tmp_path):
    calls = []

    async def runner(video_id, language):
        calls.append((video_id, language))
        return ok_result()

    t = make(tmp_path, runner)
    rec, cached = await t.transcribe(VID, None)
    assert (rec.language, rec.is_original, cached) == ("en", True, False)
    assert "hello there." in t.store.read_markdown(rec)
    assert "- **Transcribed:** 2026-10-09" in t.store.read_markdown(rec)
    rec2, cached2 = await t.transcribe(VID, None)
    assert cached2 is True and rec2 == rec
    rec3, cached3 = await t.transcribe(VID, "en")  # same language as the original: cached
    assert cached3 is True
    assert calls == [(VID, None)]


async def test_concurrent_same_video_fetches_once(tmp_path):
    calls = []

    async def runner(video_id, language):
        calls.append(1)
        await asyncio.sleep(0.05)
        return ok_result()

    t = make(tmp_path, runner)
    (r1, c1), (r2, c2) = await asyncio.gather(t.transcribe(VID, None), t.transcribe(VID, None))
    assert len(calls) == 1 and {c1, c2} == {False, True}


async def test_busy_when_lock_not_acquired(tmp_path):
    release = asyncio.Event()

    async def runner(video_id, language):
        await release.wait()
        return ok_result()

    t = make(tmp_path, runner, lock_wait=0.05)
    first = asyncio.create_task(t.transcribe(VID, None))
    await asyncio.sleep(0.01)
    with pytest.raises(TranscriptError) as e:
        await t.transcribe("Zz9_-xYz012", None)
    assert e.value.kind == "busy"
    release.set()
    await first


@pytest.mark.parametrize("result,kind", [
    ({"ok": False, "kind": "no_captions", "detail": ""}, "no_captions"),
    (ok_result(vtt="WEBVTT\n"), "no_captions"),
])
async def test_failures_store_nothing_and_release_lock(tmp_path, result, kind):
    async def runner(video_id, language):
        return result

    t = make(tmp_path, runner)
    with pytest.raises(TranscriptError) as e:
        await t.transcribe(VID, None)
    assert e.value.kind == kind
    assert t.store.list() == []
    assert not t._lock.locked()


async def test_runner_exception_releases_lock(tmp_path):
    async def runner(video_id, language):
        raise TranscriptError("timeout")

    t = make(tmp_path, runner)
    with pytest.raises(TranscriptError):
        await t.transcribe(VID, None)
    assert not t._lock.locked()


async def test_subprocess_runner_parses_json():
    out = await subprocess_runner(VID, "ru", timeout=30, cookies=None,
                                  command=[sys.executable, "-c",
                                           "import json,sys; print(json.dumps({'ok': False, 'kind': 'x', 'detail': sys.argv[1:]}))"])
    assert out == {"ok": False, "kind": "x", "detail": [VID, "ru"]}


async def test_subprocess_runner_passes_dash_and_cookies_env():
    out = await subprocess_runner(VID, None, timeout=30, cookies="/c.txt",
                                  command=[sys.executable, "-c",
                                           "import json,os,sys; print(json.dumps([sys.argv[2], os.environ['YTM_COOKIES']]))"])
    assert out == ["-", "/c.txt"]


async def test_subprocess_runner_kills_on_timeout():
    start = asyncio.get_running_loop().time()
    with pytest.raises(TranscriptError) as e:
        await subprocess_runner(VID, None, timeout=0.5, cookies=None,
                                command=[sys.executable, "-c", "import time; time.sleep(30)"])
    assert e.value.kind == "timeout"
    assert asyncio.get_running_loop().time() - start < 5


async def test_subprocess_runner_garbage_output():
    with pytest.raises(TranscriptError) as e:
        await subprocess_runner(VID, None, timeout=30, cookies=None,
                                command=[sys.executable, "-c", "import sys; print('nope'); sys.exit(3)"])
    assert e.value.kind == "failed"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_service.py -q` → FAIL.

- [ ] **Step 3: Implement** `src/ytmcp/service.py`:

```python
"""Turning a video id into a stored transcript: cache, one fetch at a time, a deadline."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ytmcp.captions import parse_vtt
from ytmcp.config import Settings
from ytmcp.errors import TranscriptError
from ytmcp.render import to_markdown
from ytmcp.store import Store, TranscriptRecord
from ytmcp.youtube import Fetched

Runner = Callable[[str, str | None], Awaitable[dict]]


async def subprocess_runner(video_id: str, language: str | None, *, timeout: float,
                            cookies: str | None, command: list[str] | None = None) -> dict:
    """Run the worker as a child process; kill it if it passes the deadline."""
    argv = (command or [sys.executable, "-m", "ytmcp.worker"]) + [video_id, language or "-"]
    env = {**os.environ, "YTM_COOKIES": cookies or ""}
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TranscriptError("timeout") from None
    try:
        return json.loads(out.decode("utf-8", errors="replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        tail = err.decode("utf-8", errors="replace").strip()[-300:]
        raise TranscriptError("failed", tail or f"worker exited with code {proc.returncode}") from None


class Transcriber:
    def __init__(self, store: Store, settings: Settings, runner: Runner | None = None,
                 today: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.settings = settings
        self._runner = runner or (lambda vid, lang: subprocess_runner(
            vid, lang, timeout=settings.fetch_timeout, cookies=settings.cookies))
        self._now = today or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()

    async def transcribe(self, video_id: str, language: str | None) -> tuple[TranscriptRecord, bool]:
        if (found := self.store.find(video_id, language)) is not None:
            return found, True
        try:
            await asyncio.wait_for(self._lock.acquire(), self.settings.lock_wait)
        except TimeoutError:
            raise TranscriptError("busy") from None
        try:
            # Another request may have made it while this one waited.
            if (found := self.store.find(video_id, language)) is not None:
                return found, True
            result = await self._runner(video_id, language)
            if not result.get("ok"):
                raise TranscriptError(str(result.get("kind") or "failed"), str(result.get("detail") or ""))
            result = {k: v for k, v in result.items() if k != "ok"}
            fetched = Fetched.from_json(result)
            cues = parse_vtt(fetched.vtt)
            if not cues:
                raise TranscriptError("no_captions")
            now = self._now()
            markdown = to_markdown(fetched, cues, transcribed=now.date())
            record = self.store.save(fetched=fetched, markdown=markdown,
                                     is_original=language is None, now=now)
            return record, False
        finally:
            self._lock.release()
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_service.py -q` → PASS; then the whole suite.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: transcriber with cache, single-flight lock and fetch deadline"`

---

### Task 8: MCP tools, web page, app assembly

**Files:**
- Create: `src/ytmcp/tools.py`, `src/ytmcp/web.py`, `src/ytmcp/app.py`, `src/ytmcp/__main__.py`
- Test: `tests/test_tools.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `Settings` (1), `TranscriptError` (1), `video_id_from_url` (2), `format_clock`, `watch_url` (4), `Store`, `TranscriptRecord`, `FILE_NAME` (5), `Transcriber` (7).
- Produces: `tools.build_mcp(transcriber: Transcriber, settings: Settings) -> MCPServer`; `tools.split_pages(text: str, size: int = 100_000) -> list[str]`; `tools.PAGE_SIZE = 100_000`; `web.web_routes(store: Store, settings: Settings) -> list[Route]`; `web.render_history(records, q: str, prefix: str) -> str`; `web.download_name(title: str, video_id: str) -> str`; `app.build_app(settings: Settings, *, transcriber: Transcriber | None = None) -> ASGIApp` (the full app incl. key gate, headers, lifespan).

- [ ] **Step 1: Write the failing tool tests** — `tests/test_tools.py`

```python
from datetime import UTC, datetime

import pytest
from mcp import Client

from ytmcp.config import Settings
from ytmcp.service import Transcriber
from ytmcp.store import Store
from ytmcp.tools import build_mcp, split_pages
from ytmcp.youtube import Fetched, Track

pytestmark = pytest.mark.anyio
VID = "aB3_-xYz012"
KEY = "k" * 32


@pytest.fixture
def anyio_backend():
    return "asyncio"


def vtt(lines):
    return "WEBVTT\n\n" + "".join(f"00:00:{i:02d}.000 --> 00:00:{i:02d}.500\n{t}\n\n" for i, t in enumerate(lines))


def make(tmp_path, body_lines=("hello there.", ">> second speaker")):
    calls = []

    async def runner(video_id, language):
        calls.append((video_id, language))
        return {"ok": True, **Fetched(video_id, "Birdhouse", "Shop Talk", 9667, "2026-10-01",
                                      Track("en-orig", "en", "auto", "u"), vtt(body_lines)).to_json()}

    settings = Settings(secret_key=KEY, public_url="https://t.example.com", data_dir=tmp_path)
    t = Transcriber(Store(tmp_path), settings, runner=runner,
                    today=lambda: datetime(2026, 10, 9, tzinfo=UTC))
    return build_mcp(t, settings), calls


async def call(mcp, name, args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


async def test_tools_listed(tmp_path):
    mcp, _ = make(tmp_path)
    async with Client(mcp) as client:
        names = sorted(t.name for t in (await client.list_tools()).tools)
    assert names == ["list_transcripts", "read_transcript", "transcribe_youtube"]


async def test_transcribe_summary(tmp_path):
    mcp, calls = make(tmp_path)
    r = await call(mcp, "transcribe_youtube", {"url": f"https://youtu.be/{VID}?si=x"})
    text = r.content[0].text
    assert r.is_error is False
    assert text.startswith("Transcript ready: Birdhouse\n")
    assert "Shop Talk · 2:41:07 · published 2026-10-01 · language: en (auto-generated captions)" in text
    assert f"Download: https://t.example.com/{KEY}/t/{VID}.en.md" in text
    assert "Preview:\n[0:00]" in text
    assert "cached" not in text
    assert calls == [(VID, None)]
    r2 = await call(mcp, "transcribe_youtube", {"url": VID})
    assert "(from history)" in r2.content[0].text and len(calls) == 1


async def test_transcribe_language_normalised(tmp_path):
    mcp, calls = make(tmp_path)
    await call(mcp, "transcribe_youtube", {"url": VID, "language": " EN "})
    await call(mcp, "transcribe_youtube", {"url": VID, "language": ""})
    assert calls[0] == (VID, "en")


async def test_transcribe_bad_url_is_tool_error(tmp_path):
    mcp, calls = make(tmp_path)
    r = await call(mcp, "transcribe_youtube", {"url": "https://vimeo.com/1"})
    assert r.is_error is True
    assert "Not a YouTube video link: https://vimeo.com/1" in r.content[0].text
    assert calls == []


async def test_read_transcript_not_found_then_found(tmp_path):
    mcp, _ = make(tmp_path)
    r = await call(mcp, "read_transcript", {"video": VID})
    assert r.is_error is True and "transcribe_youtube" in r.content[0].text
    await call(mcp, "transcribe_youtube", {"url": VID})
    r = await call(mcp, "read_transcript", {"video": f"https://www.youtube.com/watch?v={VID}"})
    assert r.is_error is False
    assert r.content[0].text.startswith("# Birdhouse")
    assert "Page " not in r.content[0].text


async def test_read_transcript_pages(tmp_path):
    long_lines = [f">> paragraph {i} " + "word " * 3000 for i in range(60)]
    mcp, _ = make(tmp_path, long_lines)
    await call(mcp, "transcribe_youtube", {"url": VID})
    r1 = await call(mcp, "read_transcript", {"video": VID})
    t1 = r1.content[0].text
    assert t1.startswith("Page 1 of ")
    total = int(t1.split()[3])
    assert total >= 2
    assert all(len((await call(mcp, "read_transcript", {"video": VID, "page": p})).content[0].text) < 101_000
               for p in range(1, total + 1))
    last = (await call(mcp, "read_transcript", {"video": VID, "page": total})).content[0].text
    assert f"Page {total} of {total}" in last and "call again" not in last
    bad = await call(mcp, "read_transcript", {"video": VID, "page": total + 1})
    assert bad.is_error is True


async def test_list_transcripts(tmp_path):
    mcp, _ = make(tmp_path)
    r = await call(mcp, "list_transcripts", {})
    assert r.content[0].text == "No transcripts yet."
    await call(mcp, "transcribe_youtube", {"url": VID})
    text = (await call(mcp, "list_transcripts", {"search": "bird"})).content[0].text
    assert "Birdhouse — Shop Talk · 2:41:07 · 2026-10-09" in text
    assert f"https://t.example.com/{KEY}/t/{VID}.en.md" in text
    assert (await call(mcp, "list_transcripts", {"search": "nothing"})).content[0].text == "No transcripts match."


def test_split_pages():
    assert split_pages("a\n\nb", size=100) == ["a\n\nb"]
    pages = split_pages("aaaa\n\nbbbb\n\ncccc", size=10)
    assert pages == ["aaaa\n\nbbbb", "cccc"]
    assert split_pages("x" * 25, size=10) == ["x" * 10, "x" * 10, "x" * 5]
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_tools.py -q` → FAIL.

- [ ] **Step 3: Implement** `src/ytmcp/tools.py`:

```python
"""The three MCP tools Claude sees."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ytmcp.config import Settings
from ytmcp.errors import TranscriptError
from ytmcp.render import KIND_LABEL, format_clock
from ytmcp.service import Transcriber
from ytmcp.store import TranscriptRecord
from ytmcp.youtube import video_id_from_url

PAGE_SIZE = 100_000
PREVIEW_CHARS = 600
INSTRUCTIONS = (
    "Turns YouTube videos into full, timestamped Markdown transcripts. "
    "After transcribe_youtube, give the user the download link. Do not paste the "
    "transcript into the chat unless the user asks; use read_transcript when you need "
    "its text to answer a question about the video."
)


def split_pages(text: str, size: int = PAGE_SIZE) -> list[str]:
    """Pages of at most `size` chars, split on paragraph boundaries where possible."""
    pages: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        while len(block) > size:
            if current:
                pages.append(current)
                current = ""
            pages.append(block[:size])
            block = block[size:]
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > size:
            pages.append(current)
            current = block
        else:
            current = candidate
    if current or not pages:
        pages.append(current)
    return pages


def _language(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    return value or None


def _details(record: TranscriptRecord) -> str:
    parts = [p for p in (record.channel,
                         format_clock(record.duration_seconds) if record.duration_seconds else "",
                         f"published {record.published}" if record.published else "") if p]
    label = KIND_LABEL.get(record.track_kind, record.track_kind)
    parts.append(f"language: {record.language} ({label})")
    return " · ".join(parts)


def build_mcp(transcriber: Transcriber, settings: Settings) -> MCPServer:
    store = transcriber.store
    mcp = MCPServer("youtube-transcripts", instructions=INSTRUCTIONS)

    def link(record: TranscriptRecord) -> str:
        return f"{settings.base_url}/t/{record.file_name}"

    def video_id(value: str) -> str:
        found = video_id_from_url(value)
        if found is None:
            raise ToolError(str(TranscriptError("invalid_url", value.strip()[:200])))
        return found

    @mcp.tool()
    async def transcribe_youtube(url: str, language: str | None = None) -> str:
        """Make a full, timestamped Markdown transcript of a YouTube video and return a
        download link. `url` is any YouTube video link. `language` is optional (e.g. "en",
        "ru"); leave it empty for the video's original language."""
        vid = video_id(url)
        try:
            record, cached = await transcriber.transcribe(vid, _language(language))
        except TranscriptError as exc:
            raise ToolError(str(exc)) from None
        body = store.read_markdown(record).split("\n---\n", 1)[-1].strip()
        preview = body[:PREVIEW_CHARS] + ("…" if len(body) > PREVIEW_CHARS else "")
        title = f"Transcript ready: {record.title}" + (" (from history)" if cached else "")
        return (f"{title}\n{_details(record)}\nDownload: {link(record)}\n"
                f"Words: {record.words:,}\n\nPreview:\n{preview}")

    @mcp.tool()
    def read_transcript(video: str, page: int = 1) -> str:
        """Read the full text of a transcript made earlier, in pages of up to 100,000
        characters. `video` is a YouTube link or video id."""
        record = store.latest(video_id(video))
        if record is None:
            raise ToolError(str(TranscriptError("not_found")))
        pages = split_pages(store.read_markdown(record))
        if not 1 <= page <= len(pages):
            raise ToolError(f"page must be between 1 and {len(pages)}")
        if len(pages) == 1:
            return pages[0]
        more = f" — call again with page={page + 1} for the rest." if page < len(pages) else "."
        return f"Page {page} of {len(pages)}{more}\n\n{pages[page - 1]}"

    @mcp.tool()
    def list_transcripts(search: str | None = None, limit: int = 20) -> str:
        """List transcripts made before, newest first. `search` filters by title or channel."""
        records = store.list(search, max(1, min(limit, 100)))
        if not records:
            return "No transcripts match." if search and search.strip() else "No transcripts yet."
        lines = []
        for r in records:
            length = format_clock(r.duration_seconds) if r.duration_seconds else "?"
            who = f" — {r.channel}" if r.channel else ""
            lines.append(f"- {r.title}{who} · {length} · {r.created_at[:10]} · {link(r)}")
        return "\n".join(lines)

    return mcp
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_tools.py -q` → PASS.

- [ ] **Step 5: Write the failing app tests** — `tests/test_app.py`

```python
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from ytmcp.app import build_app
from ytmcp.config import Settings
from ytmcp.service import Transcriber
from ytmcp.store import Store
from ytmcp.web import download_name
from ytmcp.youtube import Fetched, Track

KEY = "Abc_def-" * 5
VID = "aB3_-xYz012"
HOST = "transcripts.example.com"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "Host": HOST}


def settings(tmp_path):
    return Settings(secret_key=KEY, public_url=f"https://{HOST}", data_dir=tmp_path)


def seeded(tmp_path, title="Birdhouse <b>&</b>", channel="Shop Talk"):
    s = settings(tmp_path)
    store = Store(tmp_path)
    store.save(fetched=Fetched(VID, title, channel, 600, "2026-10-01", Track("en-orig", "en", "auto", "u"), ""),
               markdown="# hi\n", is_original=True, now=datetime(2026, 10, 9, tzinfo=UTC))
    return s, Transcriber(store, s, runner=None)


def client(tmp_path, **kw):
    s, t = seeded(tmp_path, **kw)
    return TestClient(build_app(s, transcriber=t), base_url=f"https://{HOST}")


@pytest.mark.parametrize("path", ["/", "/mcp", f"/{KEY[:-1]}/", f"/{KEY}x/", f"/{KEY}x/mcp",
                                  "/style.css", f"/t/{VID}.en.md", f"/{KEY.upper()}/", "/favicon.ico"])
def test_wrong_or_missing_key_is_empty_404(tmp_path, path):
    with client(tmp_path) as c:
        r = c.get(path)
    assert r.status_code == 404 and r.content == b""
    assert r.headers["referrer-policy"] == "no-referrer"


def test_history_page(tmp_path):
    with client(tmp_path) as c:
        r = c.get(f"/{KEY}/")
    assert r.status_code == 200
    html = r.text
    assert "Birdhouse &lt;b&gt;&amp;&lt;/b&gt;" in html and "<b>&" not in html
    assert f'href="/{KEY}/t/{VID}.en.md"' in html
    assert f'href="https://www.youtube.com/watch?v={VID}"' in html
    assert 'rel="noreferrer noopener"' in html
    assert f'href="/{KEY}/style.css"' in html
    assert "<script" not in html
    assert "10:00" in html and "2026-10-09" in html
    h = r.headers
    assert h["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-robots-tag"] == "noindex, nofollow"
    assert h["cache-control"] == "no-store"


def test_history_search_and_empty(tmp_path):
    with client(tmp_path) as c:
        assert "Birdhouse" not in c.get(f"/{KEY}/", params={"q": "zzz"}).text
        assert "Birdhouse" in c.get(f"/{KEY}/", params={"q": "SHOP"}).text
        assert 'value="&lt;x&gt;"' in c.get(f"/{KEY}/", params={"q": "<x>"}).text
    s = settings(tmp_path / "empty")
    with TestClient(build_app(s), base_url=f"https://{HOST}") as c:
        assert "No transcripts yet" in c.get(f"/{KEY}/").text


def test_stylesheet(tmp_path):
    with client(tmp_path) as c:
        r = c.get(f"/{KEY}/style.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")


def test_download(tmp_path):
    with client(tmp_path, title="Обзор: Birdhouse / v2") as c:
        r = c.get(f"/{KEY}/t/{VID}.en.md")
    assert r.status_code == 200 and r.text == "# hi\n"
    assert r.headers["content-type"].startswith("text/markdown")
    cd = r.headers["content-disposition"]
    assert cd.startswith('attachment; filename="Birdhouse-v2.md"; filename*=UTF-8\'\'')
    assert "%D0%9E" in cd


@pytest.mark.parametrize("name", [f"{VID}.de.md", "..%2Ftranscripts.db", "../transcripts.db",
                                  f"{VID}.en.md.bak", "x.md"])
def test_download_rejects(tmp_path, name):
    with client(tmp_path) as c:
        assert c.get(f"/{KEY}/t/{name}").status_code == 404


def test_download_name():
    assert download_name("Hello, World!", VID) == "Hello-World.md"
    assert download_name("Обзор", VID) == f"{VID}.md"
    assert len(download_name("a" * 300, VID)) <= 83


def test_mcp_endpoint(tmp_path):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    with client(tmp_path) as c:
        r = c.post(f"/{KEY}/mcp", headers=MCP_HEADERS, json=init)
        assert r.status_code == 200 and "youtube-transcripts" in r.text
        r = c.post(f"/{KEY}/mcp", headers=MCP_HEADERS,
                   json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert "transcribe_youtube" in r.text
        bad = c.post(f"/{KEY}/mcp", headers={**MCP_HEADERS, "Host": "evil.example.org"}, json=init)
        assert bad.status_code == 421
        assert c.post("/mcp", headers=MCP_HEADERS, json=init).status_code == 404
```

- [ ] **Step 6: Run** `.venv/bin/python -m pytest tests/test_app.py -q` → FAIL.

- [ ] **Step 7: Implement** `src/ytmcp/web.py`:

```python
"""The read-only history page and the transcript downloads."""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from ytmcp.config import Settings
from ytmcp.render import format_clock, watch_url
from ytmcp.store import Store, TranscriptRecord

PAGE_LIMIT = 500

STYLE = """\
:root { --bg: #fafaf7; --fg: #1d1d1b; --muted: #6b6b66; --line: #e4e4dd; --accent: #c4302b; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --fg: #ecece6; --muted: #9a9a93; --line: #2c2c29; --accent: #ff6b5e; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 16px; }
form { display: flex; gap: 8px; margin-bottom: 8px; }
input[type=search] { flex: 1; min-width: 0; padding: 8px 10px; font: inherit; color: inherit;
       background: transparent; border: 1px solid var(--line); border-radius: 6px; }
button { padding: 8px 14px; font: inherit; border: 1px solid var(--line); border-radius: 6px;
         background: var(--fg); color: var(--bg); cursor: pointer; }
.count { color: var(--muted); font-size: 14px; margin: 0 0 16px; }
ul { list-style: none; margin: 0; padding: 0; }
li { padding: 14px 0; border-top: 1px solid var(--line); }
.title { font-weight: 600; overflow-wrap: anywhere; }
.meta { color: var(--muted); font-size: 14px; display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 2px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.empty { color: var(--muted); padding: 24px 0; }
"""


def download_name(title: str, video_id: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_title).strip("-")[:80].strip("-")
    return f"{slug or video_id}.md"


def _item(r: TranscriptRecord, prefix: str) -> str:
    e = html.escape
    meta = [e(r.channel)] if r.channel else []
    if r.duration_seconds:
        meta.append(format_clock(r.duration_seconds))
    meta.append(e(r.created_at[:10]))
    meta.append(f'<a href="{e(prefix)}/t/{e(r.file_name)}">⬇ .md</a>')
    meta.append(f'<a href="{e(watch_url(r.video_id))}" rel="noreferrer noopener" target="_blank">▶ YouTube</a>')
    spans = "".join(f"<span>{m}</span>" for m in meta)
    return f'<li><div class="title">{e(r.title)}</div><div class="meta">{spans}</div></li>'


def render_history(records: list[TranscriptRecord], q: str, prefix: str) -> str:
    e = html.escape
    if records:
        count = f"{len(records)} transcript{'s' if len(records) != 1 else ''}"
        body = f'<p class="count">{count}</p><ul>{"".join(_item(r, prefix) for r in records)}</ul>'
    elif q:
        body = '<p class="empty">No transcripts match.</p>'
    else:
        body = '<p class="empty">No transcripts yet — ask Claude to transcribe a YouTube link.</p>'
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer"><title>Transcripts</title>'
        f'<link rel="stylesheet" href="{e(prefix)}/style.css"></head><body><main>'
        '<h1>Transcripts</h1>'
        f'<form method="get" action="{e(prefix)}/"><input type="search" name="q" value="{e(q)}" '
        'placeholder="Search by title or channel" aria-label="Search"><button type="submit">Search</button></form>'
        f"{body}</main></body></html>"
    )


def web_routes(store: Store, settings: Settings) -> list[Route]:
    prefix = f"/{settings.secret_key}"

    async def history(request: Request) -> Response:
        q = request.query_params.get("q", "").strip()[:200]
        records = store.list(q or None, PAGE_LIMIT)
        return HTMLResponse(render_history(records, q, prefix), headers={"Cache-Control": "no-store"})

    async def stylesheet(request: Request) -> Response:
        return Response(STYLE, media_type="text/css")

    async def download(request: Request) -> Response:
        record = store.by_file_name(request.path_params["file_name"])
        if record is None:
            return Response(status_code=404)
        name = download_name(record.title, record.video_id)
        utf8_name = quote(f"{record.title.strip() or record.video_id}.md", safe="")
        return Response(
            store.read_markdown(record), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{utf8_name}",
                     "Cache-Control": "no-store"})

    return [Route("/", history), Route("/style.css", stylesheet),
            Route("/t/{file_name}", download)]
```


`src/ytmcp/app.py`:

```python
"""The whole service: a key gate in front of the history page, downloads and MCP."""

from __future__ import annotations

import contextlib
import hmac
from collections.abc import AsyncIterator

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from ytmcp.config import Settings
from ytmcp.service import Transcriber
from ytmcp.store import Store
from ytmcp.tools import build_mcp
from ytmcp.web import web_routes

SECURITY_HEADERS = [
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-robots-tag", b"noindex, nofollow"),
    (b"content-security-policy",
     b"default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
]


class KeyGate:
    """Every path must start with /<key>/ (or be exactly /<key>); anything else is an
    empty 404. Security headers go on every response, the 404s included."""

    def __init__(self, app: ASGIApp, key: str) -> None:
        self.app = app
        self.key = key.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                names = {name for name, _ in SECURITY_HEADERS}
                headers = [(n, v) for n, v in message.get("headers", []) if n.lower() not in names]
                message = {**message, "headers": headers + SECURITY_HEADERS}
            await send(message)

        segment = scope["path"].split("/", 2)[1] if scope["path"].startswith("/") else ""
        if not hmac.compare_digest(segment.encode(), self.key):
            await Response(status_code=404)(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)


def build_app(settings: Settings, *, transcriber: Transcriber | None = None) -> ASGIApp:
    transcriber = transcriber or Transcriber(Store(settings.data_dir), settings)
    mcp = build_mcp(transcriber, settings)
    host = settings.public_host
    sub = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, f"{host}:*", "127.0.0.1:*", "localhost:*"],
            allowed_origins=[settings.public_url, "https://claude.ai", "https://claude.com"],
        ),
    )
    sub.router.routes.extend(web_routes(transcriber.store, settings))
    session_manager = mcp.session_manager

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():  # a mounted app's own lifespan never runs
            yield

    inner = Starlette(routes=[Mount(f"/{settings.secret_key}", app=sub)], lifespan=lifespan)
    return KeyGate(inner, settings.secret_key)
```

Note for the implementer: `KeyGate` wraps a Starlette app, and Starlette's `TestClient` drives the lifespan through the wrapper (non-http scopes pass straight through). If the history route at `/<key>/` 404s or redirects because of the Mount/route combination, adjust routing (e.g. a `Route("/", …)` inside `sub` is reached as `/<key>/`) and keep the tests as written — they define the behaviour.

`src/ytmcp/__main__.py`:

```python
"""python -m ytmcp — run the server."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from ytmcp.app import build_app
from ytmcp.config import ConfigError, load_dotenv, load_settings


def main() -> None:
    load_dotenv(Path(".env"))
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        sys.exit(f"configuration error: {exc}")
    # access_log=False: every request path carries the secret key.
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port,
                access_log=False, proxy_headers=False, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run** `.venv/bin/python -m pytest -q` → all PASS.

- [ ] **Step 9: Smoke-run locally** — `YTM_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))") YTM_PUBLIC_URL=http://127.0.0.1:8090 YTM_DATA_DIR=/tmp/ytmcp-smoke timeout 5 .venv/bin/python -m ytmcp` → starts and logs "Uvicorn running on http://127.0.0.1:8090" (exit by timeout is fine); confirm no request path is logged.

- [ ] **Step 10: Commit** — `git add -A && git commit -m "feat: MCP tools, history page and the key-gated app"`

---

### Task 9: Health check, deploy files, README

**Files:**
- Create: `src/ytmcp/check.py`, `deploy/ytmcp.service`, `deploy/install-service.sh`, `deploy/update-ytdlp.sh`, `README.md`, `LICENSE` (MIT, copyright holder "the youtube-transcript-mcp authors")
- Test: `tests/test_check.py`

**Interfaces:**
- Consumes: `fetch_captions` (6), `parse_vtt` (3), `load_dotenv` (1), `TranscriptError` (1).
- Produces: `check.run_check(video_id: str, cookies: str | None, *, fetch=fetch_captions) -> tuple[bool, str]`; `python -m ytmcp.check [video_id]` exit 0/1. Default video id from env `YTM_CHECK_VIDEO`, else `dQw4w9WgXcQ`.

- [ ] **Step 1: Write the failing test** — `tests/test_check.py`

```python
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
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `src/ytmcp/check.py`:

```python
"""python -m ytmcp.check [video_id] — one live fetch, for the weekly yt-dlp upgrade.

Uses a temporary copy of the cookies so it never races the running service,
which owns the real file."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from ytmcp.captions import parse_vtt
from ytmcp.config import load_dotenv
from ytmcp.errors import TranscriptError
from ytmcp.youtube import fetch_captions

DEFAULT_VIDEO = "dQw4w9WgXcQ"
MIN_LINES = 10


def run_check(video_id: str, cookies: str | None, *, fetch=fetch_captions) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        cookie_copy = None
        if cookies and Path(cookies).is_file():
            cookie_copy = str(Path(tmp) / "cookies.txt")
            shutil.copyfile(cookies, cookie_copy)
        try:
            fetched = fetch(video_id, None, cookiefile=cookie_copy)
        except TranscriptError as exc:
            return False, f"check failed for {video_id}: {exc}"
        lines = len(parse_vtt(fetched.vtt))
    if lines < MIN_LINES:
        return False, f"check failed for {video_id}: only {lines} caption lines"
    return True, f"ok: {video_id} «{fetched.title}» — {lines} caption lines ({fetched.track.code})"


def main() -> None:
    load_dotenv(Path(".env"))
    video_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("YTM_CHECK_VIDEO", DEFAULT_VIDEO)
    ok, message = run_check(video_id, os.environ.get("YTM_COOKIES") or None)
    print(message)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` → all PASS.

- [ ] **Step 5: Deploy files.**

`deploy/ytmcp.service`:
```ini
[Unit]
Description=YouTube transcript MCP server
After=network-online.target
Wants=network-online.target

[Service]
User=__USER__
WorkingDirectory=__DIR__
EnvironmentFile=__DIR__/.env
# deno solves YouTube's player challenge for yt-dlp; the venv runs the worker.
Environment=PATH=__HOME__/.deno/bin:__DIR__/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=__DIR__/.venv/bin/python -m ytmcp
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
MemoryMax=700M

[Install]
WantedBy=multi-user.target
```

`deploy/install-service.sh`:
```bash
#!/usr/bin/env bash
# Install and start the systemd unit. Run once as root:
#   sudo deploy/install-service.sh <user> [<repo dir>]
set -euo pipefail
USER_NAME="${1:?usage: install-service.sh <user> [<repo dir>]}"
DIR="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[ -n "$HOME_DIR" ] || { echo "no such user: $USER_NAME" >&2; exit 1; }
[ -f "$DIR/.env" ] || { echo "missing $DIR/.env (copy .env.example and fill it in)" >&2; exit 1; }
sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" -e "s|__HOME__|$HOME_DIR|g" \
  "$DIR/deploy/ytmcp.service" > /etc/systemd/system/ytmcp.service
systemctl daemon-reload
systemctl enable --now ytmcp.service
systemctl --no-pager status ytmcp.service | head -5
```

`deploy/update-ytdlp.sh`:
```bash
#!/usr/bin/env bash
# Weekly: upgrade yt-dlp in the venv, check one live fetch, roll back if it fails.
# Run as the service user from cron, e.g.  17 2 * * 1  /path/to/repo/deploy/update-ytdlp.sh
# No restart is needed: every fetch runs in a fresh worker process.
set -uo pipefail
export PATH="$HOME/.deno/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
set -a; . "$REPO/.env" 2>/dev/null || true; set +a

version() { .venv/bin/python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo unknown; }

OLD="$(version)"
.venv/bin/pip install -q -U yt-dlp || echo "update-ytdlp: pip install failed; staying at $OLD" >&2
NEW="$(version)"
echo "update-ytdlp: yt-dlp $OLD -> $NEW"

if .venv/bin/python -m ytmcp.check; then exit 0; fi
if [ "$NEW" = "$OLD" ] || [ "$OLD" = "unknown" ]; then
  echo "update-ytdlp: check failed on $NEW; nothing to roll back to" >&2
  exit 1
fi
echo "update-ytdlp: check failed on $NEW; rolling back to $OLD" >&2
.venv/bin/pip install -q "yt-dlp==$OLD"
.venv/bin/python -m ytmcp.check
```

`chmod +x deploy/*.sh`.

- [ ] **Step 6: README.md** — sections, generic (no real hostnames; use `transcripts.example.com`):
  1. What it is (one paragraph) and what the three tools do.
  2. Requirements: Python 3.12+, deno (`curl -fsSL https://deno.land/install.sh | sh`), a public HTTPS hostname (reverse proxy or tunnel).
  3. Run locally: venv, `pip install -e ".[dev]"`, `cp .env.example .env`, generate a key, `python -m ytmcp`, open `http://127.0.0.1:8090/<key>/`.
  4. Deploy on a Linux server: clone, venv, `.env` (`chmod 600`), cookies (why: datacenter IPs; export from a signed-in browser as Netscape `cookies.txt`, give this service its own copy, `chmod 600`, they expire — replace when the tool says "sign in"), `sudo deploy/install-service.sh <user>`, expose `127.0.0.1:8090` (Cloudflare Tunnel example: add a public hostname → `http://localhost:8090`), weekly `deploy/update-ytdlp.sh` cron line.
  5. Connect in Claude: Settings → Connectors → Add custom connector → URL `https://transcripts.example.com/<key>/mcp`, no sign-in. Example prompts.
  6. Security model: the key is the password — anyone with the URL can use the server and see the history; rotate by changing `YTM_SECRET_KEY`, restarting, and re-adding the connector. Access log is off.
  7. Development: `pytest -q`; tests never touch the network.
  8. Limits: captions only (no speech-to-text), one video per call, one fetch at a time, 200 s per fetch.

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: health check, deploy scripts and README"`

---

## After the tasks (controller, not a subagent task)

1. Whole-branch review against the spec (fresh reviewer).
2. Live check on the laptop: `.venv/bin/python -m ytmcp.check` (needs deno locally; if missing, run the live check on the server instead) and one real `transcribe_youtube` via the in-memory client or MCP Inspector.
3. Create the public GitHub repo and push (no secrets, no organisation details).
4. Deploy to the server (owner runs the root step; owner adds the tunnel hostname), then add the connector in Claude desktop and transcribe one video end to end.
