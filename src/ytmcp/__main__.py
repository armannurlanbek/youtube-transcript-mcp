"""python -m ytmcp — run the server."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn

from ytmcp.app import build_app
from ytmcp.config import ConfigError, load_dotenv, load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
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
