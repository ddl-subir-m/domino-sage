"""The six Problems, composed once, server-side (ADR-0027).

Sage already knew all six before this existed and told the log. What is under test is the composing:
which conditions earn a sentence, which stay silent, who owns the remedy, and the two rules that
decide whether a Problem is said at all — the line on silence, and survival across two consecutive
Preflights.

Nothing here reaches the network. `health.py` is pure functions over already-fetched inputs, and the
route runs on the injected fake, subclassed to force each failure.
"""
from __future__ import annotations

from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources import health
from sage.resources.health import (
    OWNER_ADMIN,
    OWNER_YOU,
    Problem,
    agent_problem,
    binding_problems,
    data_library_problem,
    gateway_problem,
    port_problem,
    problems,
    slot_problems,
    survivors,
)
from sage.resources.provider import FakeResourceProvider, LlmAlias, ResourceUnavailable
from sage.router.models import ModelCatalog

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {}),
]

GOOD_CATALOG = ModelCatalog(
    sovereign_plan="qwen-2-5", sovereign_implement="qwen-2-5", sovereign_ask="qwen-2-5",
    plan="sonnet", implement="sonnet", ask="sonnet",
)

ALL_AGENTS = [{"name": name} for name in health.SAGE_AGENTS]

# What each of the five reads answers when nothing is wrong. Written out so a test naming one fault
# is a test where that fault is the only reason anything was reported.
CLEAN = {
    "slots": {"state": "ok", "error": None, "slots": [], "reached": True},
    "bindings": {"state": "ok", "error": None, "bindings": []},
    "ports": {"control_port": 8080, "base_port": 8080},
    "agents": ALL_AGENTS,
    "data_library": "",
}


def _ids(found: list[Problem]) -> list[str]:
    return [p.id for p in found]


# ---- the six, one at a time ----------------------------------------------------------------------


def test_a_dead_model_slot_is_the_creators_to_fix():
    # Two owners in truth — the creator picks another model, an administrator registers the Alias —
    # and ADR-0027 sorts it under the reader who has a control in front of them.
    slots = {"state": "problems", "error": None, "reached": True, "slots": [
        {"slot": "implement", "alias": "ghost-model",
         "message": "Sage's implement model is set to the LLM Alias ghost-model. Pick another.",
         "fault": "Sage's implement model is set to the LLM Alias ghost-model.",
         "fix": "Pick a different model for that slot."}]}
    (p,) = slot_problems(slots)
    assert (p.id, p.owner) == ("slot:implement", OWNER_YOU)
    # Carried through, not rewritten: one fault that reads two ways is how a person comes to believe
    # it is two faults. The fault half, because the drawer renders the remedy on its own line and
    # the joined `message` would say it twice.
    assert p.message == slots["slots"][0]["fault"]
    assert p.fix == slots["slots"][0]["fix"]
    assert p.body is None


def test_a_gateway_that_will_not_answer_is_the_administrators():
    slots = {"state": "unreachable", "error": "ConnectError: connection refused",
             "slots": [], "reached": False}
    p = gateway_problem(slots)
    assert (p.id, p.owner) == ("gateway", OWNER_ADMIN)
    # The platform's own words travel verbatim, for whoever has to debug it.
    assert p.body == "ConnectError: connection refused"
    assert "LLM Gateway" in p.message and "LLM Gateway" in p.fix


def test_a_stale_binding_is_the_creators_to_fix():
    bindings = {"state": "problems", "error": None, "bindings": [
        {"kind": "llm_alias", "id": "id-sonnet", "name": "sonnet",
         "display_name": "Claude Sonnet 4.6",
         "message": "This app is recorded as using the LLM Alias Claude Sonnet 4.6. Remove it.",
         "fault": "This app is recorded as using the LLM Alias Claude Sonnet 4.6.",
         "fix": "Remove it, or pick a different Alias, before you build on it."}]}
    (p,) = binding_problems(bindings)
    # Keyed on kind AND id: one Resource can be recorded under two kinds, and the toast must not
    # collapse them into one Problem.
    assert (p.id, p.owner) == ("binding:llm_alias:id-sonnet", OWNER_YOU)
    assert p.fix.startswith("Remove it")


