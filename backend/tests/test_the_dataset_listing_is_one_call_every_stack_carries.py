"""Every Dataset, with its taxonomy tags, is one call an app cannot get wrong — on every stack.

A built app's study picker listed nothing on dogfood although four study Datasets were tagged. The
agent had written the pager and the tag join itself, from the reads table, and read the tags off
`datasetRwDto` where the platform never puts them: `datasets-v2` carries `taxonomyTags` BESIDE
`datasetRwDto` on the row, as `{namespaceLabel: "study", label: "abc123"}`. `sage_domino.py` now
does both reads once, and the page asks for `api/domino/sage/datasets`.

The fake platform below answers in the shapes measured on dogfood on 2026-09-30: listing rows are
`{"dataset": {...}, "projectInfo": {...}}`, 214 of them, and four carry tags.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from sage.preview.read_outcomes import read_request
from sage.workspace.stack import FASTAPI_ANTD, STACKS

_TEMPLATE = Path(__file__).resolve().parents[2] / "template"
_TAGGED = {"ds-003": "abc123", "ds-004": "abc123", "ds-150": "xyz789", "ds-213": "xyz789"}


def _load(stack: str):
    spec = importlib.util.spec_from_file_location(
        f"sage_domino_{stack.replace('-', '_')}", _TEMPLATE / stack / "sage_domino.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=sorted(STACKS))
def sd(request, monkeypatch):
    module = _load(request.param)
    monkeypatch.setattr(module, "token", lambda: "t")
    return module


def _datasets(n: int = 214) -> list[dict]:
    return [{"id": f"ds-{i:03d}", "name": f"DS_{i:03d}", "projectId": "p1", "tags": {}}
            for i in range(n)]


@contextmanager
def _platform(monkeypatch, *, page_cap: int = 10_000, fail: dict | None = None):
    """A platform that pages the listing (capping `limit` at `page_cap`) and joins tags by id."""
    everything = _datasets()
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            url = urlsplit(self.path)
            q = parse_qs(url.query)
            for target, status in (fail or {}).items():
                path, _, needle = target.partition("?")
                if url.path == path and needle in url.query:
                    return self._send(status, {"message": "nope"})
            if url.path == "/api/datasetrw/v2/datasets":
                offset = int(q.get("offset", ["0"])[0])
                limit = min(int(q.get("limit", ["10"])[0]), page_cap)
                rows = [{"dataset": d, "projectInfo": {"projectId": "p1", "projectName": "proj"}}
                        for d in everything[offset:offset + limit]]
                return self._send(200, {"datasets": rows,
                                        "metadata": {"offset": offset, "limit": limit}})
            if url.path == "/v4/datasetrw/datasets-v2":
                assert q.get("includeTaxonomyTags") == ["true"]
                ids = q["datasetIds"][0].split(",")
                rows = []
                for i in ids:
                    row = {"datasetRwDto": {"id": i, "name": f"DS_{i[3:]}"}}
                    if i in _TAGGED:
                        row["taxonomyTags"] = [{"id": "t", "label": _TAGGED[i],
                                                "namespaceLabel": "study"}]
                    rows.append(row)
                return self._send(200, rows)
            return self._send(404, {"message": "no route"})

        def _send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    t.start()
    monkeypatch.setenv("DOMINO_API_HOST", f"http://127.0.0.1:{srv.server_address[1]}")
    try:
        yield seen
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_every_dataset_comes_back_with_its_tags(sd, monkeypatch):
    with _platform(monkeypatch):
        status, datasets = sd.list_datasets()

    assert status == 200
    assert len(datasets) == 214
    assert datasets[0] == {"id": "ds-000", "name": "DS_000", "project": "proj", "tags": []}
    tagged = {d["id"]: d["tags"] for d in datasets if d["tags"]}
    assert tagged == {i: [{"namespace": "study", "label": label}] for i, label in _TAGGED.items()}


def test_a_platform_that_caps_the_page_still_gives_every_dataset(sd, monkeypatch):
    with _platform(monkeypatch, page_cap=50):
        status, datasets = sd.list_datasets()

    assert status == 200 and len(datasets) == 214


def test_the_page_reaches_it_through_the_relay(sd, monkeypatch):
    with _platform(monkeypatch):
        status, headers, body = sd.relay("sage/datasets")

    assert status == 200 and headers["Content-Type"].startswith("application/json")
    datasets = json.loads(body)["datasets"]
    assert len(datasets) == 214 and sum(1 for d in datasets if d["tags"]) == 4


@pytest.mark.parametrize("prefix", ["/api/datasetrw/v2/datasets?offset=200",
                                    "/v4/datasetrw/datasets-v2"])
def test_a_failed_read_is_the_answer_not_a_shorter_list(sd, monkeypatch, prefix):
    with _platform(monkeypatch, fail={prefix: 403}):
        status, _, body = sd.relay("sage/datasets")

    assert status == 403
    error = json.loads(body)["error"]
    assert prefix.split("?")[0] in error and "403" in error


def test_only_the_one_helper_path_is_admitted(sd):
    assert sd.allowed("/sage/datasets") is True
    assert sd.allowed("/sage/datasets/x") is False
    assert sd.allowed("/sage") is False


def test_every_stack_ships_the_same_helper():
    texts = {stack: (_TEMPLATE / stack / "sage_domino.py").read_bytes() for stack in STACKS}
    assert len(set(texts.values())) == 1, sorted(texts)


def test_an_existing_fastapi_app_gets_the_helper_on_refresh():
    files = FASTAPI_ANTD.deploy_files
    assert "sage_domino.py" in files
    assert files.index("sage_domino.py") < files.index("sage_serve.py")


@pytest.mark.parametrize("stack", sorted(STACKS))
def test_the_instructions_send_the_agent_to_the_helper(stack):
    text = (_TEMPLATE / stack / "AGENTS.md").read_text()
    assert "api/domino/sage/datasets" in text
    assert "api/domino/api/datasetrw/v2/datasets" not in text


def test_a_fastapi_route_is_told_it_can_call_the_helper_too():
    assert "from sage_domino import list_datasets" in (_TEMPLATE / "fastapi-antd" / "AGENTS.md").read_text()


def test_the_preview_diagnostics_name_the_helper_read():
    assert read_request("/sage/datasets")["path"] == "/sage/datasets"
