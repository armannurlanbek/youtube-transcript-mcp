"""Settings from the environment (and an optional .env file)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_KEY = re.compile(r"[A-Za-z0-9_-]{32,}")


# Cloudflare's edge answers 524 when the origin sends nothing for ~100 s, and a
# tool result sends nothing until it is finished: the worst wait must stay below.
MAX_TOTAL_WAIT = 95.0


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
    fetch_timeout: float = 60.0
    lock_wait: float = 25.0

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc

    @property
    def base_url(self) -> str:
        return f"{self.public_url}/{self.secret_key}"


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        raise ConfigError(f"{name} must be a number of seconds") from None
    if not value > 0:
        raise ConfigError(f"{name} must be greater than 0")
    return value


def load_settings(env: Mapping[str, str]) -> Settings:
    key = env.get("YTM_SECRET_KEY", "").strip()
    if not _KEY.fullmatch(key):
        raise ConfigError("YTM_SECRET_KEY must be at least 32 characters of A-Z a-z 0-9 _ -")
    public_url = env.get("YTM_PUBLIC_URL", "").strip().rstrip("/")
    parsed = urlparse(public_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError("YTM_PUBLIC_URL must be an http(s) URL, e.g. https://transcripts.example.com")
    if parsed.path or parsed.query or parsed.fragment or parsed.params:
        raise ConfigError("YTM_PUBLIC_URL must be only scheme and host (no path, query or fragment)")
    fetch_timeout = _seconds(env, "YTM_FETCH_TIMEOUT", 60.0)
    lock_wait = _seconds(env, "YTM_LOCK_WAIT", 25.0)
    if fetch_timeout + lock_wait > MAX_TOTAL_WAIT:
        raise ConfigError(f"YTM_FETCH_TIMEOUT + YTM_LOCK_WAIT must be at most {MAX_TOTAL_WAIT:g} seconds "
                          "(proxies such as Cloudflare drop a silent request after about 100)")
    return Settings(
        secret_key=key,
        public_url=public_url,
        host=env.get("YTM_HOST", "").strip() or "127.0.0.1",
        port=int(env.get("YTM_PORT", "").strip() or 8090),
        data_dir=Path(env.get("YTM_DATA_DIR", "").strip() or "data"),
        cookies=env.get("YTM_COOKIES", "").strip() or None,
        fetch_timeout=fetch_timeout,
        lock_wait=lock_wait,
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
