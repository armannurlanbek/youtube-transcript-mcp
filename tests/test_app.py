from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from ytmcp.app import build_app
from ytmcp.config import Settings
from ytmcp.service import Transcriber
from ytmcp.store import Store
from ytmcp.web import download_name
from ytmcp.youtube import Fetched, Track

KEY = "Abc_def-" * 5
VID = "aB3_-xYz012"
HOST = "transcripts.example.com"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "Host": HOST}


def settings(tmp_path):
    return Settings(secret_key=KEY, public_url=f"https://{HOST}", data_dir=tmp_path)


def seeded(tmp_path, title="Birdhouse <b>&</b>", channel="Shop Talk"):
    s = settings(tmp_path)
    store = Store(tmp_path)
    store.save(fetched=Fetched(VID, title, channel, 600, "2026-10-01", Track("en-orig", "en", "auto", "u"), ""),
               markdown="# hi\n", is_original=True, now=datetime(2026, 10, 9, tzinfo=UTC))
    return s, Transcriber(store, s, runner=None)


def client(tmp_path, **kw):
    s, t = seeded(tmp_path, **kw)
    return TestClient(build_app(s, transcriber=t), base_url=f"https://{HOST}")


@pytest.mark.parametrize("path", ["/", "/mcp", f"/{KEY[:-1]}/", f"/{KEY}x/", f"/{KEY}x/mcp",
                                  "/style.css", f"/t/{VID}.en.md", f"/{KEY.upper()}/", "/favicon.ico"])
def test_wrong_or_missing_key_is_empty_404(tmp_path, path):
    with client(tmp_path) as c:
        r = c.get(path)
    assert r.status_code == 404 and r.content == b""
    assert r.headers["referrer-policy"] == "no-referrer"


def test_history_page(tmp_path):
    with client(tmp_path) as c:
        r = c.get(f"/{KEY}/")
    assert r.status_code == 200
    html = r.text
    assert "Birdhouse &lt;b&gt;&amp;&lt;/b&gt;" in html and "<b>&" not in html
    assert f'href="/{KEY}/t/{VID}.en.md"' in html
    assert f'href="https://www.youtube.com/watch?v={VID}"' in html
    assert 'rel="noreferrer noopener"' in html
    assert f'href="/{KEY}/style.css"' in html
    assert "<script" not in html
    assert "10:00" in html and "2026-10-09" in html
    h = r.headers
    assert h["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-robots-tag"] == "noindex, nofollow"
    assert h["cache-control"] == "no-store"


def test_history_search_and_empty(tmp_path):
    with client(tmp_path) as c:
        assert "Birdhouse" not in c.get(f"/{KEY}/", params={"q": "zzz"}).text
        assert "Birdhouse" in c.get(f"/{KEY}/", params={"q": "SHOP"}).text
        assert 'value="&lt;x&gt;"' in c.get(f"/{KEY}/", params={"q": "<x>"}).text
    s = settings(tmp_path / "empty")
    with TestClient(build_app(s), base_url=f"https://{HOST}") as c:
        assert "No transcripts yet" in c.get(f"/{KEY}/").text


def test_stylesheet(tmp_path):
    with client(tmp_path) as c:
        r = c.get(f"/{KEY}/style.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")


def test_download(tmp_path):
    with client(tmp_path, title="Обзор: Birdhouse / v2") as c:
        r = c.get(f"/{KEY}/t/{VID}.en.md")
    assert r.status_code == 200 and r.text == "# hi\n"
    assert r.headers["content-type"].startswith("text/markdown")
    cd = r.headers["content-disposition"]
    assert cd.startswith('attachment; filename="Birdhouse-v2.md"; filename*=UTF-8\'\'')
    assert "%D0%9E" in cd


@pytest.mark.parametrize("name", [f"{VID}.de.md", "..%2Ftranscripts.db", "../transcripts.db",
                                  f"{VID}.en.md.bak", "x.md"])
def test_download_rejects(tmp_path, name):
    with client(tmp_path) as c:
        assert c.get(f"/{KEY}/t/{name}").status_code == 404


def test_download_name():
    assert download_name("Hello, World!", VID) == "Hello-World.md"
    assert download_name("Обзор", VID) == f"{VID}.md"
    assert len(download_name("a" * 300, VID)) <= 83


def test_mcp_endpoint(tmp_path):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    with client(tmp_path) as c:
        r = c.post(f"/{KEY}/mcp", headers=MCP_HEADERS, json=init)
        assert r.status_code == 200 and "youtube-transcripts" in r.text
        r = c.post(f"/{KEY}/mcp", headers=MCP_HEADERS,
                   json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert "transcribe_youtube" in r.text
        bad = c.post(f"/{KEY}/mcp", headers={**MCP_HEADERS, "Host": "evil.example.org"}, json=init)
        assert bad.status_code == 421
        assert c.post("/mcp", headers=MCP_HEADERS, json=init).status_code == 404


def test_key_without_slash_serves_history_and_never_redirects(tmp_path):
    with client(tmp_path) as c:
        r = c.get(f"/{KEY}", follow_redirects=False)
        assert r.status_code == 200 and "Birdhouse" in r.text and "location" not in r.headers
        for path in (f"/{KEY}/mcp/", f"/{KEY}/t/", f"/{KEY}/style.css/", f"/{KEY}/t/{VID}.en.md/"):
            r = c.get(path, follow_redirects=False)
            assert not 300 <= r.status_code < 400 and "location" not in r.headers, path


@pytest.mark.parametrize("key", [KEY, "wrong"])
def test_websocket_is_closed_for_any_key(tmp_path, key):
    from starlette.websockets import WebSocketDisconnect
    with client(tmp_path) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"/{key}/ws"):
                pass


def test_download_with_huge_title_has_bounded_header(tmp_path):
    with client(tmp_path, title="Ж" * 1000) as c:
        r = c.get(f"/{KEY}/t/{VID}.en.md")
    assert r.status_code == 200 and len(r.headers["content-disposition"]) < 1500
