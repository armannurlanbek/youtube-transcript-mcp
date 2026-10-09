from datetime import UTC, datetime

import pytest
from mcp import Client

from ytmcp.config import Settings
from ytmcp.service import Transcriber
from ytmcp.store import Store
from ytmcp.tools import INSTRUCTIONS, build_mcp, split_pages
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


def test_instructions_warn_about_untrusted_content():
    assert "untrusted" in INSTRUCTIONS


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
    for p in range(1, total + 1):
        page_text = (await call(mcp, "read_transcript", {"video": VID, "page": p})).content[0].text
        assert len(page_text) < 101_000
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
