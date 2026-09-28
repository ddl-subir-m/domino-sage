"""A dropped connection is tried again, and the failed attempt does not keep its socket.

The pause is stubbed in every test here. What is asserted is how many times the call went out,
and that the client, the reader, and the local server are closed before the test returns.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import types
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from sage.assets.provider import DominoAssetProvider
from sage.gateway.client import (
    CostLabels,
    GatewayUpstreamError,
    MultiProviderOpenAIClient,
    OpenAICompatibleClient,
)
from sage.gateway.open_models import OpenModel
from sage.liveread.mcp import _failed_text
from sage.resources.model_api_credentials import verify_credential
from sage.resources.provider import DataSource, DominoResourceProvider, ResourceUnavailable
from sage.transient import lost_on_a_short_drop

from .fake_opencode import Turn
from .test_chat_turn import _no_waiting, _orch

__all__ = ["_no_waiting"]

_ROOT = Path(__file__).resolve().parents[2]
_FLIGHT = (
    "Flight returned unavailable error, with message: failed to connect to all addresses; "
    "last error: UNKNOWN: ipv4:10.0.3.4:8080: Failed to connect to remote host: Connection refused"
)
_LABELS = CostLabels("plan", "auto")


def _no_pause(monkeypatch) -> None:
    monkeypatch.setattr("sage.transient.pause", lambda _attempt: None)


@contextmanager
def _server(handler):
    """A local server that is shut down, closed, and joined before the test moves on."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("the test server thread was still running")


def _read(client, body: dict) -> bytes:
    gen = client.route(body, _LABELS)
    try:
        return b"".join(gen)
    finally:
        gen.close()


def _gateway(client_factory):
    """Borrow a client and always close it, including when the call raises."""
    client = client_factory()
    try:
        yield client
    finally:
        client.close()


# The helper above is a generator; tests want the context manager.
_gateway = contextmanager(_gateway)


# --- the gateway ---------------------------------------------------------------------------


def _post_server(codes: list[int], *, drop_after: int | None = None):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            seen.append(self.path)
            code = codes.pop(0) if codes else 200
            if drop_after is not None:
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"x" * drop_after)
                self.close_connection = True
                return
            payload = b"data: ok\n\n" if code < 400 else b"busy"
            self.send_response(code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    return Handler, seen


def test_a_502_then_a_200_is_one_answer(monkeypatch):
    _no_pause(monkeypatch)
    Handler, seen = _post_server([502, 200])
    with _server(Handler) as srv:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        with _gateway(lambda: OpenAICompatibleClient(base, lambda: "tok")) as client:
            assert _read(client, {"model": "m"}) == b"data: ok\n\n"
    assert len(seen) == 2


def test_the_same_retry_covers_a_per_model_endpoint(monkeypatch):
    _no_pause(monkeypatch)
    Handler, seen = _post_server([503, 200])
    monkeypatch.setenv("TEST_MODEL_KEY", "k")
    with _server(Handler) as srv:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        model = OpenModel("m", "test", base, "TEST_MODEL_KEY")
        with _gateway(lambda: MultiProviderOpenAIClient([model])) as client:
            assert _read(client, {"model": "m"}) == b"data: ok\n\n"
    assert len(seen) == 2


def test_a_400_is_the_answer_and_is_not_sent_again(monkeypatch):
    _no_pause(monkeypatch)
    Handler, seen = _post_server([400])
    with _server(Handler) as srv:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        with _gateway(lambda: OpenAICompatibleClient(base, lambda: "tok")) as client:
            with pytest.raises(GatewayUpstreamError) as caught:
                _read(client, {"model": "m"})
    assert caught.value.status == 400
    assert len(seen) == 1


def test_four_502s_are_the_whole_allowance(monkeypatch):
    _no_pause(monkeypatch)
    pauses = []
    monkeypatch.setattr("sage.transient.pause", lambda attempt: pauses.append(attempt))
    Handler, seen = _post_server([502, 502, 502, 502])
    with _server(Handler) as srv:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        with _gateway(lambda: OpenAICompatibleClient(base, lambda: "tok", timeout_s=1.0)) as client:
            with pytest.raises(GatewayUpstreamError) as caught:
                _read(client, {"model": "m"})
    assert caught.value.status == 502
    assert len(seen) == 4
    assert pauses == [0, 1, 2]


def test_a_refused_connection_is_retried_and_the_client_is_closed(monkeypatch):
    pauses = []
    monkeypatch.setattr("sage.transient.pause", lambda attempt: pauses.append(attempt))
    client = OpenAICompatibleClient("http://127.0.0.1:1/v1", lambda: "tok", timeout_s=1.0)
    try:
        with pytest.raises(httpx.ConnectError):
            _read(client, {"model": "m"})
    finally:
        client.close()
    assert pauses == [0, 1, 2]
    assert client._http is None


def test_bytes_already_delivered_are_not_fetched_again(monkeypatch):
    _no_pause(monkeypatch)
    Handler, seen = _post_server([], drop_after=4)
    with _server(Handler) as srv:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        with _gateway(lambda: OpenAICompatibleClient(base, lambda: "tok", timeout_s=1.0)) as client:
            with pytest.raises(httpx.TransportError):
                _read(client, {"model": "m"})
    assert len(seen) == 1


# --- a Data Source -------------------------------------------------------------------------


def _source() -> DataSource:
    return DataSource(id="ds1", name="Warehouse", connector="Snowflake",
                      credential_type="Individual", connector_type="SnowflakeConfig")


def _install_store(monkeypatch, query):
    closed = []

    class Client:
        def close(self):
            closed.append(self)

        def get_datasource(self, _name):
            return self

        def query(self, sql):
            return query(sql)

    mod = types.ModuleType("domino_data.data_sources")
    mod.DataSourceClient = Client
    monkeypatch.setitem(sys.modules, "domino_data", types.ModuleType("domino_data"))
    monkeypatch.setitem(sys.modules, "domino_data.data_sources", mod)
    return closed


def _query(monkeypatch, query):
    _no_pause(monkeypatch)
    closed = _install_store(monkeypatch, query)
    provider = DominoResourceProvider.__new__(DominoResourceProvider)
    return provider, closed


def test_a_refused_store_connection_is_asked_again_and_both_clients_are_closed(monkeypatch):
    calls = {"n": 0}

    def query(_sql):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(_FLIGHT)
        return types.SimpleNamespace(to_pandas=lambda: "frame")

    provider, closed = _query(monkeypatch, query)
    assert DominoResourceProvider._query(provider, _source(), "SELECT 1") == "frame"
    assert calls["n"] == 2
    assert len(closed) == 2


def test_a_deadline_a_setup_fault_and_a_store_objection_are_not_retried(monkeypatch):
    for message in ("Deadline Exceeded", "no credentials for this source",
                    'ERROR: relation "t" does not exist'):
        calls = {"n": 0}

        def query(_sql, message=message, calls=calls):
            calls["n"] += 1
            raise RuntimeError(message)

        provider, closed = _query(monkeypatch, query)
        with pytest.raises(ResourceUnavailable):
            DominoResourceProvider._query(provider, _source(), "SELECT 1")
        assert calls["n"] == 1
        assert len(closed) == 1


def test_four_refused_connections_say_to_try_again(monkeypatch):
    calls = {"n": 0}

    def query(_sql):
        calls["n"] += 1
        raise RuntimeError(_FLIGHT)

    provider, closed = _query(monkeypatch, query)
    with pytest.raises(ResourceUnavailable, match="could not reach") as caught:
        DominoResourceProvider._query(provider, _source(), "SELECT 1")
    assert "Try again" in str(caught.value)
    assert calls["n"] == 4
    assert len(closed) == 4


# --- Dataset listing and a Model API probe -------------------------------------------------


def test_a_503_then_a_listing_is_the_listing(monkeypatch):
    _no_pause(monkeypatch)
    calls = {"n": 0}

    def get(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"datasets": [], "metadata": {"totalCount": 0}})

    monkeypatch.setattr(httpx, "get", get)
    found = DominoAssetProvider("https://acme.example", lambda: "tok", mount_roots=[]).list_datasets(None)
    assert found == []
    assert calls["n"] == 2


