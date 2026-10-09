# YouTube Transcript MCP — design

Date: 2026-10-09 · Status: approved in conversation, awaiting spec review

## Goal

A small self-hosted service that turns a YouTube link into a full,
timestamped Markdown transcript. People use it from the Claude desktop app
(or claude.ai) as a **custom connector** (a remote MCP server): they paste a
link, Claude calls the server, and they get back a download link to the `.md`
file. A read-only history page lists every transcript made so far.

The repository is public and generic. It contains no organisation names,
hostnames, keys, or server details; every deployment-specific value lives in
the server's `.env`.

### Success criteria

1. In Claude desktop, "transcribe <YouTube link>" returns, within 240 s, a
   link that downloads the full transcript as `.md`, word for word as
   YouTube's captions have it, for a video of any length.
2. A link transcribed before comes back instantly, without contacting YouTube.
3. `https://<host>/<key>/` shows every transcript, newest first, searchable.
4. Without the key every URL is a plain 404.
5. The test suite runs with no network access.

### Non-goals (YAGNI)

- Speech-to-text for videos without captions (no audio download, no Whisper).
- Playlists or channels — one video per call.
- Per-user accounts, OAuth, multi-tenant history.
- Transcribing from the web page (the page is read-only).
- Deleting transcripts from the UI.
- Summaries or any model calls on the server.

## Constraints that shaped the design

From Anthropic's connector docs (claude.com/docs/connectors, checked 2026-10-09):

- Claude reaches connectors **from Anthropic's cloud**, so the server must be
  publicly reachable over HTTPS. Transport: **Streamable HTTP**.
- Auth options are OAuth (DCR/CIMD), static headers (beta, limited orgs), or
  **none**. We use *none* plus a secret in the URL path (see Security).
- A tool result in Claude desktop / claude.ai is capped at about
  **150,000 characters**; a tool call times out after **240 s**.
- There is no documented way for a tool to hand the user a file, so the
  server hosts the file and returns a link.

From YouTube practice (proven in production elsewhere):

- Datacenter IPs need a logged-in `cookies.txt`; yt-dlp writes the cookie
  jar back to that file when it closes.
- The `web_embedded` player client avoids the PO-token requirement, and the
  `ejs:github` remote component solves the player JS challenge, which needs
  **deno** on `PATH`.
- Asking for many subtitle languages in one call triggers HTTP 429; ask for
  exactly one track.
- yt-dlp must be upgraded regularly or YouTube changes break it.

## Architecture

One Python 3.12 ASGI app (Starlette + uvicorn), bound to loopback, put on
the internet by a reverse proxy or tunnel (e.g. Cloudflare Tunnel). It serves:

| Path | What |
|---|---|
| `/<key>/mcp` | the MCP server (Streamable HTTP, stateless, JSON responses) |
| `/<key>/` | the history page (HTML, no JavaScript) |
| `/<key>/t/<file>.md` | a transcript file (download) |
| `/<key>/style.css` | the page's stylesheet |
| anything else | `404 Not Found`, empty body |

### Units

Package `ytmcp` under `src/`:

- `config.py` — `Settings` from environment / `.env`. Fails fast at startup
  on a missing or short key.
- `youtube.py` — URL handling and the yt-dlp boundary:
  `video_id_from_url(url) -> str | None` (accepts `watch?v=`, `youtu.be/`,
  `shorts/`, `embed/`, `live/`, `m.` and `music.` hosts; id must match
  `[A-Za-z0-9_-]{11}`), `probe(video_id, cookiefile) -> VideoInfo` (metadata
  plus the available caption tracks, `download=False`),
  `choose_track(info, language) -> Track`, `download_track(video_id, track,
  cookiefile) -> str` (VTT text), and the error classes. The yt-dlp options
  are the proven set: `skip_download`, `subtitlesformat: vtt`, one language,
  `extractor_args: {youtube: {player_client: [web_embedded]}}`,
  `remote_components: [ejs:github]`, `cookiefile` when set, `quiet`.
- `captions.py` — `parse_vtt(vtt) -> list[Cue]` (`Cue = (start_seconds,
  text)`): strips tags and HTML entities, skips headers/indices, and removes
  the rolling repeats of auto-captions (a line equal to the last kept line is
  dropped). Pure functions, no I/O.
