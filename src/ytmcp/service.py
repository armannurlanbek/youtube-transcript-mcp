"""Turning a video id into a stored transcript: cache, one fetch at a time, a deadline."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ytmcp.captions import parse_vtt
from ytmcp.config import Settings
from ytmcp.errors import TranscriptError
from ytmcp.render import to_markdown
from ytmcp.store import Store, TranscriptRecord
from ytmcp.youtube import Fetched

log = logging.getLogger("ytmcp")

Runner = Callable[[str, str | None], Awaitable[dict]]


async def subprocess_runner(video_id: str, language: str | None, *, timeout: float,
                            cookies: str | None, command: list[str] | None = None) -> dict:
    """Run the worker as a child process; kill it if it passes the deadline."""
    argv = (command or [sys.executable, "-m", "ytmcp.worker"]) + [video_id, language or "-"]
    # The worker needs no secret of its own; the key stays in the server process.
    env = {**{k: v for k, v in os.environ.items() if k != "YTM_SECRET_KEY"}, "YTM_COOKIES": cookies or ""}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            start_new_session=True)
    except OSError as exc:
        raise TranscriptError("failed", str(exc)) from None
    try:
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            raise TranscriptError("timeout") from None
        try:
            parsed = json.loads(out.decode("utf-8", errors="replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            parsed = None
        if not isinstance(parsed, dict):
            tail = err.decode("utf-8", errors="replace").strip()[-300:]
            if cookies:
                for form in sorted({cookies, repr(cookies)[1:-1]}, key=len, reverse=True):
                    tail = tail.replace(form, "<cookies>")
            log.warning("worker stderr (exit code %s): %s", proc.returncode, tail)
            raise TranscriptError("failed", f"worker exited with code {proc.returncode}")
        return parsed
    finally:
        if proc.returncode is None:
            _kill_group(proc)
            await asyncio.shield(proc.wait())


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the child and any helpers it started (it leads its own process group)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


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
        """One INFO log line per call: video, language, outcome, seconds (no key, URL or text)."""
        started = time.monotonic()
        result = "error:unexpected"
        try:
            record, cached = await self._transcribe(video_id, language)
            result = "cached" if cached else "fetched"
            return record, cached
        except TranscriptError as exc:
            result = f"error:{exc.kind}"
            raise
        finally:
            log.info("video=%s lang=%s result=%s seconds=%.1f", video_id, language or "original",
                     result, time.monotonic() - started)

    async def _transcribe(self, video_id: str, language: str | None) -> tuple[TranscriptRecord, bool]:
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
            try:
                fetched = Fetched.from_json(result)
            except (KeyError, TypeError, ValueError):
                raise TranscriptError("failed", "malformed worker output") from None
            now = self._now()
            record = await asyncio.to_thread(self._build, fetched, language, now)
            return record, False
        finally:
            self._lock.release()

    def _build(self, fetched: Fetched, language: str | None, now: datetime) -> TranscriptRecord:
        """CPU and disk work, run in a thread so the event loop keeps answering."""
        cues = parse_vtt(fetched.vtt)
        if not cues:
            raise TranscriptError("no_captions")
        markdown = to_markdown(fetched, cues, transcribed=now.date())
        return self.store.save(fetched=fetched, markdown=markdown,
                               is_original=language is None, now=now)