def test_a_403_listing_is_not_retried(monkeypatch):
    _no_pause(monkeypatch)
    calls = {"n": 0}

    def get(*_args, **_kwargs):
        calls["n"] += 1
        return httpx.Response(403)

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(ResourceUnavailable, match="answered 403"):
        DominoAssetProvider("https://acme.example", lambda: "tok", mount_roots=[]).list_datasets(None)
    assert calls["n"] == 1


def test_a_probe_that_fails_to_connect_once_is_asked_again(monkeypatch):
    _no_pause(monkeypatch)
    calls = {"n": 0}

    def post(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"result": {}})

    monkeypatch.setattr(httpx, "post", post)
    assert verify_credential("https://acme.example/model", "tok").ok
    assert calls["n"] == 2


# --- what the model is told, and what counts as a drop ------------------------------------


def test_a_reach_failure_tells_the_model_to_call_the_tool_once_more():
    why = ("Ada could not reach Warehouse just now. Nothing is wrong with the Data Source "
           "itself. Try again in a moment.")
    text = _failed_text(why)
    assert "Call this same tool once more" in text
    assert "Query the data with Python" not in text
    assert "Nothing was put on the person's screen." in text


def test_a_store_objection_still_says_to_use_python():
    text = _failed_text('Warehouse did not answer: ERROR: relation "t" does not exist')
    assert "Query the data with Python" in text
    assert "Call this same tool once more" not in text


@pytest.mark.parametrize("body,step,gateway,expected", [
    ("", "ConnectError: connection refused", "", True),
    (("⚠️ The model gateway closed the stream mid-response (ReadError). "
      "This is usually an upstream idle or duration limit — please retry."), "", "", True),
    (("There are 12 rows.\n\n⚠️ The model gateway closed the stream mid-response (ReadError). "
      "This is usually an upstream idle or duration limit — please retry."), "", "", False),
    ("", "blocked by a guardrail", "", False),
    ("", "context length exceeded", "", False),
    ("", "", "gateway returned 400 for http://gw: no", False),
    ("", "", "gateway returned 502 for http://gw: busy", True),
])
def test_only_an_empty_drop_is_re_sent(body, step, gateway, expected):
    assert lost_on_a_short_drop(body, step, gateway) is expected