- `render.py` — `to_markdown(info, track, cues) -> str` and
  `paragraphs(cues) -> list[Paragraph]`. Pure.
- `worker.py` — the fetch run as a **child process**
  (`python -m ytmcp.worker <video_id> <language|->`), printing one JSON
  object (`{"ok": …}` or `{"error": kind, "message": …}`) on stdout. A
  child process can be killed on timeout; a thread cannot.
- `store.py` — SQLite (`transcripts` table) plus the `.md` files on disk.
- `service.py` — `Transcriber`: cache lookup → single-flight lock →
  worker with deadline → render → store. The only place that orchestrates.
- `tools.py` — the three MCP tools, thin wrappers over `Transcriber` and
  `Store` that format text for Claude.
- `web.py` — the history page and file download routes.
- `app.py` — builds the Starlette app: key gate, MCP mount, web routes,
  security headers. `__main__.py` runs uvicorn.

Each unit is testable alone; only `youtube.py` and `worker.py` touch the
network, and both take injectable fakes in tests.

## MCP tools

All return Markdown text. Tool descriptions tell Claude to give the user
the download link and not to paste the transcript unless asked.

### `transcribe_youtube(url: str, language: str | None = None)`

1. `video_id_from_url(url)`; not a single-video YouTube link → error
   "Not a YouTube video link: …".
2. Cache: with no `language`, any stored transcript of this video in its
   original track; with a `language`, one stored in that language. Hit →
   return it at once (`cached: yes`).
3. Otherwise transcribe (see Data flow) and return:

```
Transcript ready: <title>
<channel> · 2:41:07 · published 2026-10-01 · language: en (auto-generated)
Download: https://<host>/<key>/t/<video_id>.en.md
Words: 28,431

Preview:
[0:00] First ~600 characters of the transcript…
```

### `read_transcript(video: str, page: int = 1)`

`video` is a URL or a video id; it reads the newest stored transcript of that
video (any language). Returns the `.md` body in pages of **100,000
characters**, split on paragraph boundaries, with a header line
`Page 1 of 3 — call again with page=2 for the rest.` Not transcribed yet →
error telling Claude to call `transcribe_youtube` first.

### `list_transcripts(search: str | None = None, limit: int = 20)`

The newest `limit` (max 100) transcripts, optionally filtered by a
case-insensitive substring of title or channel: one line each with title,
channel, length, date transcribed, and the download link.

## Language choice

`probe` returns `subtitles` (uploaded by the creator) and
`automatic_captions` (YouTube's ASR and its machine translations), plus the
video's `language` field when YouTube knows it.

With **no language requested** (the default — the original language):

1. A creator-uploaded track in the video's `language` (exact code or same
   base, e.g. `en-US` for `en`).
2. The auto-caption track whose code ends in `-orig` (YouTube's ASR in the
   spoken language).
3. The auto-caption track in the video's `language`.
4. If the video's `language` is unknown: the only creator track if there is
   exactly one; else the `-orig` track; else fail with "no captions".

With **a language requested** (e.g. `en`, `ru`): a creator track in that
language, else the auto-caption track in that language (YouTube's machine
translation), else fail listing the available languages.

The stored and displayed language is the track's base code (`en`, `ru`, …),
with "auto-generated" or "translated" noted in the file header.

## Data flow (cache miss)

```
tool call ─▶ Transcriber.transcribe(video_id, language)
   │  acquire the single-flight lock (wait ≤ 25 s, else "busy, try again")
   │  spawn worker: probe → choose_track → download_track  (deadline 60 s)
   │      worker exits non-zero / times out → kill, map to a clear error
   │  parse_vtt → paragraphs → to_markdown
   │  store: write <video_id>.<lang>.md atomically, upsert the row
   └─▶ release lock, return summary
```

One fetch at a time across the whole process (the server shares a small
machine; a second request waits for the lock). The deadline of 60 s plus a
25 s wait for the lock stays under the ~100 s after which Cloudflare's edge answers 524 when the origin sends
nothing (JSON responses send nothing until the tool finishes); `load_settings` enforces a total of at most 95 s.

