# youtube-transcript-mcp

A small, self-hosted remote [MCP](https://modelcontextprotocol.io) server that turns YouTube links into full,
timestamped Markdown transcripts. You run it on your own server, add it to Claude as a custom connector, and
paste a YouTube link into a chat. Claude gets a download link and can read the transcript when it needs to.
Every transcript is kept forever and browsable on a private history page.

It reads the video's own captions (manual ones first, then automatic). It does not do speech-to-text.

Three tools are exposed to Claude:

- `transcribe_youtube(url, language?)` makes the transcript, saves it as a `.md` file and returns a download
  link, the details (channel, length, language, word count) and a short preview. `language` is optional
  (for example `en`, `ru`); by default the video's original language is used. A video already done is served
  from history.
- `read_transcript(video, page?)` returns the text of a transcript made earlier (`video` is a link or an id),
  in pages of 100,000 characters.
- `list_transcripts(search?, limit?)` lists past transcripts, newest first, filtered by title or channel.

## Requirements

- Linux or macOS (the service is POSIX-only), Python 3.12 or newer.
- [deno](https://deno.com), which yt-dlp uses to solve YouTube's player challenge:
  `curl -fsSL https://deno.land/install.sh | sh`
- A public HTTPS hostname that reaches the server (a reverse proxy or a tunnel). Claude's custom connectors
  need a public URL.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into YTM_SECRET_KEY in .env
.venv/bin/python -m ytmcp
```

Then open `http://127.0.0.1:8090/<key>/` for the history page. Set `YTM_PUBLIC_URL` in `.env` to the URL the
outside world will use (for local-only testing, `http://127.0.0.1:8090`).

All settings are environment variables (or lines in `.env`); `.env.example` documents each one.

## Deploy on a Linux server

1. Clone the repository on the server and create the virtualenv:
   `python3 -m venv .venv && .venv/bin/pip install -e .`
2. Create `.env` from `.env.example`: set `YTM_SECRET_KEY` and `YTM_PUBLIC_URL=https://transcripts.example.com`,
   then `chmod 600 .env`.
3. Cookies. YouTube blocks most datacenter IP addresses unless the request carries a signed-in session. Export
   your YouTube cookies from a signed-in browser in the Netscape `cookies.txt` format, put a copy used by this
   service alone on the server (for example `/home/<user>/ytmcp-cookies.txt`), `chmod 600` it and point
   `YTM_COOKIES` at it. yt-dlp rewrites this file, so do not share it with anything else. Cookies expire; when a
   tool reports that YouTube asks to sign in, export fresh ones and replace the file.
4. Install the systemd unit (once, as root): `sudo deploy/install-service.sh <user>`. It fills in the user and
   repository path, enables the service and starts it.
5. Expose `127.0.0.1:8090` on your hostname. With a Cloudflare Tunnel, add a public hostname
   `transcripts.example.com` that points to `http://localhost:8090`. A reverse proxy such as nginx or Caddy
   works the same way; proxy everything to port 8090.
6. Keep yt-dlp current, because YouTube changes often. Add a weekly cron line for the service user:
   `17 2 * * 1 /path/to/repo/deploy/update-ytdlp.sh`. It upgrades yt-dlp, runs one live fetch
   (`python -m ytmcp.check`) and rolls back to the previous version if the check fails. No restart is needed:
   every fetch runs in a fresh process. You can run the check yourself at any time with
   `.venv/bin/python -m ytmcp.check [video_id]` (exit status 0 means it works; `YTM_CHECK_VIDEO` sets the
   default video).

## Connect in Claude

In Claude, go to Settings, then Connectors, then Add custom connector, and enter:

```
https://transcripts.example.com/<key>/mcp
```

No sign-in is needed; the key in the URL is the credential. Then try:

- "Transcribe https://www.youtube.com/watch?v=... and give me the download link."
- "Summarise the key arguments in that video." (Claude reads it with `read_transcript`.)
- "List my transcripts about databases."

## URLs

| Path | What it is |
| --- | --- |
| `/<key>/mcp` | the MCP connector endpoint |
| `/<key>/` | the history page |
| `/<key>/t/<video_id>.<lang>.md` | a transcript download |

Any other path returns an empty 404.

## Security model

- The key is the password. Anyone who has the URL can use the server and see the whole history. Treat the URL
  like a secret and do not post it anywhere.
- To rotate it, change `YTM_SECRET_KEY`, restart the service and re-add the connector with the new URL.
- The server's access log is off, so keys in request paths do not end up in logs. If a reverse proxy sits in
  front, make sure it does not log paths either.
- Titles, channel names and captions come from the video, so they are untrusted. They are Markdown-escaped in
  the `.md` files, and the server tells Claude that transcript text is untrusted content and that instructions
  inside it must not be followed.
- Each fetch runs yt-dlp in a separate worker process group that is killed on timeout.
- Transcripts are kept forever under `YTM_DATA_DIR` (`./data` by default). Back that directory up if you care
  about the history; delete files from it to remove transcripts.

## Limits

- Captions only: a video without captions cannot be transcribed.
- One video per call.
- One fetch at a time. A second request waits up to 30 seconds for its turn, then gets a "busy" error.
- A fetch is stopped after 200 seconds (`YTM_FETCH_TIMEOUT`).
- Tool results are paged at 100,000 characters.

## Development

```bash
.venv/bin/python -m pytest -q
```

The tests never touch the network: every yt-dlp call goes through an injectable boundary, and the test
captions are synthetic.

## License

MIT, see [LICENSE](LICENSE).
