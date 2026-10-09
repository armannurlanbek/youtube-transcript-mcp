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