## The `.md` file

```markdown
# <title>

- **Channel:** <channel>
- **Video:** https://www.youtube.com/watch?v=<id>
- **Length:** 2:41:07
- **Published:** 2026-10-01
- **Language:** en (auto-generated captions)
- **Transcribed:** 2026-10-09

---

[0:00](https://www.youtube.com/watch?v=<id>&t=0s) Text of the first paragraph …

[0:47](https://www.youtube.com/watch?v=<id>&t=47s) Next paragraph …
```

Paragraph rules (`render.paragraphs`):

- A caption line beginning `>>` (YouTube's speaker-change marker) always
  starts a new paragraph; the `>>` itself is dropped.
- Otherwise a paragraph closes at the first cue boundary after **45 s**
  where the text so far ends a sentence (`.`, `?`, `!`), and is forced closed
  at **90 s** regardless.
- Lines are joined with single spaces; whitespace is collapsed. No other
  change to the words.
- Each paragraph starts with `[m:ss]` (or `[h:mm:ss]` past an hour) linking
  to `&t=<floor(start)>s`.

Lengths ≥ 1 h render `h:mm:ss`; shorter `m:ss`. Unknown publish date or
length → the line is omitted. Title and channel are escaped for Markdown
where they would break the header (a leading `#`, newlines collapsed).

## Storage

`data/` (configurable):

- `data/transcripts.db` — table `transcripts`: `id` PK, `video_id`,
  `language` (base code), `is_original` (bool: chosen with no language
  requested), `track_kind` (`manual` / `auto` / `translated`), `title`,
  `channel`, `duration_seconds`, `published` (`YYYY-MM-DD` or NULL),
  `file_name`, `chars`, `words`, `created_at` (UTC ISO). Unique
  `(video_id, language)`; re-transcribing replaces the row and the file.
- `data/transcripts/<video_id>.<lang>.md` — written to a temp file in the
  same directory, then `os.replace`.
- Kept forever (about 150 KB per 2-hour episode).

## Web

**History page** `GET /<key>/?q=…` — server-rendered HTML, no script. A
search form (GET, `q`), then the transcripts newest first (up to 500;
filtered by a case-insensitive substring of title or channel): title, channel,
length, date transcribed, `⬇ .md`, `▶ YouTube`. Empty state: "No transcripts
yet — ask Claude to transcribe a YouTube link." Every string is HTML-escaped.

**File** `GET /<key>/t/<file>` — `file` must match
`^[A-Za-z0-9_-]{11}\.[a-z]{2,3}(-[A-Za-z0-9]+)?\.md$` and exist; served as
`text/markdown; charset=utf-8` with `Content-Disposition: attachment;
filename="<slugified title>.md"` (ASCII fallback plus `filename*=UTF-8''…`).

## Security

- **Secret path key.** `YTM_SECRET_KEY` (required, ≥ 32 URL-safe chars,
  generated with `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
  The first path segment is compared with `hmac.compare_digest`; a mismatch
  or a missing segment is `404` with an empty body — the server does not
  reveal that it exists. Rotating the key = edit `.env`, restart, re-add the
  connector.
- **The key must not leak through `Referer`.** Every response sets
  `Referrer-Policy: no-referrer`, and outbound links carry
  `rel="noreferrer noopener"`.
- Headers on every response: `Content-Security-Policy: default-src 'none';
  style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors
  'none'`, `X-Content-Type-Options: nosniff`, `X-Robots-Tag: noindex,
  nofollow`, `Cache-Control: no-store` on HTML.
- **MCP host checks.** The SDK's DNS-rebinding protection checks the `Host`
  header; it must allow the public hostname (`YTM_PUBLIC_URL`'s host) or every
  connector call is rejected.
- Only YouTube video ids reach yt-dlp — never a raw user URL — so the server
  cannot be used to fetch arbitrary URLs.
- The cookies file is a credential: `chmod 600`, never logged, never in the
  repo. Use a copy **owned by this service alone** (yt-dlp rewrites it on
  every fetch; two programs sharing one file race). Fetches are serialised,
  so the service's own writes never race.
- Logs record video ids and outcomes, never the key or cookie contents.
  Uvicorn's access log is disabled (it would log the key in every path).

## Errors

The worker maps failures to kinds; the tool returns a one-line message:

| Kind | Detected by | Message |
|---|---|---|
| `invalid_url` | `video_id_from_url` is None | Not a YouTube video link: … |
| `login_required` | "not a bot" in the yt-dlp error | YouTube asked the server to sign in — its cookies have expired. The operator needs to replace them. |
| `unavailable` | "private video", "video unavailable", "confirm your age", "members-only" | This video is private, removed, age-restricted, or members-only. |
| `no_captions` | no usable track / empty parse | This video has no captions (in that language: available …). |
| `timeout` | worker past the deadline (killed) | YouTube took too long. Try again in a minute. |
| `busy` | lock not acquired in 25 s | Another transcript is being made. Try again in a minute. |
| `failed` | anything else | Could not fetch the transcript: <first 300 chars of error>. |

Errors are returned as MCP tool errors (`isError`), never as exceptions that
kill the session. Nothing is stored on failure.

## Configuration

`.env` (documented in `.env.example`, which must list every knob):

| Variable | Default | Meaning |
|---|---|---|
| `YTM_SECRET_KEY` | — (required) | the path key |
| `YTM_PUBLIC_URL` | — (required) | e.g. `https://transcripts.example.com`; used to build links and the allowed `Host` |
| `YTM_HOST` / `YTM_PORT` | `127.0.0.1` / `8090` | bind address |
| `YTM_DATA_DIR` | `./data` | database and files |
| `YTM_COOKIES` | empty | path to this service's own `cookies.txt` |
| `YTM_FETCH_TIMEOUT` | `60` | worker deadline, seconds |
| `YTM_LOCK_WAIT` | `25` | seconds a second request waits for the lock; timeout + wait ≤ 95 (proxy limit ~100 s) |

## Deployment (generic, in `deploy/` and the README)

- Ubuntu with Python 3.12 and deno (`~/.deno/bin` on the service `PATH`).
- `deploy/ytmcp.service` — a systemd unit template (user, working dir,
  `EnvironmentFile`, `Restart=on-failure`, `PATH` including deno).
- `deploy/install-service.sh` — run once as root: installs the unit with the
  given user and directory, enables and starts it.
- `deploy/update-ytdlp.sh` — weekly cron: upgrade yt-dlp in the venv, run
  `python -m ytmcp.check` (one real fetch of a known captioned video), and
  roll back to the previous version if the check fails; restart the service.
- Expose `127.0.0.1:8090` with any HTTPS reverse proxy or tunnel; the README
  shows Cloudflare Tunnel (a public hostname → `http://localhost:8090`).
- Add the connector in Claude: Settings → Connectors → Add custom connector →
  URL `https://<host>/<key>/mcp`, authentication "No sign-in".

## Testing

pytest, **no network**: every yt-dlp call goes through an injectable
boundary.

- `test_youtube.py` — URL forms → id (and rejections: playlists, channels,
  other hosts, bad ids); `choose_track` over recorded `info` dicts (manual vs
  `-orig` vs unknown language, a requested language, missing language);
  error-string classification.
- `test_captions.py` — real auto-caption and manual VTT fixtures (short,
  committed under `tests/fixtures/`, a few minutes each): rolling repeats
  removed, tags/entities stripped, timings kept.
- `test_render.py` — paragraph rules (speaker marks, 45 s sentence break,
  90 s force), timestamp formats, header with missing fields, Markdown
  escaping of titles.
- `test_store.py` — upsert replaces, cache lookups (original vs language),
  search, atomic file write.
- `test_service.py` — cache hit skips the worker; lock busy → `busy`;
  worker timeout → killed and `timeout`; failure stores nothing.
- `test_app.py` — Starlette `TestClient`: wrong/missing key → 404 on every
  route; page lists and filters, escapes HTML; file route rejects bad names
  and path traversal, sets the download headers; security headers present.
- `test_tools.py` — the three tools through an in-memory MCP client session:
  output shape, paging of `read_transcript` under 100k chars per page, error
  results flagged `isError`.

A manual end-to-end check before deploy: run locally, add as a connector
through a temporary tunnel or test with the MCP Inspector, transcribe one
real video.
