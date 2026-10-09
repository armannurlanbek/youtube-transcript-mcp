"""Turning a video id into a stored transcript: cache, one fetch at a time, a deadline."""

from __future__ import annotations

import asyncio
import json
import os
import signal
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
            raise TranscriptError("failed", tail or f"worker exited with code {proc.returncode}")
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