# --- Chat re-sends the question once -------------------------------------------------------


def test_chat_asks_the_question_once_more_after_a_drop(tmp_path):
    orch, oc = _orch(tmp_path, [
        Turn(error={"data": {"message": "ConnectError: connection refused"}}),
        Turn(text="There are 12 rows."),
    ])
    out = list(orch.chat_stream(orch.create_thread()["id"], "how many rows"))
    assert len(oc.prompts) == 2
    assert oc.prompts[1]["text"].startswith("how many rows")
    done = next(event for event in out if event["type"] == "done")
    assert done["ok"] is True
    assert done["recoveries"] == 1
    assert not any("couldn't finish" in str(event.get("message") or "") for event in out)


def test_chat_does_not_re_send_a_context_length_refusal(tmp_path):
    orch, oc = _orch(tmp_path, [
        Turn(error={"data": {"message": "context length exceeded"}}),
    ])
    list(orch.chat_stream(orch.create_thread()["id"], "how many rows"))
    assert len(oc.prompts) == 1


# --- a published app -----------------------------------------------------------------------


def _load(name: str, filename: str):
    path = _ROOT / "template" / "react-vite" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Column:
    def __init__(self, values):
        self._values = list(values)

    def to_pylist(self):
        return list(self._values)


class _Reader:
    def __init__(self, *, rows=None, fail=None):
        self.schema = types.SimpleNamespace(names=["region"])
        self.cancelled = False
        self._rows = rows or []
        self._fail = fail
        self._done = False

    def read_chunk(self):
        if self._fail is not None:
            raise self._fail
        if self._done:
            raise StopIteration
        self._done = True
        return types.SimpleNamespace(data=types.SimpleNamespace(
            columns=[_Column([row[0] for row in self._rows])],
            num_rows=len(self._rows),
        ))

    def cancel(self):
        self.cancelled = True


def test_a_published_query_retries_a_reset_and_cancels_the_failed_reader(monkeypatch):
    sq = _load("sage_queries_retry", "sage_queries.py")
    monkeypatch.setattr(sq.time, "sleep", lambda *_a, **_k: None)
    failed = _Reader(fail=RuntimeError("Connection reset by peer"))
    ok = _Reader(rows=[["EMEA"]])
    readers = [failed, ok]
    closed = []

    class Client:
        def __init__(self):
            self.did_close = False

        def close(self):
            self.did_close = True
            closed.append(self)

        def get_datasource(self, _name):
            return self

        def update(self, _config):
            return None

        def query(self, _sql):
            return types.SimpleNamespace(reader=readers.pop(0))

    mod = types.ModuleType("domino_data.data_sources")
    mod.DataSourceClient = Client
    monkeypatch.setitem(sys.modules, "domino_data", types.ModuleType("domino_data"))
    monkeypatch.setitem(sys.modules, "domino_data.data_sources", mod)
    source = sq.Source(id="s", name="Warehouse", connector_type="SnowflakeConfig")
    executor = sq.FlightExecutor({"wh": source}, 10)
    got = executor(sq.Query(name="rows", binding="wh", sql="SELECT 1"), {})
    assert got["rows"] == [["EMEA"]]
    assert failed.cancelled is True
    assert ok.cancelled is False
    assert len(closed) == 1 and closed[0].did_close is True


def _platform(codes: list[int]):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            hits.append(self.path)
            if self.path.endswith("/access-token"):
                payload = b"Bearer tok"
                code = 200
            else:
                code = codes.pop(0) if codes else 200
                payload = b'{"ok": true}' if code < 400 else b"no"
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    return Handler, hits


def test_a_published_platform_read_retries_a_502_and_not_a_404(monkeypatch):
    sd = _load("sage_domino_retry", "sage_domino.py")
    monkeypatch.setattr(sd.time, "sleep", lambda *_a, **_k: None)
    Handler, hits = _platform([502, 200])
    with _server(Handler) as srv:
        base = f"http://127.0.0.1:{srv.server_port}"
        monkeypatch.setenv("DOMINO_API_PROXY", base)
        monkeypatch.setenv("DOMINO_API_HOST", base)
        status, _headers, body = sd.get("/api/users/v1/self")
    assert status == 200 and json.loads(body) == {"ok": True}
    platform_hits = [path for path in hits if path != "/access-token"]
    assert platform_hits == ["/api/users/v1/self", "/api/users/v1/self"]

    Handler, hits = _platform([404])
    with _server(Handler) as srv:
        base = f"http://127.0.0.1:{srv.server_port}"
        monkeypatch.setenv("DOMINO_API_PROXY", base)
        monkeypatch.setenv("DOMINO_API_HOST", base)
        status, _headers, _body = sd.get("/api/users/v1/self")
    assert status == 404
    assert [path for path in hits if path != "/access-token"] == ["/api/users/v1/self"]