def test_a_port_mismatch_is_the_administrators():
    p = port_problem({"control_port": 8080, "base_port": 8888})
    assert (p.id, p.owner) == ("ports", OWNER_ADMIN)
    assert "8080" in p.message and "8888" in p.message
    assert p.body is None


def test_a_missing_agent_is_reported_even_when_the_others_resolved():
    # Any one of the five missing means that mode ran the default build agent, so its permission
    # block never applied. Four out of five is not four fifths fine.
    p = agent_problem([{"name": n} for n in health.SAGE_AGENTS if n != "sage-ask"])
    assert (p.id, p.owner) == ("agents", OWNER_ADMIN)
    assert agent_problem(ALL_AGENTS) is None


def test_an_agent_row_keyed_the_other_way_still_resolves():
    # `agent_summaries` deliberately does not pin an identifier key: a `/api/agent` that answers an
    # object keyed by agent name arrives as `key`, not `name`. Reading one key would report all five
    # missing on a deployment where all five loaded.
    assert agent_problem([{"key": n} for n in health.SAGE_AGENTS]) is None
    assert agent_problem([{"id": n, "mode": "subagent"} for n in health.SAGE_AGENTS]) is None
    assert agent_problem([{"key": n} for n in health.SAGE_AGENTS if n != "sage-plan"]) is not None


def test_all_five_agents_are_checked_not_three():
    # The list is five — chat, ask, plan, architect, implement — and /api/diag said three for a while.
    assert health.SAGE_AGENTS == (
        "sage-chat", "sage-ask", "sage-plan", "sage-architect", "sage-implement")
    for missing in health.SAGE_AGENTS:
        rest = [{"name": n} for n in health.SAGE_AGENTS if n != missing]
        assert agent_problem(rest) is not None, missing


def test_an_unreadable_data_library_quotes_the_import_error():
    p = data_library_problem("ModuleNotFoundError: No module named 'domino_data'")
    assert (p.id, p.owner) == ("data-library", OWNER_ADMIN)
    assert p.body == "ModuleNotFoundError: No module named 'domino_data'"
    assert data_library_problem("") is None


def test_a_deployment_with_nothing_wrong_says_nothing():
    assert problems(**CLEAN) == []


def test_every_one_of_the_six_is_composed_by_one_call():
    found = problems(
        slots={"state": "problems", "error": "ConnectError", "reached": False, "slots": [
            {"slot": "ask", "alias": "ghost", "fault": "m", "fix": "f"}]},
        bindings={"bindings": [{"kind": "llm_alias", "id": "id-x", "fault": "m", "fix": "f"}]},
        ports={"control_port": 8080, "base_port": 8888},
        agents=[],
        data_library="ImportError: boom",
    )
    # The creator's own first: the drawer groups by owner, and a payload that arrives in the order it
    # will be read spares the client from deciding what "first" means.
    assert _ids(found) == ["slot:ask", "binding:llm_alias:id-x",
                          "gateway", "ports", "agents", "data-library"]
    assert [p.owner for p in found] == [OWNER_YOU, OWNER_YOU] + [OWNER_ADMIN] * 4


# ---- the line on silence -------------------------------------------------------------------------


def test_an_agent_list_that_was_never_read_reports_nothing():
    # None means the agent runner is not up yet or the query failed. Reporting that as "missing"
    # would light the chip on every boot.
    assert agent_problem(None) is None
    assert problems(**{**CLEAN, "agents": None}) == []


