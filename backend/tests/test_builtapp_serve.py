"""Built App serving tests — the published app is served by Python, not `vite preview` (ADR-0002).

The server under test ships IN the app's repo (`template/react-vite/serve.py`), not in the sage
package, so it is loaded by path here. It is stdlib-only for the same reason it lives there: the App
container's python3 is whichever one the image ships, so there is nothing to install into it.
"""
from __future__ import annotations

import http.client
import importlib.util
import socket
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
    spec.loader.exec_module(mod)
    return mod


serve = _load_serve()


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A stand-in for `vite build` output: hashed assets, a favicon from public/, and a data file
    rehydrated from the attachments manifest."""
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "data").mkdir()
    (d / "index.html").write_text('<!doctype html><div id="root">APP</div>')
    (d / "assets" / "index-abc123.js").write_text("export const x = 1;\n")
    (d / "assets" / "index-abc123.css").write_text(":root{--accent:#543FDE}")
    (d / "favicon.svg").write_text("<svg/>")
    (d / "data" / "sales.csv").write_text("a,b\n1,2\n")
    return d


@contextmanager
def running(root: Path):
    """The server on a throwaway port, yielding its base URL."""
    srv = serve.build_server(root, host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
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


def test_a_route_deeper_than_one_segment_still_cannot_load_its_assets(dist: Path):
    # NOT a property of this server — of the relative build base (vite.config.ts `base: "./"`). The
    # index.html it hands back says src="./assets/…", which the browser resolves against /reports/,
    # so the asset request arrives one directory too deep and misses. `vite preview` answered that
    # same request with index.html at 200, which the browser then parsed as a JS module: both serve a
    # blank page, and a 404 at least names the file. Pinned here so the limitation is visible to
    # whoever fixes it (it needs a <base href> or an absolute build base, not a fallback tweak).
    with running(dist) as base:
        page = httpx.get(base + "/reports/2026")
        asset = httpx.get(base + "/reports/assets/index-abc123.js")
    assert page.status_code == 200  # the route itself resolves
    assert asset.status_code == 404  # but its assets do not


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
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/access-token"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_probe_reports_a_reachable_sidecar_without_disclosing_the_token():
    with _stub_sidecar(b"Bearer eyJhbGciOi.SUPERSECRET.sig") as url:
        status = serve.probe_token_sidecar(url)
    assert "reachable" in status
    assert "SUPERSECRET" not in status  # app logs are readable by anyone who can see the deploy


def test_probe_reports_an_unreachable_sidecar_rather_than_raising():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing is listening there now

    status = serve.probe_token_sidecar(f"http://127.0.0.1:{port}/access-token", timeout=1.0)

    assert "not reachable" in status.lower()


def test_sidecar_url_prefers_the_injected_proxy_address(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DOMINO_API_PROXY", "http://localhost:9999/")
    assert serve.sidecar_url() == "http://localhost:9999/access-token"


def test_sidecar_url_falls_back_to_the_documented_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DOMINO_API_PROXY", raising=False)
    assert serve.sidecar_url() == "http://localhost:8899/access-token"
