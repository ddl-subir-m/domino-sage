"""Built App serving tests — the published app is served by Python, not `vite preview` (ADR-0002).

The server under test ships IN the app's repo (`template/react-vite/serve.py`), not in the sage
package, so it is loaded by path here. It is stdlib-only for the same reason it lives there: the App
container's python3 is whichever one the image ships, so there is nothing to install into it.
"""
from __future__ import annotations

import http.client
import importlib.util
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

_SERVE_PY = Path(__file__).resolve().parents[2] / "template" / "react-vite" / "serve.py"


def _load_serve():
    spec = importlib.util.spec_from_file_location("builtapp_serve", _SERVE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `serve.py` uses `from __future__ import annotations`, so a dataclass
    # resolves its field types by looking its own module up in sys.modules. Running the app for real
    # (`python3 serve.py`) puts it there as __main__; loading it by path here does not unless we do.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


serve = _load_serve()
sq = serve.sq  # the query half (`sage_queries.py`), the same module object serve.py imported


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A stand-in for `vite build` output: hashed assets, a favicon from public/, and a data file
    rehydrated from the attachments manifest."""
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "data").mkdir()
    (d / "index.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>app</title></head>'
        '<body><div id="root">APP</div>'
        '<script type="module" crossorigin src="./assets/index-abc123.js"></script>'
        "</body></html>"
    )
    (d / "assets" / "index-abc123.js").write_text("export const x = 1;\n")
    (d / "assets" / "index-abc123.css").write_text(":root{--accent:#543FDE}")
    (d / "favicon.svg").write_text("<svg/>")
    (d / "data" / "sales.csv").write_text("a,b\n1,2\n")
    return d


@contextmanager
def running(root: Path):
    """The server on a throwaway port, yielding its base URL."""
    srv = serve.build_server(root, host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_root_serves_the_built_index(dist: Path):
    with running(dist) as base:
        r = httpx.get(base + "/")
    assert r.status_code == 200
    assert 'id="root"' in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_hashed_js_asset_is_served_as_javascript_and_cached_immutably(dist: Path):
    # A wrong content-type here is not cosmetic: the browser refuses to execute a module served as
    # octet-stream, so the app renders a blank page with only a console error to explain it.
    with running(dist) as base:
        r = httpx.get(base + "/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.text == "export const x = 1;\n"
    assert r.headers["content-type"].startswith(("text/javascript", "application/javascript"))
    assert "immutable" in r.headers["cache-control"]


def test_css_and_svg_get_their_own_types(dist: Path):
    with running(dist) as base:
        css = httpx.get(base + "/assets/index-abc123.css")
        svg = httpx.get(base + "/favicon.svg")
    assert css.headers["content-type"].startswith("text/css")
    assert svg.headers["content-type"].startswith("image/svg+xml")


def test_index_html_is_not_cached(dist: Path):
    # The asset names are hashed, index.html is not — a cached copy would keep pointing at the
    # previous deploy's assets after a republish.
    with running(dist) as base:
        r = httpx.get(base + "/")
    assert r.headers["cache-control"] == "no-cache"


def test_client_side_route_falls_back_to_index(dist: Path):
    # react-router-dom is in the template's toolbox, so a viewer can land on a route rather than on
    # /. This is what `vite preview`'s html fallback does for an extensionless path.
    with running(dist) as base:
        r = httpx.get(base + "/reports")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_a_deep_route_gets_a_base_href_so_its_assets_resolve(dist: Path):
    # The bug this fixes (#18): the build base is relative (`vite.config.ts` `base: "./"`), so
    # `src="./assets/…"` on a page at /apps/uuid/reports/2026 resolved against /apps/uuid/reports/
    # and asked one directory too deep. The shim gives the page a <base href> instead, so the browser
    # asks for the same URL it asks for at the root.
    with running(dist) as base:
        page = httpx.get(base + "/reports/2026")
        asset = httpx.get(base + "/assets/index-abc123.js")
    assert page.status_code == 200
    assert '"/reports/2026"' in page.text  # the path this server received, for the shim to subtract
    assert asset.status_code == 200


def test_the_shim_is_the_first_thing_in_head(dist: Path):
    # `document.write` inserts at the parser's position, so the <base> only governs the asset URLs
    # if the shim runs before the tags that carry them.
    with running(dist) as base:
        body = httpx.get(base + "/").text
    assert body.index("__SAGE_BASE__") < body.index("index-abc123.js")
    assert body.index("<head") < body.index("__SAGE_BASE__")


def test_the_shim_records_the_path_before_the_spa_rewrite(dist: Path):
    # /reports/2026 is answered WITH index.html, so by the time the file is read `self.path` says
    # /index.html. Stamping that would make every prefix subtract to the wrong thing.
    with running(dist) as base:
        deep = httpx.get(base + "/a/b/c").text
        query = httpx.get(base + "/reports?year=2026").text
    assert '"/a/b/c"' in deep
    assert '"/reports"' in query  # the query string is not part of the path being subtracted


def test_the_shim_quotes_a_path_that_could_break_out_of_its_string():
    # The received path reaches the page as a JS string literal. A quote or a backslash in it must
    # not end that literal early, or the shim becomes a place to inject script.
    out = serve.inject_base_shim("<head></head>", '/a"b\\c')
    assert 'var s="/a\\"b\\\\c"' in out
    assert out.count("<script>") == 1


def test_the_shim_still_precedes_every_url_when_there_is_no_head():
    out = serve.inject_base_shim('<div id="root"></div><script src="./x.js"></script>', "/")
    assert out.index("__SAGE_BASE__") < out.index("./x.js")


def test_head_and_get_agree_on_the_length_of_the_patched_page(dist: Path):
    # index.html is built in memory now, so a Content-Length copied from the file on disk would be
    # short by the length of the shim and the browser would truncate the page.
    with running(dist) as base:
        got = httpx.get(base + "/")
        head = httpx.head(base + "/")
    assert head.headers["content-length"] == str(len(got.content))
    assert head.content == b""


def test_missing_asset_is_a_404_not_the_index_page(dist: Path):
    # The other half of the fallback rule. Answering a <script src> with HTML turns a broken build
    # into a silent blank page; a 404 says which file is missing.
    with running(dist) as base:
        r = httpx.get(base + "/assets/index-deadbeef.js")
    assert r.status_code == 404
    assert "root" not in r.text
    # An immutable year on a 404 outlives the mistake that caused it.
    assert "immutable" not in r.headers["cache-control"]


def test_responses_do_not_advertise_the_interpreter(dist: Path):
    with running(dist) as base:
        r = httpx.get(base + "/")
    assert "Python" not in r.headers.get("server", "")


def test_rehydrated_data_file_is_served(dist: Path):
    # public/data/ is rebuilt from .sage/attachments.json before the build, so attachments arrive
    # in dist/data/ and the app fetches them by path.
    with running(dist) as base:
        r = httpx.get(base + "/data/sales.csv")
    assert r.status_code == 200
    assert r.text == "a,b\n1,2\n"


def test_directories_are_not_listed(dist: Path):
    with running(dist) as base:
        r = httpx.get(base + "/assets/")
    assert r.status_code == 404
    assert "index-abc123.js" not in r.text


def test_head_matches_get_without_a_body(dist: Path):
    with running(dist) as base:
        r = httpx.head(base + "/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["content-length"] == "20"


def test_traversal_out_of_the_build_directory_is_refused(dist: Path, tmp_path: Path):
    # http.client sends the path verbatim; httpx would normalize the `..` away before it left.
    (tmp_path / "secret.txt").write_text("SECRET")
    with running(dist) as base:
        port = int(base.rsplit(":", 1)[1])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/../secret.txt")
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
        conn.close()
    assert status == 404
    assert b"SECRET" not in body


def test_serving_needs_a_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    empty = tmp_path / "dist"
    empty.mkdir()
    assert serve.main(["--dir", str(empty)]) == 1
    assert "npm run build" in capsys.readouterr().out


def test_cold_start_is_reported_against_the_time_app_sh_started(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SAGE_APP_T0", str(time.time() - 42))
    assert 41 <= (serve._cold_start_secs() or 0) <= 45


def test_cold_start_is_omitted_rather_than_reported_as_zero(monkeypatch: pytest.MonkeyPatch):
    # ADR-0002 says to take the recorded baseline from this line, so a made-up 0s is worse than
    # silence — it would be written down as the number to compare against.
    monkeypatch.delenv("SAGE_APP_T0", raising=False)
    assert serve._cold_start_secs() is None
    monkeypatch.setenv("SAGE_APP_T0", "not-a-timestamp")
    assert serve._cold_start_secs() is None


# --- token sidecar probe (the ADR-0002 prerequisite this ticket confirms) ----------------------


@contextmanager
def _stub_sidecar(body: bytes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/access-token"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_probe_reports_a_reachable_sidecar_without_disclosing_the_token():
    with _stub_sidecar(b"Bearer eyJhbGciOi.SUPERSECRET.sig") as url:
        status = sq.probe_token_sidecar(url)
    assert "reachable" in status
    assert "SUPERSECRET" not in status  # app logs are readable by anyone who can see the deploy


# Nothing can ever listen here, so a connection to it refuses instantly. Port 1 is privileged (no
# unprivileged test can bind it) and sits far below the ephemeral range that `bind(port 0)` draws
# from, so no parallel xdist worker can land on it. Binding port 0 and closing it left a window
# where another worker claimed the freed port and the connection unexpectedly succeeded.
_DEAD_PORT = 1


def test_probe_reports_an_unreachable_sidecar_rather_than_raising():
    status = sq.probe_token_sidecar(f"http://127.0.0.1:{_DEAD_PORT}/access-token", timeout=1.0)

    assert "not reachable" in status.lower()


def test_sidecar_url_prefers_the_injected_proxy_address(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DOMINO_API_PROXY", "http://localhost:9999/")
    assert sq.sidecar_url() == "http://localhost:9999/access-token"


def test_sidecar_url_falls_back_to_the_documented_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DOMINO_API_PROXY", raising=False)
    assert sq.sidecar_url() == "http://localhost:8899/access-token"


# --- the platform relay (#489) ------------------------------------------------------------------

sd = serve.sd  # the platform half (`sage_domino.py`), the same module object serve.py imported
_DOMINO_PY = _SERVE_PY.with_name("sage_domino.py")
_SECRET = b"Bearer eyJhbGciOi.SUPERSECRET.sig"   # what the sidecar mints; must reach the platform once


@contextmanager
def _stub_platform(status: int = 200, body: bytes = b'{"ok": true}',
                   content_type: str = "application/json", extra: dict | None = None):
    """A stand-in for DOMINO_API_HOST that records every request it hears, headers lower-cased."""
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


@contextmanager
def _on_platform(monkeypatch, status: int = 200, body: bytes = b'{"ok": true}',
                 content_type: str = "application/json", extra: dict | None = None):
    """This app on the platform: a host that records what reached it, and a sidecar minting a token."""
    with _stub_sidecar(_SECRET) as sidecar, _stub_platform(status, body, content_type, extra) as (host, seen):
        monkeypatch.setenv("DOMINO_API_PROXY", sidecar.removesuffix("/access-token"))
        monkeypatch.setenv("DOMINO_API_HOST", host)
        yield seen


def test_a_platform_read_carries_the_apps_token_and_nothing_of_the_browsers(dist, monkeypatch):
    with _on_platform(monkeypatch, body=b'{"datasets": []}') as seen, running(dist) as base:
        r = httpx.get(base + "/api/domino/api/datasetrw/v2/datasets?limit=50",
                      headers={"Cookie": "session=viewer", "Authorization": "Bearer the-viewers-own"})

    assert r.status_code == 200 and r.json() == {"datasets": []}
    [hit] = seen
    assert hit["path"] == "/api/datasetrw/v2/datasets?limit=50"
    # Once, not "Bearer Bearer": the sidecar's own prefix is stripped before the header is built.
    assert hit["headers"]["authorization"] == _SECRET.decode()
    assert "cookie" not in hit["headers"]


def test_a_path_outside_the_families_is_refused_before_the_platform_hears_of_it(dist, monkeypatch):
    with _on_platform(monkeypatch) as seen, running(dist) as base:
        r = httpx.get(base + "/api/domino/api/users/v1/user/u1/tokenCredentials/c1")

    assert r.status_code == 403
    assert "GET" in r.json()["error"] and "/api/datasetrw/" in r.json()["error"]
    assert seen == []


@pytest.mark.parametrize("path", [
    "/api/datasetrw/../users/v1/user/u1/roles",
    "/api/datasetrw/%2e%2e/users/v1/user/u1/roles",       # the same door, encoded
    "/api/datasetrw/%252e%252e/users/v1/user/u1/roles",   # and encoded twice, for a receiver that decodes twice
    "/api/datasetrw/..\\users/v1/user/u1/roles",          # a backslash, for a receiver that reads it as a slash
    "/api/datasetrw//v2/datasets",
    "/api/users/v1/user/u1/tokenCredentials/c1",
    "/api/users/v1/user",
    "/v4/datasetrw/datasets/d1/snapshot/file/test",    # a GET that drives an upload session
    "/v4/datasetUi/d1/snapshots",
    "/api/apps/beta/apps",
])
def test_a_path_cannot_leave_its_family(path: str):
    assert sd.allowed(path) is False


@pytest.mark.parametrize("path", [
    "/api/datasetrw/v2/datasets",
    "/api/datasetrw/v1/datasets/d1/snapshots",
    "/api/governance/v1/bundles/b1/approvals",
    "/api/users/v1/self",
    "/api/users/v1/users",
    "/api/users/v1/user/671fd3aa49827159bd79ed53",
    "/v4/datasetrw/datasets-v2",
    "/v4/datasetrw/snapshots/d1",
    "/v4/datasetrw/snapshot/s1/file/raw",
])
def test_every_family_admits_its_own_reads(path: str):
    assert sd.allowed(path) is True


def test_the_relay_takes_get_and_nothing_else(dist, monkeypatch):
    with _on_platform(monkeypatch) as seen, running(dist) as base:
        posted = httpx.post(base + "/api/domino/api/datasetrw/v2/datasets", json={})
        headed = httpx.head(base + "/api/domino/api/datasetrw/v2/datasets")

    assert posted.status_code == 405 and headed.status_code == 405
    assert "GET" in posted.json()["error"]
    assert seen == []


def test_off_the_platform_the_relay_says_so(dist, monkeypatch):
    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    with running(dist) as base:
        r = httpx.get(base + "/api/domino/api/users/v1/self")

    assert r.status_code == 503 and "platform" in r.json()["error"]


def test_the_platforms_own_refusal_reaches_the_page_unchanged(dist, monkeypatch):
    with _on_platform(monkeypatch, status=404, body=b'{"message": "no such dataset"}'), running(dist) as base:
        r = httpx.get(base + "/api/domino/api/datasetrw/v1/datasets/nope")

    assert r.status_code == 404 and r.json() == {"message": "no such dataset"}


def test_a_file_comes_back_as_the_bytes_it_is(dist, monkeypatch):
    with _on_platform(monkeypatch, body=b"a,b\n1,2\n", content_type="text/plain") as seen, running(dist) as base:
        r = httpx.get(base + "/api/domino/v4/datasetrw/snapshot/s1/file/raw?path=adae.csv")

    assert r.status_code == 200 and r.text == "a,b\n1,2\n"
    assert r.headers["content-type"].startswith("text/plain")
    assert seen[0]["path"] == "/v4/datasetrw/snapshot/s1/file/raw?path=adae.csv"


def test_a_redirect_is_relayed_as_its_status_and_the_token_stays_home(dist, monkeypatch):
    # The default urllib opener follows a 3xx and re-sends every header — the token included — to
    # wherever Location points. Here Location points at a second stub that must hear nothing.
    with _stub_platform(body=b"gotcha") as (elsewhere, heard_elsewhere):
        with _on_platform(monkeypatch, status=302, body=b"", extra={"Location": elsewhere + "/login"}) as seen, \
                running(dist) as base:
            r = httpx.get(base + "/api/domino/api/users/v1/self", follow_redirects=False)

    assert r.status_code == 302 and r.content == b""
    assert "location" not in r.headers          # the page is not sent there either
    assert len(seen) == 1 and heard_elsewhere == []


def test_a_relayed_answer_can_neither_be_sniffed_nor_rendered(dist, monkeypatch):
    # A file somebody wrote into a Dataset must never become a page on this app's origin: nosniff on
    # everything, and a download on anything that is not JSON. `fetch` ignores the latter.
    with _on_platform(monkeypatch, body=b"<script>1</script>", content_type="text/html"), running(dist) as base:
        page = httpx.get(base + "/api/domino/v4/datasetrw/snapshot/s1/file/raw?path=evil.html")
    with _on_platform(monkeypatch, body=b'{"ok": true}'), running(dist) as base:
        data = httpx.get(base + "/api/domino/api/users/v1/self")
        refused = httpx.get(base + "/api/domino/api/apps/beta/apps")

    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["content-disposition"] == "attachment"
    assert data.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in data.headers
    assert refused.status_code == 403 and refused.headers["x-content-type-options"] == "nosniff"


def test_head_and_post_leave_a_kept_alive_connection_usable(dist, monkeypatch):
    # One client, one connection. A HEAD answered with a body, or a POST answered without its body
    # read, leaves bytes on the wire that the next exchange reads as its own — so the GET after each
    # is the proof, not the 405 itself.
    with _on_platform(monkeypatch) as seen, running(dist) as base, httpx.Client(base_url=base) as c:
        assert c.head("/api/domino/api/users/v1/self").status_code == 405
        after_head = c.get("/api/domino/api/users/v1/self")
        assert c.post("/api/domino/api/users/v1/self", json={"unread": "x" * 512}).status_code == 405
        after_post = c.get("/api/domino/api/users/v1/self")

    assert after_head.status_code == 200 and after_post.status_code == 200
    assert len(seen) == 2


def test_a_failed_read_is_logged_on_one_line(monkeypatch, capsys):
    # The preview hands the relay a decoded path, so a CRLF the page put there would otherwise
    # start a second `[sage]` line in the log.
    with _on_platform(monkeypatch):
        status, _, _ = sd.get("/api/users/v1/self\r\n[sage] forged: all is well")

    assert status == 502
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1                                   # the forged line never starts one
    assert lines[0].startswith("[sage] platform api: GET") and "InvalidURL" in lines[0]


def test_a_dead_sidecar_is_a_502_and_not_a_hang(dist, monkeypatch):
    with _stub_platform() as (host, seen):
        monkeypatch.setenv("DOMINO_API_HOST", host)
        monkeypatch.setenv("DOMINO_API_PROXY", f"http://127.0.0.1:{_DEAD_PORT}")
        started = time.monotonic()
        with running(dist) as base:
            r = httpx.get(base + "/api/domino/api/users/v1/self")

    assert r.status_code == 502 and "token" in r.json()["error"]
    assert time.monotonic() - started < 5
    assert seen == []


def test_an_answer_past_the_cap_is_refused_rather_than_relayed(dist, monkeypatch):
    monkeypatch.setattr(sd, "MAX_BYTES", 8)
    with _on_platform(monkeypatch, body=b"x" * 9, content_type="text/plain"), running(dist) as base:
        r = httpx.get(base + "/api/domino/v4/datasetrw/snapshot/s1/file/raw?path=big.csv")

    assert r.status_code == 502 and "larger" in r.json()["error"]


def test_the_boot_line_names_the_host_and_the_status_and_never_the_token(monkeypatch):
    with _on_platform(monkeypatch) as seen:
        line = sd.platform_status()

    assert sd.platform_host() in line and "200" in line
    assert "SUPERSECRET" not in line      # app logs are readable by anyone who can see the deploy
    assert seen[0]["path"] == "/api/users/v1/self"


def test_the_boot_line_says_when_there_is_no_host(monkeypatch):
    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    assert "DOMINO_API_HOST" in sd.platform_status()


def test_the_relay_needs_nothing_the_venv_provides():
    """Run by path with whatever python3 the image has, so it must import stdlib only — plus the
    query module beside it, which is held to the same rule."""
    import ast

    tree = ast.parse(_DOMINO_PY.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    allowed = set(sys.stdlib_module_names) | {"sage_queries"}
    assert imported <= allowed, imported - allowed
