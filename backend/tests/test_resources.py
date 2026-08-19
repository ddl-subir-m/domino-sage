"""Resources module: LLM Alias listing (#5).

The listing is the INTERSECTION of two gateway calls, so most of what matters here is the join and
the shapes each call can arrive in. No test reaches the network: the join and the parsers are pure,
the real provider is exercised against a stub HTTP server on a throwaway port, and the orchestrator
path runs on the injected fake.
"""
from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import (
    DominoResourceProvider,
    FakeResourceProvider,
    LlmAlias,
    ResourceUnavailable,
    accessible_ids,
    join_aliases,
    parse_capabilities,
    parse_costs,
    records_of,
)
from sage.router.models import ModelCatalog

# One record in the shape the live gateway returns, trimmed to the fields a picker reads.
REGISTERED = [
    {"id": "abc", "name": "sonnet", "display_name": "Claude Sonnet 4.6", "description": "",
     "capabilities": ["chat", "responses", "tools", "streaming"],
     "effective_costs": {"input": 3.0, "output": 15.0}},
    {"id": "def", "name": "gpt-5.4-nano", "display_name": "gpt-5.4-nano", "description": "small",
     "capabilities": ["chat"], "effective_costs": {"input": 1.0, "output": 2.0}},
]


# ---- the join: only what the caller may actually use ---------------------------------------------


def test_a_registration_without_a_grant_is_not_offered():
    # The whole point of two calls: 12 registered, 6 accessible on the gateway this mirrors.
    out = join_aliases({"sonnet"}, REGISTERED)
    assert [a.name for a in out] == ["sonnet"]


def test_the_offered_row_carries_name_capabilities_and_cost():
    (a,) = join_aliases({"sonnet"}, REGISTERED)
    assert a.display_name == "Claude Sonnet 4.6"  # the row's primary identifier
    assert a.name == "sonnet"  # what request["model"] must say
    assert a.capabilities == ["chat", "responses", "tools", "streaming"]
    assert a.costs == {"input": 3.0, "output": 15.0}
    assert a.description is None  # "" is absent, not a description


def test_the_join_also_matches_on_the_record_id():
    # /v1/models reports the alias name on every gateway seen so far, but the control plane keys on
    # the id and the recipe joins on either.
    (a,) = join_aliases({"def"}, REGISTERED)
    assert a.name == "gpt-5.4-nano"


def test_an_accessible_model_with_no_record_still_gets_a_row():
    # Denying a model the caller CAN call would be the worse error: /v1/models is the authority on
    # availability, so a thin row beats a missing one.
    names = [a.name for a in join_aliases({"sonnet", "mystery"}, REGISTERED)]
    assert names == ["sonnet", "mystery"]
    thin = join_aliases({"mystery"}, REGISTERED)[0]
    assert thin.display_name == "mystery" and thin.capabilities == [] and thin.costs == {}


def test_a_registration_missing_a_display_name_falls_back_to_its_alias_name():
    (a,) = join_aliases({"bare"}, [{"id": "x", "name": "bare"}])
    assert a.display_name == "bare"


def test_no_grants_means_no_rows_rather_than_every_registration():
    assert join_aliases(set(), REGISTERED) == []


# ---- payload shapes -----------------------------------------------------------------------------


def test_records_come_out_of_a_bare_array_a_data_key_or_an_items_key():
    # /api/aliases returned a bare array; /v1/models follows the OpenAI {"data": [...]} convention.
    assert records_of([{"id": "a"}]) == [{"id": "a"}]
    assert records_of({"object": "list", "data": [{"id": "a"}]}) == [{"id": "a"}]
    assert records_of({"items": [{"id": "a"}]}) == [{"id": "a"}]
    assert records_of("a login page") == []
    assert records_of({"data": [{"id": "a"}, "junk"]}) == [{"id": "a"}]


def test_accessible_ids_reads_an_openai_model_listing():
    assert accessible_ids({"data": [{"id": "sonnet"}, {"id": "opus"}, {}]}) == {"sonnet", "opus"}


def test_capabilities_survive_a_shape_that_is_not_a_list():
    assert parse_capabilities(["chat", "tools", 3]) == ["chat", "tools"]
    # A bare string would iterate into one chip per character.
    assert parse_capabilities("chat") == []
    assert parse_capabilities(None) == []


