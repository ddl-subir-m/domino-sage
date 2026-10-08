"""A plan step that names a Project resource is built only when the app's code reaches it (#712).

Live (Signal Room, #714): the first build of a planned app shipped a placeholder Deal Desk route and
no news source, and Sage reported it complete. The unbuilt-step check (#662, #684) asks only whether
a step's files changed, and the stub route changed its file. The planner had also never been told the
Project's MCP servers, so the plan said "a live news source" instead of naming `tavily`.

Now a step may carry `Uses`, naming Project resources exactly; the planner is given the list to name
them from; and a step whose named resource nothing in the app reaches goes back through the same one
repair as a step that wrote none of its files.
"""
from __future__ import annotations

from pathlib import Path

from sage import extension_mcp, project_secrets
from sage.orchestrator import plan_resources
from sage.orchestrator.plan_steps import parse_steps, validate_execution_contract

from .fake_opencode import Turn, execution_plan
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting, _orch  # noqa: F401

DEAL_DESK_URL = "https://deal-desk.example.com/mcp"

PLAN = Turn(text=execution_plan(
    "Signal Room", "A product signal room.", "Deal desk route", files="app.py",
    work="Add a route that asks the deal desk for open deals.") + (
    "\n- Uses — deal-desk, Mystery API\n\n"
    "### 2. News panel\n- Files — static/news.js\n"
    "- Do — Show the latest product news.\n- Done when — The panel lists headlines.\n"
    "- Uses — NEWS_KEY"))

STUB = 'from fastapi import FastAPI\napp = FastAPI()\n\n@app.get("/api/deals")\ndef deals():\n    return {"deals": []}  # placeholder\n'
WIRED = ('from sage_mcp import call_tool\nfrom sage_secrets import secret\n\n'
         '@app.get("/api/deals")\ndef deals():\n'
         f'    return call_tool("{DEAL_DESK_URL}", "open_deals", {{}},\n'
         '                     {"Authorization": f"Bearer {secret(\'DEAL_DESK_TOKEN\')}"})\n\n'
         '@app.get("/api/news")\ndef news():\n    return {"key_set": bool(secret("NEWS_KEY"))}\n')
NEWS = 'fetch("/api/news")\n'

DEAL_DESK_SENT_BACK = "Plan step 1 (Deal desk route) uses deal-desk, but nothing in the app calls it."
NEWS_KEY_SENT_BACK = "Plan step 2 (News panel) uses NEWS_KEY, but nothing in the app reads it."


def _project_resources(orch, monkeypatch) -> None:
    root = orch.project(start_preview=False).record.path
    extension_mcp.add(root, "deal-desk", DEAL_DESK_URL,
                      {"Authorization": "Bearer {env:DEAL_DESK_TOKEN}"})
    extension_mcp.set_tools(root, "deal-desk", ["open_deals", "deal_terms"], None)
    extension_mcp.add(root, "switched-off", "https://off.example.com/mcp")
    extension_mcp.set_enabled(root, "switched-off", False)
    monkeypatch.setattr(project_secrets, "known_values", lambda: {
        "DEAL_DESK_TOKEN": "tok-value-never-shown", "NEWS_KEY": "news-value-never-shown",
        "SAGE_APP_KEY": "reserved"})


def _approve(tmp_path: Path, monkeypatch, *builds: Turn):
    orch, oc = _orch(tmp_path, turns=[PLAN, *builds])
    _project_resources(orch, monkeypatch)
    list(orch.build_stream("build me a signal room", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))
    history = orch.project(start_preview=False).app_for_turn().read_history("c1")
    return events, history, oc


def _done(events: list[dict]) -> dict:
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    return done[0]


# --- the plan format ---------------------------------------------------------------------------

def test_a_step_names_the_resources_it_uses_and_a_step_without_any_is_still_valid():
    steps = parse_steps(PLAN.text)
    assert [s.uses for s in steps] == [["deal-desk", "Mystery API"], ["NEWS_KEY"]]
    assert validate_execution_contract(PLAN.text).valid
    plain = execution_plan("Signal Room", "A product signal room.", "Deal desk route")
    assert parse_steps(plain)[0].uses == []
    assert validate_execution_contract(plain).valid


