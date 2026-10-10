"""A bound LLM Alias is called from the page, and a server route that reaches for one is not clean (#766).

Live (#714, Signal Room 28, prompt 9): "Make Write deal brief use @haiku and Write product brief use
@sonnet." Both Aliases were bound before the request was sent, so the app's AGENTS.md carried its
model section — but that section only ever said how the PAGE calls a model. The brief was built in
an `app.py` route, the only server-side call the guide documents is `sage_mcp.call_tool`, and the
mention note said "pass `alias: ...`" without naming a call. Haiku wrote
`call_tool(..., {"alias": "haiku"})` with a canned fallback, so the bound models were never called,
and the turn ended "Page checks passed": the route answered 200, and the plan-step check counted the
quoted name as the app reaching the model.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator import plan_resources
from sage.orchestrator.plan_steps import parse_steps
from sage.resources.app_helpers import FASTAPI, TEMPLATE
from sage.resources.bindings import KIND_LLM_ALIAS, Binding, Mention, mention_note
from sage.resources.gateway_bypass import server_model_calls
from sage.resources.pinned_model import agents_block

from .fake_opencode import Turn, execution_plan
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting, _orch  # noqa: F401
from .test_model_calls_answers_this_turn_on_chat import _no_real_waiting  # noqa: F401

ALIAS = "implement-model"
HAIKU = Binding(KIND_LLM_ALIAS, "id-haiku", "haiku", "Claude Haiku")
SONNET = Binding(KIND_LLM_ALIAS, "id-sonnet", "sonnet", "Claude Sonnet")

PLAN = Turn(text=execution_plan(
    "Signal Room", "A revenue team's signal room.", "Write deal brief", files="app.py, static/app.js",
    work=f"Make the Write deal brief button use {ALIAS}."))

ROUTE_CALLS_THE_MODEL = (
    "from sage_mcp import call_tool\n\n"
    '@app.post("/api/deal-brief")\n'
    "def deal_brief(req: dict) -> dict:\n"
    "    try:\n"
    f'        return call_tool(URL, "complete", {{"alias": "{ALIAS}", "facts": req}})\n'
    "    except Exception:\n"
    '        return {"verdict": "At risk", "why": "No success criteria recorded"}\n')
ROUTE_RETURNS_FACTS = ('@app.post("/api/deal-brief")\n'
                       "def deal_brief(req: dict) -> dict:\n"
                       '    return {"facts": req}\n')
PAGE_CALLS_THE_MODEL = ("const brief = await sage.askJson(messages, {\n"
                        f'  alias: "{ALIAS}", schemaHint: "{{ why: string }}", fallback: {{ why: "" }},\n'
                        "});\n")


def _approve(tmp_path: Path, *builds: Turn):
    orch, oc = _orch(tmp_path, turns=[PLAN, *builds])
    orch.bind_llm_alias("id-impl")
    list(orch.build_stream(f"Make Write deal brief use @{ALIAS}", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))
    return events, oc


def _done(events: list[dict]) -> dict:
    [done] = [e for e in events if e["type"] == "done"]
    return done


def _notices(events: list[dict]) -> list[str]:
    return [e["message"] for e in events if e["type"] == "data-source-failed"]


# --- what the guide says ------------------------------------------------------------------------

def test_the_model_section_says_the_server_cannot_call_the_model():
    block = agents_block([HAIKU, SONNET], [], FASTAPI)
    assert "never the app's server" in block
    # The one server-side call the fastapi guide documents is named as what it is.
    assert "`sage_mcp.call_tool` reaches MCP servers, not a model" in block
    # A route keeps the facts it gathers; the page asks the model with them.
    assert "return those facts" in block and "askJson" in block


def test_a_stack_with_no_mcp_helper_is_not_told_about_one():
    block = agents_block([HAIKU], [], TEMPLATE)
    assert "never the app's server" in block
    assert "call_tool" not in block


def test_the_mention_note_names_the_call_that_takes_the_alias():
    note = mention_note([Mention(HAIKU), Mention(SONNET)], [HAIKU, SONNET])
    assert 'pass `alias: "sonnet"` to `askModel` or `askJson`' in note
    assert "from the page" in note


# --- what reaches a model -----------------------------------------------------------------------

def test_a_server_file_naming_a_bound_model_is_found_with_its_route():
    sources = [("app.py", ROUTE_CALLS_THE_MODEL),
               ("static/app.js", PAGE_CALLS_THE_MODEL),        # the page is where the call belongs
               ("sage_mcp.py", f'EXAMPLE = "{ALIAS}"\n'),      # Sage's own file
               ("notes.py", "# implement-model, unquoted\n")]  # a word, not a value
    found = server_model_calls(sources, [ALIAS, "other"], frozenset({"sage_mcp.py"}))
    assert found == [("app.py", "/api/deal-brief", ALIAS)]


def test_a_model_named_above_every_route_is_found_without_one():
    found = server_model_calls([("app.py", f'MODEL = "{ALIAS}"\n' + ROUTE_RETURNS_FACTS)],
                               [ALIAS], frozenset())
    assert found == [("app.py", "", ALIAS)]


def _unreached(sources: dict[str, str], aliases=("haiku", "sonnet")) -> list[str]:
    plan = execution_plan("App", "An app.", "Step") + "\n- Uses — haiku"
    res = plan_resources.Resources(aliases=aliases)
    found = plan_resources.unreached(parse_steps(plan), res, list(sources.items()), [])
    return [name for step in found for name in step.missing]


def test_a_model_named_only_in_a_call_tool_argument_is_not_reached():
    assert _unreached({"app.py": 'call_tool(URL, "complete", {"alias": "haiku"})'}) == ["haiku"]
    # Named in one file, called in another: not this model's call.
    assert _unreached({"app.py": 'M = "haiku"', "static/app.js": "sage.askModel(m)"}) == ["haiku"]
    assert _unreached({"static/app.js": 'sage.askModel(m, { alias: "haiku" })'}) == []


# --- the turn -----------------------------------------------------------------------------------

def test_a_route_that_reaches_for_the_model_is_sent_back_once_then_ends_not_clean(tmp_path):
    events, oc = _approve(
        tmp_path, Turn(writes={"app.py": ROUTE_CALLS_THE_MODEL}),
        Turn(writes={"app.py": ROUTE_CALLS_THE_MODEL + "# retried\n"}))

    repairs = [p["text"] for p in oc.prompts[2:]]
    assert len(repairs) == 1
    assert "`/api/deal-brief` in app.py" in repairs[0] and "askJson" in repairs[0]
    done = _done(events)
    assert done["ok"] is False
    assert done["decision"] == "model not called"
    [notice] = _notices(events)
    assert "`/api/deal-brief` in app.py" in notice and ALIAS in notice


def test_a_route_moved_to_the_page_after_the_repair_ends_clean(tmp_path):
    events, oc = _approve(
        tmp_path, Turn(writes={"app.py": ROUTE_CALLS_THE_MODEL}),
        Turn(writes={"app.py": ROUTE_RETURNS_FACTS, "static/app.js": PAGE_CALLS_THE_MODEL}))

    assert len(oc.prompts) == 3
    assert _done(events)["ok"] is True
    assert _notices(events) == []


def test_the_page_calling_the_model_is_clean(tmp_path):
    events, oc = _approve(
        tmp_path, Turn(writes={"app.py": ROUTE_RETURNS_FACTS, "static/app.js": PAGE_CALLS_THE_MODEL}))

    assert len(oc.prompts) == 2  # the plan, the build: nothing sent back
    assert _done(events)["ok"] is True
    assert _notices(events) == []
