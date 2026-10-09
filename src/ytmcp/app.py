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
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await receive()  # the connect message
                await send({"type": "websocket.close", "code": 1008})
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
        if scope["path"] == f"/{self.key.decode()}":  # serve it as /<key>/, never redirect
            scope = {**scope, "path": scope["path"] + "/", "raw_path": scope.get("raw_path", b"") + b"/"}
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
    # A redirect would carry the key in a Location header (http:// behind a TLS tunnel).
    inner.router.redirect_slashes = False
    sub.router.redirect_slashes = False
    return KeyGate(inner, settings.secret_key)