def test_uses_is_read_under_the_names_a_model_drifts_to():
    for spelling in ("Uses — `tavily`, NEWS_KEY", "uses: tavily, NEWS_KEY", "Use - tavily, NEWS_KEY",
                     "Resources — tavily, NEWS_KEY", "Depends on: tavily, NEWS_KEY"):
        plan = execution_plan("News", "News.", "News panel") + "\n- " + spelling
        assert parse_steps(plan)[0].uses == ["tavily", "NEWS_KEY"], spelling


# --- the planner is told what the Project has ----------------------------------------------------

def test_the_planner_is_given_the_projects_servers_secrets_and_models(tmp_path: Path, monkeypatch):
    orch, oc = _orch(tmp_path, turns=[PLAN])
    _project_resources(orch, monkeypatch)
    orch.bind_llm_alias("id-impl")
    list(orch.build_stream("build me a signal room", conversation="c1"))

    prompt = oc.prompts[0]["text"]
    assert "`deal-desk`" in prompt and "open_deals" in prompt and "deal_terms" in prompt
    assert "`DEAL_DESK_TOKEN`" in prompt and "`NEWS_KEY`" in prompt
    assert "`implement-model`" in prompt
    assert "'- Uses —'" in prompt
    # Names, never values; never Sage's own reserved names; never a server switched off.
    assert "never-shown" not in prompt and "SAGE_APP_KEY" not in prompt
    assert "switched-off" not in prompt


def test_a_project_with_no_resources_adds_nothing_to_the_planner():
    assert plan_resources.planner_note(plan_resources.Resources()) == ""


# --- the check, and the repair it feeds -----------------------------------------------------------

def test_a_step_that_uses_deal_desk_but_ships_a_stub_route_is_built_again(tmp_path, monkeypatch):
    events, history, oc = _approve(
        tmp_path, monkeypatch,
        Turn(writes={"app.py": STUB + '\n# NEWS: secret("NEWS_KEY")\n', "static/news.js": NEWS}),
        Turn(writes={"app.py": WIRED}))

    assert [DEAL_DESK_SENT_BACK in p["text"] for p in oc.prompts[2:]] == [True]
    assert _done(events)["ok"] is True
    assert "plan-unbuilt" not in [r["type"] for r in history]


def test_a_step_whose_route_calls_deal_desk_is_built(tmp_path, monkeypatch):
    events, history, oc = _approve(
        tmp_path, monkeypatch, Turn(writes={"app.py": WIRED, "static/news.js": NEWS}))

    assert len(oc.prompts) == 2  # the plan, the build: nothing sent back
    assert _done(events)["ok"] is True
    assert "plan-unbuilt" not in [r["type"] for r in history]


def test_a_secret_the_step_names_but_the_app_never_reads_is_sent_back(tmp_path, monkeypatch):
    never_reads_news = WIRED.replace('secret("NEWS_KEY")', '"NEWS"')
    events, _history, oc = _approve(
        tmp_path, monkeypatch, Turn(writes={"app.py": never_reads_news, "static/news.js": NEWS}),
        Turn(writes={"app.py": WIRED}))

    assert [NEWS_KEY_SENT_BACK in p["text"] for p in oc.prompts[2:]] == [True]
    assert _done(events)["ok"] is True


def test_a_resource_still_unreached_after_one_repair_ends_the_turn_incomplete(tmp_path, monkeypatch):
    events, history, oc = _approve(
        tmp_path, monkeypatch,
        Turn(writes={"app.py": STUB + '\n# secret("NEWS_KEY")\n', "static/news.js": NEWS}),
        Turn(writes={"app.py": STUB + '\n# secret("NEWS_KEY")\n# still a stub\n'}))

    # One repair, the same bound as a step that wrote none of its files.
    assert [DEAL_DESK_SENT_BACK in p["text"] for p in oc.prompts[2:]] == [True]
    done = _done(events)
    assert done["ok"] is False
    assert done["decision"] == "incomplete — plan step 1 (Deal desk route) not built"
    rows = [r for r in history if r["type"] == "plan-unbuilt"]
    assert [r["steps"] for r in rows] == [[1]]
    assert "Step 1 (Deal desk route) uses deal-desk, but nothing in the app calls it." \
        in rows[0]["message"]


def test_a_name_that_is_no_project_resource_is_ignored(tmp_path, monkeypatch):
    # PLAN's step 1 also names "Mystery API", which the Project does not have. WIRED reaches
    # everything that IS a resource, so nothing is sent back for the unknown name.
    events, _history, oc = _approve(
        tmp_path, monkeypatch, Turn(writes={"app.py": WIRED, "static/news.js": NEWS}))
    assert len(oc.prompts) == 2
    assert _done(events)["ok"] is True