def test_costs_keep_the_numbers_and_invent_nothing():
    assert parse_costs({"input": 3, "output": 15.0}) == {"input": 3.0, "output": 15.0}
    # A rate we cannot read is reported as absent rather than guessed at.
    assert parse_costs({"input": {"amount": 3}}) == {}
    assert parse_costs(None) == {}
    # bool is an int in Python — a flag must not price at 1.
    assert parse_costs({"cache_enabled": True, "input": 2.0}) == {"input": 2.0}


# ---- the real provider, against a stub gateway (no network) ---------------------------------------


@contextmanager
def stub_gateway(models: object, aliases: object, *, html_at: str | None = None):
    """A gateway that answers the two control-plane calls, recording what it was asked."""
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization", "")))
            if self.path == html_at:
                body, ctype = b"<html>keycloak login</html>", "text/html"
            else:
                payload = models if self.path.endswith("/v1/models") else aliases
                body, ctype = json.dumps(payload).encode(), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/v1", seen
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_the_provider_makes_both_calls_off_one_configured_url_and_intersects_them():
    models = {"object": "list", "data": [{"id": "sonnet"}]}
    with stub_gateway(models, REGISTERED) as (base, seen):
        out = DominoResourceProvider(base, lambda: "tok").list_llm_aliases()
    assert [a.display_name for a in out] == ["Claude Sonnet 4.6"]
    # The control plane sits at the OpenAI base's ROOT, so /v1 is stripped for /api/aliases.
    assert [p for p, _ in seen] == ["/v1/models", "/api/aliases"]
    assert {auth for _, auth in seen} == {"Bearer tok"}  # the existing bearer path, unchanged


def test_a_signed_out_gateway_is_an_error_and_not_an_empty_list():
    # Verified live: an unauthenticated call returns 200 carrying a Keycloak LOGIN PAGE, so status is
    # not proof of an answer. Reporting "no models" here would blame the user's permissions.
    with stub_gateway({"data": []}, REGISTERED, html_at="/v1/models") as (base, _), \
            pytest.raises(ResourceUnavailable, match="non-JSON"):
        DominoResourceProvider(base, lambda: "tok").list_llm_aliases()


def test_an_unreachable_gateway_is_reported_rather_than_raised_raw():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing is listening there now
    with pytest.raises(ResourceUnavailable, match="didn't answer"):
        DominoResourceProvider(f"http://127.0.0.1:{port}/v1", lambda: "tok", timeout_s=1.0).list_llm_aliases()


def test_the_error_message_never_carries_the_token():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(ResourceUnavailable) as e:
        DominoResourceProvider(f"http://127.0.0.1:{port}/v1", lambda: "dgw_supersecret", timeout_s=1.0).list_llm_aliases()
    assert "supersecret" not in str(e.value)


# ---- through the orchestrator, on the injected fake ----------------------------------------------


def _orch(tmp_path: Path, resources=None) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp_path / "ws",
        template=tmp_path / "template",
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        resources=resources,
    )


def test_the_orchestrator_lists_aliases_from_the_injected_provider(tmp_path: Path):
    rows = _orch(tmp_path, FakeResourceProvider([
        LlmAlias("x", "sonnet", "Claude Sonnet 4.6", "desc", ["chat"], {"input": 3.0}),
    ])).list_llm_aliases()
    assert rows == [{
        "id": "x", "name": "sonnet", "display_name": "Claude Sonnet 4.6",
        "description": "desc", "capabilities": ["chat"], "costs": {"input": 3.0},
    }]


def test_the_default_provider_is_the_fake_so_a_local_run_lists_something(tmp_path: Path):
    rows = _orch(tmp_path).list_llm_aliases()
    assert rows and all(r["display_name"] and r["capabilities"] for r in rows)


def test_the_route_returns_the_aliases_and_a_502_when_the_gateway_will_not_answer(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path, FakeResourceProvider()))
    client = TestClient(appmod.control_app)
    body = client.get("/api/resources").json()
    assert [a["name"] for a in body["llm_aliases"]][:1] == ["gpt-5.4"]

    class Broken:
        def list_llm_aliases(self):
            raise ResourceUnavailable("The LLM Gateway answered 503 at /v1/models.")

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path, Broken()))
    bad = client.get("/api/resources")
    # A 502 with a reason, not 200 with an empty list: "no models" would blame the wrong thing.
    assert bad.status_code == 502 and "503" in bad.json()["error"]