def test_a_gateway_that_answered_with_an_empty_model_list_reports_nothing():
    # A `/v1/models` that returns 200 with no usable rows is a permission-cache blip, not a broken
    # deployment — and "every Alias is missing" is a sentence with no remedy that could work.
    # `preflight_slots` reaches the gateway, finds no slot to fault, and says so.
    empty = {"state": "ok", "error": None, "slots": [], "reached": True}
    assert gateway_problem(empty) is None
    assert problems(**{**CLEAN, "slots": empty}) == []


def test_a_sub_listing_that_failed_behind_a_gateway_that_answered_stays_silent():
    # Since #21 `unreachable` is also the state when the slots resolved and only the endpoint
    # listing behind them failed. That is a sub-listing behind a dependency that answered, and
    # ADR-0027 leaves the old silence in place. `reached` is what tells the two apart.
    behind = {"state": "unreachable", "error": "endpoint listing failed",
              "slots": [], "reached": True}
    assert gateway_problem(behind) is None


def test_a_preflight_that_has_not_run_yet_reports_nothing():
    # `pending` at boot and `skipped` on a deployment with no gateway both arrive without `reached`.
    assert gateway_problem({"state": "pending", "error": None, "slots": []}) is None
    assert gateway_problem({"state": "skipped", "error": None, "slots": []}) is None


def test_a_configured_port_that_could_not_be_read_is_not_a_mismatch():
    # `match` is false there too — None never equals a port number — and reporting it would turn
    # "we could not check" into "this is broken".
    assert port_problem({"control_port": 8080, "base_port": None}) is None


# ---- survival: two consecutive Preflights --------------------------------------------------------


def test_a_problem_seen_once_is_not_reported_and_seen_twice_is():
    # Domino reports a workspace running before its proxy serves, so a boot Preflight sees faults
    # that clear themselves in seconds. A chip that lights for a blip becomes furniture.
    found = problems(**{**CLEAN, "data_library": "ImportError: boom"})
    first = survivors(set(), found)
    assert first == []
    second = survivors({p.id for p in found}, found)
    assert _ids(second) == ["data-library"]


def test_a_problem_that_cleared_in_between_starts_its_count_over():
    found = problems(**{**CLEAN, "data_library": "ImportError: boom"})
    # Preflight 1 saw it, Preflight 2 did not, Preflight 3 sees it again — consecutive, not a tally.
    after_clear = survivors({p.id for p in found}, [])
    assert after_clear == []
    assert survivors(set(), found) == []


def test_survival_is_counted_per_problem_not_per_preflight():
    # A second fault appearing must not carry a first-sighting one along with it.
    old = problems(**{**CLEAN, "data_library": "ImportError: boom"})
    both = problems(**{**CLEAN, "data_library": "ImportError: boom",
                       "ports": {"control_port": 8080, "base_port": 8888}})
    assert _ids(survivors({p.id for p in old}, both)) == ["data-library"]


# ---- the payload ---------------------------------------------------------------------------------


def test_a_problem_with_nothing_quoted_carries_no_empty_body():
    # Absent rather than empty: a reader is never shown a quotation with nothing in it.
    assert "body" not in port_problem({"control_port": 8080, "base_port": 8888}).to_dict()
    assert data_library_problem("boom").to_dict()["body"] == "boom"


def test_every_problem_carries_the_four_fields_the_client_renders():
    found = problems(
        slots={"reached": False, "error": "boom", "slots": [
            {"slot": "ask", "alias": "ghost", "fault": "m", "fix": "f"}]},
        bindings={"bindings": [{"kind": "llm_alias", "id": "id-x", "fault": "m", "fix": "f"}]},
        ports={"control_port": 8080, "base_port": 8888},
        agents=[],
        data_library="boom",
    )
    for p in found:
        row = p.to_dict()
        assert set(row) >= {"id", "message", "fix", "owner"}
        assert row["message"] and row["fix"]
        assert row["owner"] in (OWNER_YOU, OWNER_ADMIN)