def test_sages_own_model_config_naming_the_model_is_not_the_app_calling_it(tmp_path, monkeypatch):
    plan = Turn(text=execution_plan(
        "Brief", "A brief writer.", "Brief panel", files="src/App.tsx",
        work="Ask the model for a brief.") + "\n- Uses — implement-model")
    orch, oc = _orch(tmp_path, turns=[plan, Turn(writes={"src/App.tsx": "export default 1\n"}),
                                      Turn(writes={"src/App.tsx": "askModel(messages)\n"})])
    orch.bind_llm_alias("id-impl")
    app = orch.project(start_preview=False).app_for_turn()
    assert any("implement-model" in (app.path / rel).read_text()
               for rel in app.helpers.owned if (app.path / rel).is_file())
    list(orch.build_stream("build me a brief writer", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))

    sent_back = "Plan step 1 (Brief panel) uses implement-model, but nothing in the app calls it."
    assert [sent_back in p["text"] for p in oc.prompts[2:]] == [True]
    assert _done(events)["ok"] is True


# --- the reference rule for each kind -------------------------------------------------------------

SERVER = {"name": "deal-desk", "url": DEAL_DESK_URL + "/?region={env:DEAL_REGION}",
          "headers": {"Authorization": "Bearer {env:DEAL_DESK_TOKEN}"}}


def _unreached(uses: list[str], sources: dict[str, str], *, aliases=(), queries=()) -> list[str]:
    plan = execution_plan("App", "An app.", "Step") + "\n- Uses — " + ", ".join(uses)
    res = plan_resources.Resources(servers=(SERVER,), secrets=("DEAL_DESK_TOKEN", "NEWS_KEY"),
                                   aliases=tuple(aliases))
    found = plan_resources.unreached(parse_steps(plan), res, list(sources.items()), list(queries))
    return [name for step in found for name in step.missing]


def test_a_server_is_reached_by_its_url_or_its_header_secret_beside_a_call():
    by_url = {"app.py": f'call_tool("{DEAL_DESK_URL}/", "open_deals")'}
    by_secret = {"app.py": 'call_tool(URL, "open_deals", {}, {"x": secret("DEAL_DESK_TOKEN")})'}
    assert _unreached(["deal-desk"], by_url) == []
    assert _unreached(["deal-desk"], by_secret) == []
    # The address alone, with no call, is a constant nobody uses.
    assert _unreached(["deal-desk"], {"app.py": f'URL = "{DEAL_DESK_URL}"'}) == ["deal-desk"]
    # A call in one file and the address in another is not this server's call.
    split = {"app.py": 'call_tool(URL, "open_deals")', "config.py": f'URL = "{DEAL_DESK_URL}"'}
    assert _unreached(["deal-desk"], split) == ["deal-desk"]


def test_a_secret_is_reached_only_by_reading_it_by_name():
    assert _unreached(["NEWS_KEY"], {"app.py": "secret('NEWS_KEY')"}) == []
    assert _unreached(["NEWS_KEY"], {"app.py": 'os.environ["NEWS_KEY"]'}) == ["NEWS_KEY"]


def test_a_model_is_reached_by_naming_it_or_by_being_the_only_one():
    call = {"static/app.js": 'sage.askModel(messages, { alias: "haiku" })'}
    assert _unreached(["haiku"], call, aliases=("haiku", "sonnet")) == []
    bare = {"static/app.js": "sage.askModel(messages)"}
    assert _unreached(["haiku"], bare, aliases=("haiku",)) == []
    assert _unreached(["haiku"], bare, aliases=("haiku", "sonnet")) == ["haiku"]
    assert _unreached(["haiku"], {"static/app.js": "// no model"}, aliases=("haiku",)) == ["haiku"]


def test_a_query_is_reached_when_the_catalog_has_it_and_the_app_calls_it_by_name():
    calls = {"static/app.js": 'sage.runQuery("open_deals", {})'}
    assert _unreached(["open_deals"], calls, queries=("open_deals",)) == []
    assert _unreached(["open_deals"], {"static/app.js": ""}, queries=("open_deals",)) \
        == ["open_deals"]
    # Not in the catalog: not a resource this check knows, so not judged.
    assert _unreached(["open_deals"], {"static/app.js": ""}) == []
