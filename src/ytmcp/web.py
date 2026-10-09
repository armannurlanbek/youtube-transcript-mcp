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

    def history(request: Request) -> Response:
        q = request.query_params.get("q", "").strip()[:200]
        records = store.list(q or None, PAGE_LIMIT)
        return HTMLResponse(render_history(records, q, prefix), headers={"Cache-Control": "no-store"})

    async def stylesheet(request: Request) -> Response:
        return Response(STYLE, media_type="text/css")

    def download(request: Request) -> Response:
        record = store.by_file_name(request.path_params["file_name"])
        if record is None:
            return Response(status_code=404)
        try:
            text = store.read_markdown(record)
        except FileNotFoundError:  # deleted between the lookup and the read
            return Response(status_code=404)
        name = download_name(record.title, record.video_id)
        title = " ".join(record.title.split())[:200] or record.video_id
        utf8_name = quote(f"{title}.md", safe="", errors="replace")
        return Response(
            text, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{utf8_name}",
                     "Cache-Control": "no-store"})

    return [Route("/", history), Route("/style.css", stylesheet),
            Route("/t/{file_name}", download)]