# ---- through the route ---------------------------------------------------------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path, resources=None) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=GOOD_CATALOG,
        project_id="Sage",
        resources=resources if resources is not None else FakeResourceProvider(list(ALIASES)),
    )
    orch.project(start_preview=False)
    return orch


class _DeadGateway(FakeResourceProvider):
    """The gateway will not answer. The house pattern: subclass the fake to force the failure."""

    def list_llm_aliases(self) -> list[LlmAlias]:
        raise ResourceUnavailable("The LLM Gateway did not answer (ConnectError).")


def _client(tmp_path: Path, monkeypatch, resources=None):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path, resources=resources))
    # Both are process-wide state the route reads and writes. Through monkeypatch so one test's
    # Preflight cannot leave its verdict, or its survival count, behind for another.
    monkeypatch.setattr(appmod, "PREFLIGHT_SLOTS", dict(appmod.PREFLIGHT_SLOTS))
    monkeypatch.setattr(appmod, "_PREFLIGHT_SEEN", set())
    return appmod, TestClient(appmod.control_app)


def _route_ids(client) -> list[str]:
    return [p["id"] for p in client.get("/api/health").json()["problems"]]


def test_the_route_reports_a_stale_binding_on_the_second_preflight(tmp_path, monkeypatch):
    appmod, client = _client(tmp_path, monkeypatch)
    appmod.orchestrator.bind_llm_alias("id-sonnet")
    appmod.orchestrator._resources = FakeResourceProvider([ALIASES[1]])
    # First Preflight: seen once, so nothing is said about it yet.
    assert "binding:llm_alias:id-sonnet" not in _route_ids(client)
    rows = {p["id"]: p for p in client.get("/api/health").json()["problems"]}
    p = rows["binding:llm_alias:id-sonnet"]
    # The label the row showed when the choice was made — the only version the creator ever saw.
    assert "Claude Sonnet 4.6" in p["message"]
    assert p["owner"] == "you"


def test_the_route_is_200_when_every_read_fails(tmp_path, monkeypatch):
    # "We could not check" is a state, not a failed request. A 502 here would read to the UI exactly
    # like the Resource rail's, where it means "you have no models" — and a route that reports
    # Problems must not become one.
    appmod, client = _client(tmp_path, monkeypatch, resources=_DeadGateway())

    def _boom(*a, **kw):
        raise RuntimeError("nothing answers here")

    monkeypatch.setattr(appmod.orchestrator, "preflight_bindings", _boom)
    monkeypatch.setattr(appmod.orchestrator, "resolved_agents", _boom)
    monkeypatch.setattr(appmod, "data_library_ready", _boom)
    monkeypatch.setattr("sage.orchestrator.service._opencode_base_port", _boom)
    for _ in range(2):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["problems"] == []


def test_the_route_names_the_unreachable_gateway_the_boot_preflight_found(tmp_path, monkeypatch):
    appmod, client = _client(tmp_path, monkeypatch, resources=_DeadGateway())
    monkeypatch.setattr(appmod, "GATEWAY_MODE", "domino")
    appmod._run_slot_preflight()
    assert "gateway" not in _route_ids(client)
    assert "gateway" in _route_ids(client)


def test_a_port_that_will_not_parse_still_answers(tmp_path, monkeypatch):
    # A route that reports Problems must not become one. An unparseable port costs the port Problem
    # its answer and nothing else.
    _, client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("SAGE_CONTROL_PORT", "not-a-port")
    for _ in range(2):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert "ports" not in [p["id"] for p in r.json()["problems"]]


def test_the_orphaned_preflight_route_is_gone(tmp_path, monkeypatch):
    # It documented itself as "called by the UI just after the project view is live" and had no
    # caller at all. Its Binding check is /api/health's now.
    _, client = _client(tmp_path, monkeypatch)
    assert client.get("/api/preflight").status_code == 404
