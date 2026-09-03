"""Resources module: LLM Alias listing (#5).

The listing is the INTERSECTION of two gateway calls, so most of what matters here is the join and
the shapes each call can arrive in. No test reaches the network: the join and the parsers are pure,
the real provider is exercised against a stub HTTP server on a throwaway port, and the orchestrator
path runs on the injected fake.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import (
    SQL_DIALECTS,
    DataSource,
    DominoResourceProvider,
    FakeResourceProvider,
    LlmAlias,
    ModelApi,
    ResourceUnavailable,
    accessible_ids,
    cascade_levels,
    dialect_for,
    join_aliases,
    merge_readiness,
    name_column,
    parse_capabilities,
    parse_costs,
    parse_data_sources,
    parse_endpoints,
    parse_model_apis,
    parse_reasoning_efforts,
    readable_error,
    records_of,
    safe_identifier,
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


def test_gpt54_advertises_reasoning_effort_when_the_alias_record_is_silent():
    (a,) = join_aliases({"gpt-5.4"}, [])
    assert a.reasoning_efforts == ["low", "medium", "high"]
    (nano,) = join_aliases({"gpt-5.4-nano"}, REGISTERED)
    assert nano.reasoning_efforts == ["low", "medium", "high"]
    (sonnet,) = join_aliases({"sonnet"}, REGISTERED)
    assert sonnet.reasoning_efforts == []


def test_inference_params_win_over_the_name_heuristic():
    rec = {
        "id": "x", "name": "gpt-5.4", "display_name": "GPT",
        "inference_params": {"reasoning_effort": ["low", "high"]},
    }
    (a,) = join_aliases({"gpt-5.4"}, [rec])
    assert a.reasoning_efforts == ["low", "high"]
    assert parse_reasoning_efforts({"reasoning_effort": {"enum": ["low", "medium"]}}) == ["low", "medium"]


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
    t = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
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


# Nothing can ever listen here, so a connection to it refuses instantly. Port 1 is privileged (no
# unprivileged test can bind it) and sits far below the ephemeral range that `bind(port 0)` draws
# from, so no parallel xdist worker can land on it. Binding port 0 and closing it left a window
# where another worker claimed the freed port and the connection unexpectedly succeeded.
_DEAD_PORT = 1


def test_an_unreachable_gateway_is_reported_rather_than_raised_raw():
    with pytest.raises(ResourceUnavailable, match="didn't answer"):
        DominoResourceProvider(f"http://127.0.0.1:{_DEAD_PORT}/v1", lambda: "tok", timeout_s=1.0).list_llm_aliases()


def test_the_error_message_never_carries_the_token():
    with pytest.raises(ResourceUnavailable) as e:
        DominoResourceProvider(f"http://127.0.0.1:{_DEAD_PORT}/v1", lambda: "dgw_supersecret", timeout_s=1.0).list_llm_aliases()
    assert "supersecret" not in str(e.value)


# ---- Model APIs: the parse, and the project scope it cannot be asked without (#8) ----------------

# One record in the shape the live listing returns, trimmed to the fields the picker reads.
MODEL_APIS = {
    "items": [
        {"id": "m1", "name": "churn-risk", "description": "Scores cancellation risk.",
         "isArchived": False,
         "activeVersion": {"id": "v9", "number": 3, "deployment": {"status": "Running", "isPending": False}}},
        {"id": "m2", "name": "demand-forecast", "description": "", "isArchived": False,
         "activeVersion": {"id": "v2", "number": 1, "deployment": {"status": "Stopped", "isPending": False}}},
        {"id": "m3", "name": "never-deployed", "description": "", "isArchived": False},
        {"id": "m4", "name": "retired-scorer", "description": "", "isArchived": True,
         "activeVersion": {"id": "v1", "number": 1, "deployment": {"status": "Stopped", "isPending": False}}},
    ],
    "metadata": {"pagination": {"offset": 0, "limit": 100, "totalCount": 4}, "requestId": "r", "notices": []},
}


def test_the_row_carries_the_name_as_its_identifier_and_the_deployment_status():
    rows = parse_model_apis(MODEL_APIS)
    assert [m.name for m in rows[:2]] == ["churn-risk", "demand-forecast"]
    assert rows[0] == ModelApi("m1", "churn-risk", "Scores cancellation risk.", "Running")
    # Domino's own word, not a boolean: "Stopped" and "no version at all" are different situations
    # and a creator reading the row should be able to tell which one they are looking at.
    assert rows[1].status == "Stopped" and rows[1].description is None
    assert rows[2].status is None


def test_an_archived_model_api_is_not_offered():
    # Archiving is how a Model API is retired, so it is not a disabled-but-relevant Resource — it is
    # one nobody would pick, and it would only make the live ones harder to find.
    assert "retired-scorer" not in [m.name for m in parse_model_apis(MODEL_APIS)]


def test_a_record_with_no_name_is_dropped():
    # The name IS the row's identifier here, so a nameless row is not one a creator could pick.
    assert parse_model_apis({"items": [{"id": "m9", "name": ""}]}) == []


def test_model_apis_are_unlistable_rather_than_empty_when_there_is_no_domino_api():
    # The unscoped listing 403s ("not authorized to view access configuration") because it is an
    # admin surface. Off a Domino API Sage cannot fan out either, so it must not report "none".
    off_domino = DominoResourceProvider("http://gw/v1", lambda: "tok")
    with pytest.raises(ResourceUnavailable, match="not running in one"):
        off_domino.list_model_apis("proj-1")
    with pytest.raises(ResourceUnavailable, match="not running in one"):
        off_domino.list_model_apis(None)


@contextmanager
def stub_domino_api(pages: list[object], *, user: object = None, projects: object = None):
    """A Domino API that answers the Model API listing, handing out `pages` in order and recording
    every path it was asked for (query string included).

    `pages` are for the LISTING only, and are consumed in listing order however many other calls the
    provider makes around them. The project fan-out (#42) asks two more questions first — who is
    calling, and which projects do they belong to — and those are answered from `user` and
    `projects`. Both default to a 404, which is a Domino that will not say: the fan-out then collapses
    to the builder's own project, which is the shape every test written before #42 assumes.

    Answering by path rather than by call order for exactly that reason. Order made the stub read as
    a script of the provider's internals, so adding one call to the provider rewrote every test that
    never cared about it.
    """
    seen: list[str] = []
    listings: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if self.path.startswith("/api/users/v1/self"):
                payload = user
            elif self.path.startswith("/api/projects/beta/projects"):
                payload = projects
            else:
                listings.append(self.path)
                payload = pages[min(len(listings) - 1, len(pages) - 1)] if pages else None
            if payload is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", listings
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_the_provider_scopes_the_listing_to_the_project_it_was_asked_about():
    with stub_domino_api([MODEL_APIS]) as (api_host, seen):
        out = DominoResourceProvider(
            "http://gw/v1", lambda: "gwtok", api_host=api_host, api_token_provider=lambda: "apitok"
        ).list_model_apis("proj-1")
    assert [m.name for m in out] == ["churn-risk", "demand-forecast", "never-deployed"]
    assert seen[0].startswith("/api/modelServing/v1/modelApis?")
    assert "projectId=proj-1" in seen[0]


def test_a_project_with_more_model_apis_than_one_page_is_listed_whole():
    # A silently truncated list reads as "that is all of them", which is the one thing it is not.
    def page(names, total):
        return {"items": [{"id": n, "name": n} for n in names],
                "metadata": {"pagination": {"offset": 0, "limit": 100, "totalCount": total}}}

    with stub_domino_api([page(["a"], 2), page(["b"], 2), page([], 2)]) as (api_host, seen):
        p = DominoResourceProvider("http://gw/v1", lambda: "t", api_host=api_host)
        p._PAGE = 1
        assert [m.name for m in p.list_model_apis("proj-1")] == ["a", "b"]
    assert "offset=1" in seen[1]


# ---- the project fan-out (#42) -----------------------------------------------------------------
# A Model API deployed in another project used to be invisible AND unbindable, so a creator who could
# call the model from a terminal had no way to tell Sage about it. The listing now asks once per
# project the caller belongs to, and binding stopped consulting the listing at all.

_ME = {"user": {"id": "u-me", "userName": "subir"}}


def _projects(*records: dict) -> dict:
    return {"projects": list(records),
            "metadata": {"pagination": {"offset": 0, "limit": 100, "totalCount": len(records)}}}


def _model_apis(*names: str) -> dict:
    # `totalCount` so the pager stops after one page. The stub repeats its last response forever, so
    # a payload without it would spin to `_MAX_PAGES` and hide what the test is measuring.
    return {"items": [{"id": n, "name": n} for n in names],
            "metadata": {"pagination": {"offset": 0, "limit": 100, "totalCount": len(names)}}}


def test_the_listing_spans_the_projects_this_caller_belongs_to_and_names_each_row():
    projects = _projects(
        {"id": "proj-1", "name": "test-ds", "ownerId": "u-me"},
        {"id": "proj-2", "name": "Sage", "ownerId": "u-else",
         "collaborators": [{"id": "u-me", "role": "Contributor"}]},
    )
    with stub_domino_api([_model_apis("churn"), _model_apis("priority")],
                         user=_ME, projects=projects) as (api_host, listings):
        out = DominoResourceProvider(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t",
        ).list_model_apis("proj-1")

    # Home first, and blank — the rail is already in that project's context.
    assert [(m.name, m.project_name) for m in out] == [("churn", ""), ("priority", "Sage")]
    assert "projectId=proj-1" in listings[0] and "projectId=proj-2" in listings[1]


def test_off_domino_the_listing_fans_out_over_membership_with_no_home_project():
    projects = _projects(
        {"id": "proj-1", "name": "test-ds", "ownerId": "u-me"},
        {"id": "proj-2", "name": "Sage", "ownerId": "u-me"},
    )
    with stub_domino_api([_model_apis("churn"), _model_apis("priority")],
                         user=_ME, projects=projects) as (api_host, listings):
        out = DominoResourceProvider(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t",
        ).list_model_apis(None)

    assert {m.name for m in out} == {"churn", "priority"}
    assert any("projectId=proj-1" in path for path in listings)
    assert any("projectId=proj-2" in path for path in listings)


def test_a_project_the_caller_only_has_visibility_on_is_never_asked_about():
    # "Projects visible to user" can mean every public project on a demo deployment. Fanning out over
    # those would be a slow rail full of models the creator holds no token for.
    projects = _projects(
        {"id": "proj-1", "name": "test-ds", "ownerId": "u-me"},
        {"id": "proj-public", "name": "Somebody else's", "ownerId": "u-else",
         "collaborators": [{"id": "u-other", "role": "Owner"}]},
    )
    with stub_domino_api([_model_apis("churn")], user=_ME, projects=projects) as (api_host, listings):
        out = DominoResourceProvider(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t",
        ).list_model_apis("proj-1")

    assert [m.name for m in out] == ["churn"]
    assert len(listings) == 1 and "projectId=proj-1" in listings[0]


def test_one_model_bound_into_two_projects_is_offered_once():
    projects = _projects(
        {"id": "proj-1", "name": "test-ds", "ownerId": "u-me"},
        {"id": "proj-2", "name": "Sage", "ownerId": "u-me"},
    )
    with stub_domino_api([_model_apis("churn"), _model_apis("churn")],
                         user=_ME, projects=projects) as (api_host, _):
        out = DominoResourceProvider(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t",
        ).list_model_apis("proj-1")

    # First writer wins, so the row keeps the home project's blank label rather than gaining one.
    assert [(m.id, m.project_name) for m in out] == [("churn", "")]


def test_a_domino_that_will_not_say_who_is_calling_still_lists_the_builders_own_project():
    # Degrading to the pre-#42 answer, not to an error: a rail listing one project beats a rail
    # listing a failure, and the fan-out is an improvement on that answer rather than a condition of
    # it. `user` defaults to a 404 here.
    with stub_domino_api([_model_apis("churn")]) as (api_host, listings):
        out = DominoResourceProvider(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t",
        ).list_model_apis("proj-1")

    assert [m.name for m in out] == ["churn"]
    assert len(listings) == 1


def test_the_home_projects_failure_is_fatal_and_another_projects_is_not():
    # Asymmetric on purpose. A creator whose own project will not list is looking at something broken
    # and has to be told; one odd grant elsewhere on the tenant must not empty a rail that would
    # otherwise have answered.
    projects = _projects(
        {"id": "proj-1", "name": "test-ds", "ownerId": "u-me"},
        {"id": "proj-2", "name": "Sage", "ownerId": "u-me"},
    )
    calls: list[str] = []

    class Provider(DominoResourceProvider):
        def _model_apis_in(self, project_id, project_name):
            calls.append(project_id)
            if project_id == "proj-2":
                raise ResourceUnavailable("The Domino API answered 503.", 503)
            return super()._model_apis_in(project_id, project_name)

    with stub_domino_api([_model_apis("churn")], user=_ME, projects=projects) as (api_host, _):
        p = Provider("http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t")
        assert [m.name for m in p.list_model_apis("proj-1")] == ["churn"]
        assert calls == ["proj-1", "proj-2"]

        class HomeBroken(Provider):
            def _model_apis_in(self, project_id, project_name):
                raise ResourceUnavailable("The Domino API answered 503.", 503)

        broken = HomeBroken(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t")
        with pytest.raises(ResourceUnavailable):
            broken.list_model_apis("proj-1")


def test_one_model_is_readable_by_id_whatever_project_it_lives_in():
    # The question binding actually asks. The project listing only ever approximated it, and got it
    # wrong for every model deployed somewhere else.
    with stub_domino_api([{"id": "m-x", "name": "churn", "activeVersion": {"deployment": {
        "status": "Running"}}}]) as (api_host, listings):
        found = DominoResourceProvider(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t",
        ).get_model_api("m-x")

    assert found is not None and (found.name, found.status) == ("churn", "Running")
    assert listings[0] == "/api/modelServing/v1/modelApis/m-x"


def test_a_model_domino_refuses_to_describe_is_none_but_a_domino_that_is_down_is_raised():
    # A refusal is an answer — the creator cannot read it, and an access token may still prove they
    # can call it. A 503 is not, and saying "that model is not yours" would send them to look for a
    # permission problem that is not there.
    with stub_domino_api([]) as (api_host, _):   # every path 404s
        p = DominoResourceProvider(
            "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t")
        assert p.get_model_api("m-gone") is None

    class Down(DominoResourceProvider):
        def _domino_get(self, path, params=None):
            raise ResourceUnavailable("The Domino API answered 503.", 503)

    with pytest.raises(ResourceUnavailable):
        Down("http://gw/v1", lambda: "t", api_host="http://api").get_model_api("m-x")


# ---- Data Sources: the allowlist, and readiness asked rather than inferred (#10) -----------------

# The shape `/api/datasource/v1/datasources` returned live on cloud-dogfood, trimmed to the fields the
# panel reads, plus the kinds that have to be filtered out. The two Snowflake sources really did both
# report `displayName: "Snowflake"` — that is the case the primary-identifier rule exists for.
DATA_SOURCES = {
    "dataSources": [
        {"id": "d1", "name": "Snowflake-Data-Warehouse", "displayName": "Snowflake",
         "dataSourceType": "SnowflakeConfig", "authType": "KeyPair", "credentialType": "Shared",
         "description": "The company warehouse."},

        {"id": "d2", "name": "test", "displayName": "Snowflake",
         "dataSourceType": "SnowflakeConfig", "authType": "Basic", "credentialType": "Individual",
         "description": ""},

        {"id": "d3", "name": "AWS_MSSQL", "displayName": "SQL Server",
         "dataSourceType": "SQLServerConfig", "authType": "Basic", "credentialType": "Individual"},

        # Mount-shaped, and 16 of the 22 rows live were these two kinds.
        {"id": "d4", "name": "shared-files", "displayName": "Dataset",
         "dataSourceType": "DatasetConfig", "credentialType": "Shared"},
        {"id": "d5", "name": "netapp-vol", "displayName": "NetApp Volume",
         "dataSourceType": "NetAppVolumeConfig", "credentialType": "Shared"},

        # Right family, wrong question: a key lookup and a similarity search, not a query.
        {"id": "d6", "name": "raw-bucket", "displayName": "Amazon S3",
         "dataSourceType": "S3Config", "credentialType": "Shared"},
        {"id": "d7", "name": "embeddings", "displayName": "Pinecone",
         "dataSourceType": "PineconeConfig", "credentialType": "Shared"},

        # A connector Domino ships after this code was written.
        {"id": "d8", "name": "brand-new", "displayName": "Some New Store",
         "dataSourceType": "SomeNewStoreConfig", "credentialType": "Shared"},
    ],
    "metadata": {"pagination": {"offset": 0, "limit": 100, "totalCount": 8}, "requestId": "r",
                 "notices": []},
}

OFFERED = ["Snowflake-Data-Warehouse", "test", "AWS_MSSQL"]


def test_only_sql_and_warehouse_connectors_are_offered():
    # One access path, one shape: query in, table out. Everything downstream of picking a Data
    # Source assumes it, so a connector that cannot answer a query is not a row this panel can offer.
    assert [d.name for d in parse_data_sources(DATA_SOURCES)] == OFFERED


def test_an_unknown_connector_type_is_hidden_rather_than_shown():
    # The reason the filter is an allowlist. Domino's enum already carries 33 values and grows
    # without asking us; a row Sage cannot render is worse than a row Sage does not mention.
    assert "brand-new" not in [d.name for d in parse_data_sources(DATA_SOURCES)]
    assert parse_data_sources({"dataSources": [{"id": "x", "name": "x",
                                                "dataSourceType": "SomeNewStoreConfig"}]}) == []


def test_mount_shaped_sources_are_left_to_the_assets_panel():
    # Already surfaced as Assets. Listing them here would show one thing twice under two mental
    # models, and the mount half of the pair has no query at all.
    offered = [d.name for d in parse_data_sources(DATA_SOURCES)]
    assert "shared-files" not in offered and "netapp-vol" not in offered


def test_object_stores_and_vector_databases_are_not_offered():
    offered = [d.name for d in parse_data_sources(DATA_SOURCES)]
    assert "raw-bucket" not in offered and "embeddings" not in offered


def test_the_row_is_identified_by_its_own_name_not_its_connector_type():
    # Both Snowflake sources report displayName "Snowflake" (verified live). Reversing these two
    # fields renders two rows a creator cannot tell apart, which is the whole panel wasted.
    rows = parse_data_sources(DATA_SOURCES)
    assert [d.name for d in rows[:2]] == ["Snowflake-Data-Warehouse", "test"]
    assert [d.connector for d in rows[:2]] == ["Snowflake", "Snowflake"]
    # An empty description stays absent rather than becoming "", so the row renders one line not two.
    assert rows[0].description == "The company warehouse."
    assert rows[1].description is None


def test_the_connector_label_falls_back_to_the_type_with_its_suffix_dropped():
    (d,) = parse_data_sources({"dataSources": [
        {"id": "x", "name": "x", "dataSourceType": "PostgreSQLConfig", "credentialType": "Shared"}]})
    assert d.connector == "PostgreSQL"


def test_a_source_with_no_name_is_dropped():
    # The name is the only field that tells two sources of one connector apart.
    assert parse_data_sources({"dataSources": [
        {"id": "x", "name": "", "dataSourceType": "SnowflakeConfig"}]}) == []


def test_readiness_is_what_domino_says_and_not_what_the_credential_type_implies():
    # `test` and `AWS_MSSQL` are both Individual-credential sources, and Domino reports one openable
    # and the other not. Inferring readiness from the credential type would have called them the
    # same, which is the guess this second call exists to replace.
    rows = merge_readiness(parse_data_sources(DATA_SOURCES), [True, False, True])
    assert [(d.name, d.ready) for d in rows] == [
        ("Snowflake-Data-Warehouse", True), ("test", False), ("AWS_MSSQL", True)]
    assert rows[2].credential_type == "Individual"


def test_a_readiness_answer_of_the_wrong_shape_leaves_every_row_undecided():
    # The endpoint answers a bare boolean array — position is the only thing tying an answer to a
    # source. A row called unusable because a boolean slid by one is worse than one that admits
    # Domino did not say, so a mismatch decides nothing.
    rows = parse_data_sources(DATA_SOURCES)
    for answer in ([True, False], None, {"statuses": [True, True, True]}, "true"):
        assert [d.ready for d in merge_readiness(rows, answer)] == [None, None, None]


def test_undecided_is_not_the_same_as_unready():
    # Three states, not two: the rail says "cannot open this" and "cannot tell" differently.
    (unready,) = merge_readiness(parse_data_sources(
        {"dataSources": [DATA_SOURCES["dataSources"][1]]}), [False])
    assert unready.ready is False
    assert merge_readiness([unready], None)[0].ready is False


@contextmanager
def stub_data_source_api(*, listing: list[object], readiness: object, readiness_status: int = 200):
    """A Domino API answering both halves of the Data Source listing, recording what it was asked.

    Routed by method because these two are a GET and a POST to different paths — `stub_domino_api`
    above only speaks GET.
    """
    seen: list[tuple[str, str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: object):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            gets = [s for s in seen if s[0] == "GET"]
            seen.append(("GET", self.path, None))
            self._reply(200, listing[min(len(gets), len(listing) - 1)])

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append(("POST", self.path, json.loads(raw or b"{}")))
            self._reply(readiness_status, readiness)

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


def test_the_provider_asks_the_permission_keyed_listing_and_then_asks_about_readiness():
    with stub_data_source_api(listing=[DATA_SOURCES], readiness=[True, False, True]) as (host, seen):
        out = DominoResourceProvider("http://gw/v1", lambda: "t", api_host=host).list_data_sources()
    assert [(d.name, d.ready) for d in out] == [
        ("Snowflake-Data-Warehouse", True), ("test", False), ("AWS_MSSQL", True)]
    # The public, permission-keyed listing — not `/v4/datasource/projects/{id}`, which answered
    # `200 []` live for a user with a working Snowflake source, and not
    # `/v4/datasource/dataSources/all`, which answered 403 because it wants an admin grant.
    assert seen[0][1].startswith("/api/datasource/v1/datasources?")
    assert "projectId" not in seen[0][1]
    # Readiness is asked about exactly the rows the panel will draw, in the order it will draw them,
    # because the answer carries no ids of its own.
    assert seen[1][0] == "POST" and seen[1][1] == "/v4/datasource/authentication-status"
    assert seen[1][2] == {"dataSourceIds": ["d1", "d2", "d3"]}


def test_a_failed_readiness_call_still_lists_the_sources():
    # The listing already succeeded. Hiding sources the creator can see in Domino because a private
    # endpoint 500'd would be the empty-panel dead end this listing was chosen to avoid — so the
    # rows stay and say readiness is unknown.
    with stub_data_source_api(listing=[DATA_SOURCES], readiness={"error": "nope"},
                              readiness_status=500) as (host, _):
        out = DominoResourceProvider("http://gw/v1", lambda: "t", api_host=host).list_data_sources()
    assert [d.name for d in out] == OFFERED
    assert [d.ready for d in out] == [None, None, None]


def test_a_page_of_nothing_but_filtered_out_kinds_does_not_end_the_listing():
    # Pagination counts the raw page, not the rows that survived the allowlist. On the live
    # deployment 16 of 22 sources were dataset-backed, so a page of only those is the ordinary case —
    # stopping there would hide every SQL source that came after it.
    def page(records, total):
        return {"dataSources": records,
                "metadata": {"pagination": {"offset": 0, "limit": 100, "totalCount": total}}}

    mounts = [{"id": f"m{i}", "name": f"m{i}", "dataSourceType": "DatasetConfig"} for i in range(2)]
    sql = [{"id": "s1", "name": "warehouse", "dataSourceType": "SnowflakeConfig",
            "credentialType": "Shared"}]
    with stub_data_source_api(listing=[page(mounts, 3), page(sql, 3), page([], 3)],
                              readiness=[True]) as (host, seen):
        p = DominoResourceProvider("http://gw/v1", lambda: "t", api_host=host)
        p._PAGE = 2
        assert [d.name for d in p.list_data_sources()] == ["warehouse"]
    assert "offset=2" in seen[1][1]


def test_data_sources_are_unlistable_rather_than_empty_off_domino():
    # "Sage could not ask" and "you have none" send the creator to different places, and only one of
    # them is true here.
    p = DominoResourceProvider("http://gw/v1", lambda: "tok")
    with pytest.raises(ResourceUnavailable, match="not configured to reach one"):
        p.list_data_sources()


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
        "reasoning_efforts": [],
    }]


def test_the_default_provider_is_the_fake_so_a_local_run_lists_something(tmp_path: Path):
    rows = _orch(tmp_path).list_llm_aliases()
    assert rows and all(r["display_name"] and r["capabilities"] for r in rows)
    gpt = next(r for r in rows if r["name"] == "gpt-5.4")
    assert gpt["reasoning_efforts"] == ["low", "medium", "high"]
    embed = next(r for r in rows if "embed" in r["name"])
    assert embed["reasoning_efforts"] == []


def test_the_orchestrator_hands_its_own_project_down_as_the_home_of_the_listing(tmp_path: Path):
    asked: list[str | None] = []

    class Recording(FakeResourceProvider):
        def list_model_apis(self, project_id):
            asked.append(project_id)
            return [
                ModelApi("m1", "churn-risk", "Scores cancellation risk.", "Running"),
                ModelApi("m2", "churn-risk", None, "Running", project_name="Underwriting"),
            ]

    orch = _orch(tmp_path, Recording())
    orch._domino_project_id = "proj-1"
    # Two rows reading `churn-risk`, told apart by the project — the whole reason the field is
    # carried (#42). Blank on the home one: a label every row wears says nothing, and would bury the
    # one row where it says something.
    assert orch.list_model_apis() == [
        {"id": "m1", "name": "churn-risk", "description": "Scores cancellation risk.",
         "status": "Running", "project": ""},
        {"id": "m2", "name": "churn-risk", "description": None, "status": "Running",
         "project": "Underwriting"},
    ]
    # The project is still not optional: the deployment-wide listing needs an admin role a Sage user
    # has not got, so the fan-out needs a project to start from rather than a call it can skip.
    assert asked == ["proj-1"]


def test_the_orchestrator_lists_data_sources_without_scoping_them_to_its_project(tmp_path: Path):
    # No project argument at all, unlike the two listings above, and that asymmetry is the finding:
    # a Data Source is permission-scoped to the person. The provider's method takes none, so a
    # regression that reintroduces project scoping cannot even be expressed here.
    rows = _orch(tmp_path, FakeResourceProvider(
        data_sources=[DataSource("d1", "warehouse", "Snowflake", "Shared", "desc", False,
                                 connector_type="SnowflakeConfig")],
    )).list_data_sources()
    assert rows == [{
        "id": "d1", "name": "warehouse", "connector": "Snowflake",
        "credential_type": "Shared", "description": "desc", "ready": False,
        "levels": ["database", "schema", "table"], "default_database": None, "default_schema": None,
    }]


def test_the_route_carries_both_kinds_and_one_failing_service_does_not_blank_the_other(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path, FakeResourceProvider()))
    client = TestClient(appmod.control_app)
    body = client.get("/api/resources").json()
    assert [a["name"] for a in body["llm_aliases"]][:1] == ["gpt-5.4"]

    assert [m["name"] for m in body["model_apis"]][:1] == ["churn-risk"]
    assert [d["name"] for d in body["data_sources"]][:1] == ["Snowflake-Data-Warehouse"]
    assert body["errors"] == {}

    class GatewayDown(FakeResourceProvider):
        def list_llm_aliases(self):
            raise ResourceUnavailable("The LLM Gateway answered 503 at /v1/models.")

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path, GatewayDown()))
    bad = client.get("/api/resources").json()
    # A reason, not an empty list: "no models" would blame the wrong thing. And the reason is carried
    # per kind — Model APIs come from a different Domino service, so they are still listed.
    assert bad["llm_aliases"] == [] and "503" in bad["errors"]["llm_aliases"]
    assert [m["name"] for m in bad["model_apis"]][:1] == ["churn-risk"]
    assert "model_apis" not in bad["errors"]
    assert [d["name"] for d in bad["data_sources"]][:1] == ["Snowflake-Data-Warehouse"]
    assert "data_sources" not in bad["errors"]


# ---- the cascade (#11) ---------------------------------------------------------------------------
#
# There is no Domino endpoint that enumerates a Data Source's contents, so the cascade is SQL sent
# through the source itself, and the shape of that SQL is per connector. Only Snowflake's has been
# run live. These tests therefore pin the two things that survive that uncertainty: which levels a
# connector is offered at all, and that a level Sage cannot ask about fails by name.


class _Frame:
    """The two attributes `name_column` touches, so the read path is testable without pandas — which
    is not installed in the backend venv and is not a dependency Sage may add: it arrives inside the
    Domino image alongside `domino_data`."""

    def __init__(self, columns: dict[str, list]):
        self.columns = list(columns)
        self._data = columns

    def __getitem__(self, key):
        return type("_Col", (), {"tolist": lambda s, v=self._data[key]: v})()


def _source(connector_type: str, **kw) -> DataSource:
    return DataSource("d", "src", connector_type.removesuffix("Config"), "Shared", None, True,
                      connector_type=connector_type, **kw)


def test_the_cascade_offers_only_the_levels_a_connector_actually_has():
    # Three cases that must stay distinguishable, because they send the creator somewhere different.
    assert cascade_levels(_source("SnowflakeConfig")) == ["database", "schema", "table"]
    # Postgres opens already inside one database. "No level above the schema" is a fact about the
    # connector, not a missing answer, so the cascade starts one level down rather than showing a
    # list of one.
    assert cascade_levels(_source("PostgreSQLConfig")) == ["schema", "table"]
    # And no dialect at all is empty, which the UI reads as "record it, do not offer a picker".
    assert cascade_levels(_source("OracleConfig")) == []


def test_a_connector_with_no_dialect_says_which_connector_rather_than_showing_nothing():
    # The failure the panel must not have is an expander that opens on an empty list, because that
    # reads as "this source holds nothing". Naming the connector is what makes it a fact.
    with pytest.raises(ResourceUnavailable, match="Oracle"):
        dialect_for(_source("OracleConfig"))


def test_the_introspection_statements_only_read():
    # Sage holds a shared service credential here, and one that can read the whole warehouse. Every
    # statement it can ever send is in this table, so the table is the place to assert that.
    banned = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT", "MERGE")
    for name, dialect in SQL_DIALECTS.items():
        for template in (dialect.databases, dialect.schemas, dialect.tables):
            if template is None:
                continue
            rendered = dialect.statement(template, database="DB", schema="s").upper()
            assert rendered.startswith(("SELECT", "SHOW")), f"{name}: {rendered}"
            for verb in banned:
                assert verb not in rendered, f"{name} sends {verb}"


def test_snowflakes_statements_are_the_ones_that_were_run_live():
    # Pinned literally because these three are the only introspection statements in the table with a
    # live run behind them (DATA-SOURCES-RESEARCH.md Addendum 2). A change here is a change to
    # verified behaviour and should have to be made on purpose.
    d = SQL_DIALECTS["SnowflakeConfig"]
    assert d.verified
    assert d.statement(d.databases) == "SHOW DATABASES"
    assert d.statement(d.schemas, database="DWH") == 'SHOW SCHEMAS IN DATABASE "DWH"'
    assert d.statement(d.tables, database="DWH", schema="MARTS") == (
        'SELECT TABLE_NAME AS name FROM "DWH".INFORMATION_SCHEMA.TABLES '
        "WHERE TABLE_SCHEMA = 'MARTS' ORDER BY TABLE_NAME")


def test_a_name_is_refused_rather_than_escaped():
    assert safe_identifier("FCT_USAGE_DAILY$1") == "FCT_USAGE_DAILY$1"
    # An allowlist, not an escape: the credential behind this SQL reads the whole warehouse, so the
    # bar is "could not be anything but a name", not "quoted correctly".
    for bad in ('DWH"; DROP TABLE x', "a b", "", "sch.ema", "x--", "naïve"):
        with pytest.raises(ValueError, match="letters, digits"):
            safe_identifier(bad)


def test_a_stores_failure_is_shown_without_carrying_a_credential():
    # `DataSourceClient.__repr__` prints its api_key in plaintext, so an exception that holds a client
    # would carry the key into the panel.
    leaked = readable_error(RuntimeError("connect failed for key=" + "A" * 64))
    assert "A" * 64 not in leaked and "[redacted]" in leaked
    # The store's own words still have to arrive — they are the whole reason an unverified dialect
    # fails honestly rather than looking like an empty schema.
    assert "SQL compilation error" in readable_error(RuntimeError("SQL compilation error"))
    # Redacted first, then cut — a cut that ran first would leave the front of a key showing.
    long = readable_error(RuntimeError(" ".join(["driver traceback line"] * 90)))
    assert long.startswith("RuntimeError: driver traceback") and len(long) < 350


def test_the_names_are_read_out_of_whatever_column_the_store_used():
    assert name_column(_Frame({"name": ["MARTS", "REPORTING"]})) == ["MARTS", "REPORTING"]
    # `AS name` comes back upper-cased from a folding store.
    assert name_column(_Frame({"NAME": ["a"], "kind": ["TABLE"]})) == ["a"]
    # And `SHOW CATALOGS` has no `name` column at all — it answers with `catalog`.
    assert name_column(_Frame({"catalog": ["hive", "tpch"]})) == ["hive", "tpch"]
    assert name_column(_Frame({})) == []


def test_a_data_sources_preconfigured_scope_is_read_when_it_is_there(tmp_path: Path):
    # Half the point of the criterion: a source that names no default database is not a broken
    # source, it is the ordinary case, and its cascade has to work identically.
    listed = _orch(tmp_path, FakeResourceProvider()).list_data_sources()
    rows = {d["name"]: d for d in listed}
    assert rows["AWS_MSSQL"]["default_database"] == "underwriting"
    assert rows["Snowflake-Data-Warehouse"]["default_database"] is None
    assert rows["Snowflake-Data-Warehouse"]["levels"] == ["database", "schema", "table"]
    assert rows["billing-oracle"]["levels"] == []


def test_a_source_with_no_default_database_still_cascades_end_to_end(tmp_path: Path):
    # The acceptance criterion, walked: nothing was preconfigured, and the creator still reaches a
    # table by opening one level at a time.
    orch = _orch(tmp_path, FakeResourceProvider())
    assert orch.list_data_source_databases("ds-dwh") == ["DWH", "SANDBOX"]
    assert orch.list_data_source_schemas("ds-dwh", "DWH") == ["MARTS", "REPORTING", "STAGING"]
    tables = orch.list_data_source_tables("ds-dwh", "DWH", "MARTS")
    assert "FCT_USAGE_DAILY" in tables
    # An empty schema is empty, not broken — and is reachable, which is how the creator can tell.
    assert orch.list_data_source_tables("ds-dwh", "DWH", "STAGING") == []


def test_a_two_level_store_is_asked_for_schemas_without_a_database(tmp_path: Path):
    orch = _orch(tmp_path, FakeResourceProvider())
    assert orch.list_data_source_databases("ds-reporting") == []
    assert orch.list_data_source_schemas("ds-reporting", "") == ["audit", "public"]
    assert orch.list_data_source_tables("ds-reporting", "", "public") == ["accounts", "events"]


def test_only_the_level_that_was_opened_is_asked_about(tmp_path: Path):
    # Each level is a query through Arrow Flight and costs seconds (2.3s / 3.5s / 2.9s live), so
    # prefetching the tree would spend a minute of the creator's time on branches never opened.
    asked: list[str] = []

    class Counting(FakeResourceProvider):
        def list_databases(self, source):
            asked.append("databases")
            return super().list_databases(source)

        def list_schemas(self, source, database):
            asked.append(f"schemas:{database}")
            return super().list_schemas(source, database)

        def list_tables(self, source, database, schema):
            asked.append(f"tables:{schema}")
            return super().list_tables(source, database, schema)

    orch = _orch(tmp_path, Counting())
    orch.list_data_source_databases("ds-dwh")
    assert asked == ["databases"]
    orch.list_data_source_schemas("ds-dwh", "DWH")
    assert asked == ["databases", "schemas:DWH"]
    orch.list_data_source_tables("ds-dwh", "DWH", "MARTS")
    assert asked == ["databases", "schemas:DWH", "tables:MARTS"]


def test_a_name_the_creator_did_not_pick_from_a_list_is_refused_at_the_orchestrator(tmp_path: Path):
    orch = _orch(tmp_path, FakeResourceProvider())
    with pytest.raises(ValueError):
        orch.list_data_source_schemas("ds-dwh", 'DWH"; DROP TABLE x')
    with pytest.raises(ValueError):
        orch.bind_data_source("ds-dwh", database="DWH", schema="a b")


def test_an_unknown_source_id_is_a_lookup_failure_not_an_empty_list(tmp_path: Path):
    orch = _orch(tmp_path, FakeResourceProvider())
    with pytest.raises(LookupError):
        orch.list_data_source_databases("ds-does-not-exist")


def test_the_cascade_cannot_look_inside_a_source_without_the_domino_data_library():
    # The library ships in the Domino image, not in Sage's venv, so this is the state a developer
    # running the backend on a laptop is in — and the message has to separate it from "empty".
    p = DominoResourceProvider("http://gw/v1", lambda: "tok")
    with pytest.raises(ResourceUnavailable, match="not installed here"):
        p.list_databases(_source("SnowflakeConfig"))


def test_the_cascade_routes_carry_one_level_each(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path, FakeResourceProvider()))
    client = TestClient(appmod.control_app)

    def get(path):
        r = client.get(path)
        return r.status_code, r.json()

    assert get("/api/data-sources/ds-dwh/databases") == (200, {"items": ["DWH", "SANDBOX"]})
    assert get("/api/data-sources/ds-dwh/schemas?database=DWH") == (
        200, {"items": ["MARTS", "REPORTING", "STAGING"]})
    ok, body = get("/api/data-sources/ds-dwh/tables?database=DWH&schema=MARTS")
    assert ok == 200 and "DIM_ACCOUNT" in body["items"]

    # The level is in the path rather than inferred from which query parameters arrived, so a
    # two-level store asking for schemas with no database is a request, not a guess.
    assert get("/api/data-sources/ds-reporting/schemas") == (200, {"items": ["audit", "public"]})

    assert get("/api/data-sources/nope/databases")[0] == 404
    assert get('/api/data-sources/ds-dwh/schemas?database=D";DROP')[0] == 400
    status, body = get("/api/data-sources/ds-oracle/databases")
    # 502 and not 404: the source exists and the creator can still record it. Only looking inside is
    # unavailable, and the reason names the connector.
    assert status == 502 and "Oracle" in body["error"]


# ---- Hosted GenAI Endpoints: the listing preflight joins an Alias to (#21) ------------------------
#
# Shapes taken from a live `GET /api/gen-ai/beta/endpoints` on cloud-dogfood (2026-08-21) and from
# `ModelEndpointsListingV1` in the public API spec. The two facts that matter and are easy to get
# wrong: `currentVersion` is OPTIONAL, and the join key is `url` rather than `id` or `vanityUrl`.

ENDPOINTS = {"items": [
    {"id": "308f788c", "name": "qwen-2-5", "vanityUrl": "https://apps.x.tech/endpoints/qwen-vanity",
     "url": "https://apps.x.tech/endpoints/308f788c",
     "currentVersion": {"number": 1, "status": "Running"}},
    {"id": "629c65ce", "name": "Mistral-7B-Instruct-v02",
     "url": "https://apps.x.tech/endpoints/629c65ce",
     "currentVersion": {"number": 3, "status": "Stopped"}},
    # Never built, so it has no currentVersion at all — the schema allows this and it occurs live.
    {"id": "777", "name": "never-built", "url": "https://apps.x.tech/endpoints/777"},
]}


def test_endpoints_are_parsed_with_their_status():
    rows = parse_endpoints(ENDPOINTS)
    assert [(e.name, e.status) for e in rows] == [
        ("qwen-2-5", "Running"), ("Mistral-7B-Instruct-v02", "Stopped"), ("never-built", None)]


def test_an_endpoint_with_no_current_version_has_no_status_rather_than_a_bad_one():
    (never_built,) = [e for e in parse_endpoints(ENDPOINTS) if e.name == "never-built"]
    assert never_built.status is None


def test_an_endpoint_with_no_url_is_dropped():
    # The url is the only thing an Alias can be joined on, so a row without one cannot answer the
    # only question this listing is fetched for.
    assert parse_endpoints({"items": [{"id": "x", "name": "no-url"}]}) == []


def test_a_trailing_slash_on_the_endpoint_url_is_normalised_away():
    (one,) = parse_endpoints({"items": [{"id": "x", "name": "n", "url": "https://apps.x.tech/e/1/"}]})
    assert one.url == "https://apps.x.tech/e/1"


def test_an_empty_listing_parses_to_nothing():
    assert parse_endpoints({"items": []}) == []
    assert parse_endpoints({}) == []


def test_an_alias_carries_the_endpoint_url_it_was_registered_with():
    # The join key, and it arrives in a call Sage already makes. Dropping it here was what made the
    # endpoint behind an Alias unknowable without a second lookup.
    records = [{"id": "id-qwen", "name": "qwen-2-5", "display_name": "Qwen 2.5",
                "endpoint_url": "https://apps.x.tech/endpoints/308f788c/v1"}]
    (alias,) = join_aliases({"qwen-2-5"}, records)
    assert alias.endpoint_url == "https://apps.x.tech/endpoints/308f788c/v1"


def test_a_vendor_alias_has_no_endpoint_url():
    # 12 of the 14 aliases on cloud-dogfood are this shape. None must be treated as a hosted endpoint.
    (alias,) = join_aliases({"sonnet"}, [{"id": "a", "name": "sonnet", "display_name": "Sonnet"}])
    assert alias.endpoint_url is None


# Collaborators used to be tested here, when they were one read for one caller. They are now two
# reads Domino disagrees on, with writes beside them, and they live in
# `test_a_collaborator_is_added_not_invited.py` — including the contract change ADR-0028 made to
# what an unreadable project record costs.
