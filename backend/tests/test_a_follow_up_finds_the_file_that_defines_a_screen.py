"""#700, slice E v1: a follow-up asks the code map which file defines a screen instead of rediscovering it.

The symptom was spend, not a crash: a follow-up on a larger app spent its first model calls on
`read`/`glob`/`grep` to find which file holds a screen or a route, because the turn had only a flat
list of paths and names. `sage_source_map` answers that from the app's own files, on demand, and
only on a Build turn whose Project switched it on — promotion to the default offer waits for the
evaluation (#702).

What it may say is as much the subject here as what it finds. It reads the turn's own app and
nothing else: a path that climbs out, a dotfile, a data file, a symlink into another app — each
returns nothing. And it is never stale: an edit, a deletion or an addition shows on the next call,
including an edit that keeps the file's size and mtime.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

import pytest

from sage import source_map

from .fake_opencode import FakeOpenCode, Turn


def _write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def _line(text: str, needle: str) -> int:
    return text[:text.index(needle)].count("\n") + 1


def _stack(root: Path, name: str) -> None:
    _write(root, ".sage/settings.json", json.dumps({"stack": name}))


_APP_PY = '''"""This app's server."""
import sage_serve
from fastapi import FastAPI

app = FastAPI()
sage_serve.mount(app)


@app.get("/api/summary")
def summary(region, *, limit=10) -> dict:
    return {"rows": 0, "token": "PRIVATE_DEFAULT"}


ROUTE = "/api/dynamic"


@app.post(ROUTE)
async def dynamic(body):
    pass


class Report:
    pass
'''

_INDEX = '''<!DOCTYPE html>
<html>
<body>
  <div id="root"></div>
  <script src="static/vendor/react.production.min.js"></script>
  <script src="static/sage/appQuery.js"></script>
  <script src="./static/components/Detail.js?v=2"></script>
  <script src="static/components/MainScreen.js"></script>
  <script src="static/app.js"></script>
</body>
</html>
'''

_APP_JS = '''(function () {
  const { createElement: h } = React;
  function App() {
    return h(window.app.MainScreen);
  }
  const name = 'Detail';
  const Other = window.app[name];
  ReactDOM.createRoot(document.getElementById('root')).render(h(App));
})();
'''

_MAIN = '''(function () {
  const { createElement: h } = React;
  window.app = window.app || {};

  function MainScreen() {
    const q = sage.useQuery("usage_by_region", { region: "all" });
    return h(window.app.Detail, { rows: q.data });
  }

  window.app.MainScreen = MainScreen;
})();
'''

_DETAIL = '''(function () {
  window.app = window.app || {};
  const SECRET_ROWS = [{ ssn: '123-45-6789' }];
  function Detail(props) {
    const which = props.query;
    sage.runQuery(which, {});
    return null;
  }
  window.app.Detail = Detail;
})();
'''

_QUERIES = '''[
  {"name": "usage_by_region", "binding": "ds_1",
   "sql": "SELECT PRIVATE_SQL_BODY FROM T WHERE region = :region",
   "params": [{"name": "region", "type": "string"}]}
]
'''


def _fastapi(tmp: Path) -> Path:
    """App A, with app B beside it — the other app a request must never reach."""
    root = tmp / "apps" / "A"
    _stack(root, "fastapi-antd")
    _write(root, "app.py", _APP_PY)
    _write(root, "static/index.html", _INDEX)
    _write(root, "static/app.js", _APP_JS)
    _write(root, "static/components/MainScreen.js", _MAIN)
    _write(root, "static/components/Detail.js", _DETAIL)
    _write(root, "static/vendor/react.production.min.js", "window.React = {}\n")
    _write(root, "static/sage/appQuery.js",
           '// sage.runQuery("example_in_a_comment", {})\nwindow.sage = window.sage || {};\n')
    _write(root, ".sage/queries.json", _QUERIES)
    _write(root, ".sage/bindings.json", '[{"id": "ds_1", "kind": "data_source"}]\n')
    _write(root, ".env", "SECRET=PRIVATE_ENV_VALUE\n")
    _write(root, "public/data.csv", "ssn\n123-45-6789\n")
    other = tmp / "apps" / "B"
    _stack(other, "fastapi-antd")
    _write(other, "static/secret.js", "window.app.Leak = 'OTHER_APP_SECRET';\n")
    _write(other, "app.py", "def other_app_route():\n    return 'OTHER_APP_SECRET'\n")
    (root / "static" / "components" / "Leak.js").symlink_to(other / "static" / "secret.js")
    return root


def _react(tmp: Path) -> Path:
    root = tmp / "react"
    _stack(root, "react-vite")
    _write(root, "src/App.tsx",
           'import "./App.css";\nimport MainScreen from "./screens/MainScreen";\n\n'
           "function App() {\n  return <MainScreen />;\n}\n\nexport default App;\n")
    _write(root, "src/screens/MainScreen.tsx",
           'import { useQuery } from "../appQuery";\n\n'
           "export default function MainScreen() {\n"
           '  const q = useQuery("usage_by_region", {});\n  return <main />;\n}\n')
    return root


def _ask(root: Path, **args) -> dict:
    return json.loads(source_map.lookup(root, args))


def _file(out: dict, path: str) -> dict:
    return next(f for f in out["files"] if f["path"] == path)


def _rels(out: dict, kind: str) -> list[dict]:
    return [r for r in out["relationships"] if r["kind"] == kind]


# ---- what it finds ---------------------------------------------------------------------------


def test_a_screen_registered_on_window_app_is_found_by_its_name_without_any_import(tmp_path):
    root = _fastapi(tmp_path)

    out = _ask(root, symbol="MainScreen")

    assert out["files"][0]["path"] == "static/components/MainScreen.js", "definitions come first"
    assert {"name": "window.app.MainScreen", "kind": "window",
            "line": _line(_MAIN, "window.app.MainScreen =")} in out["files"][0]["definitions"]
    assert {"kind": "references", "from": "static/app.js", "to": "window.app.MainScreen",
            "line": _line(_APP_JS, "window.app.MainScreen")} in out["relationships"]


def test_the_page_loads_its_scripts_in_order_and_says_where(tmp_path):
    root = _fastapi(tmp_path)

    loads = _rels(_ask(root, paths=["static/index.html"]), "loads")

    assert [(r["from"], r["to"], r["order"], r["line"]) for r in loads] == [
        ("static/index.html", "static/sage/appQuery.js", 2, _line(_INDEX, "appQuery")),
        ("static/index.html", "static/components/Detail.js", 3, _line(_INDEX, "Detail.js")),
        ("static/index.html", "static/components/MainScreen.js", 4, _line(_INDEX, "MainScreen.js")),
        ("static/index.html", "static/app.js", 5, _line(_INDEX, "static/app.js")),
    ]


def test_python_definitions_and_literal_routes_carry_their_lines_and_no_values(tmp_path):
    root = _fastapi(tmp_path)

    out = _ask(root, paths=["app.py"])

    assert _file(out, "app.py")["definitions"] == [
        {"name": "summary", "kind": "function", "line": _line(_APP_PY, "def summary"),
         "signature": "def summary(region, *, limit)"},
        {"name": "dynamic", "kind": "function", "line": _line(_APP_PY, "async def dynamic"),
         "signature": "async def dynamic(body)"},
        {"name": "Report", "kind": "class", "line": _line(_APP_PY, "class Report")},
    ]
    assert _rels(out, "route") == [{"kind": "route", "from": "app.py", "to": "GET /api/summary",
                                    "name": "summary", "line": _line(_APP_PY, "@app.get")}]
    assert "PRIVATE_DEFAULT" not in json.dumps(out)


def test_named_queries_come_from_the_catalog_with_their_parameters_and_never_their_sql(tmp_path):
    root = _fastapi(tmp_path)

    out = _ask(root, symbol="usage_by_region")

    assert _file(out, ".sage/queries.json")["definitions"] == [
        {"name": "usage_by_region", "kind": "query", "line": 2,
         "params": [{"name": "region", "type": "string"}]}]
    assert {"kind": "calls_query", "from": "static/components/MainScreen.js",
            "to": "query:usage_by_region", "line": _line(_MAIN, "useQuery")} in out["relationships"]
    assert "PRIVATE_SQL_BODY" not in json.dumps(out)


def test_a_path_returns_its_definitions_and_what_it_is_linked_to_one_hop_away(tmp_path):
    root = _fastapi(tmp_path)

    out = _ask(root, paths=["static/app.js"])

    paths = [f["path"] for f in out["files"]]
    assert paths[0] == "static/app.js"
    assert "static/components/MainScreen.js" in paths, "the file defining what app.js mounts"
    assert "static/index.html" in paths, "and the page that loads it"


def test_a_relationship_the_map_cannot_resolve_is_unknown_and_not_absent(tmp_path):
    root = _fastapi(tmp_path)

    coverage = _ask(root)["coverage"]

    assert coverage["status"] == "unknown"
    for entry in ({"path": "static/app.js", "line": _line(_APP_JS, "window.app[name]"),
                   "reason": "computed window member"},
                  {"path": "static/components/Detail.js", "line": _line(_DETAIL, "runQuery(which"),
                   "reason": "query name is not a literal"},
                  {"path": "app.py", "line": _line(_APP_PY, "@app.post"),
                   "reason": "route path is not a literal"}):
        assert entry in coverage["unknown"], entry


def test_react_imports_are_reported_as_unknown_coverage_rather_than_no_relationships(tmp_path):
    root = _react(tmp_path)

    out = _ask(root, paths=["src/App.tsx"])

    app = _file(out, "src/App.tsx")
    assert app["definitions"] == [{"name": "App", "kind": "name", "line": 4}]
    assert out["coverage"]["status"] == "unknown"
    assert [u for u in out["coverage"]["unknown"] if u["path"] == "src/App.tsx"] == [
        {"path": "src/App.tsx", "line": 1, "reason": "import"},
        {"path": "src/App.tsx", "line": 2, "reason": "import"}]


def test_sage_owned_helper_text_is_not_mistaken_for_the_apps_own_query_calls(tmp_path):
    root = _fastapi(tmp_path)

    assert "example_in_a_comment" not in json.dumps(_ask(root))


# ---- what it may not say -----------------------------------------------------------------------


@pytest.mark.parametrize("asked", [
    "../B/static/secret.js",
    "static/../../B/app.py",
    "/etc/passwd",
    "ABSOLUTE",
    ".env",
    ".sage/bindings.json",
    "public/data.csv",
    "static/components/Leak.js",
])
def test_a_path_outside_the_apps_own_source_returns_nothing(tmp_path, asked):
    root = _fastapi(tmp_path)
    if asked == "ABSOLUTE":
        asked = str(root / "app.py")

    out = _ask(root, paths=[asked])

    assert out["files"] == [] and out["relationships"] == [] and out["coverage"]["unknown"] == []
    assert out["unmatched"] == [asked]
    for secret in ("OTHER_APP_SECRET", "PRIVATE_ENV_VALUE", "123-45-6789", "ds_1"):
        assert secret not in json.dumps(out)


def test_a_symlink_into_another_app_is_not_part_of_this_apps_map(tmp_path):
    root = _fastapi(tmp_path)

    overview, by_name = _ask(root), _ask(root, symbol="Leak")

    assert "static/components/Leak.js" not in [f["path"] for f in overview["files"]]
    assert by_name["files"] == []
    assert "OTHER_APP_SECRET" not in json.dumps(overview) + json.dumps(by_name)


def test_the_overview_never_carries_a_value_from_the_source(tmp_path):
    root = _fastapi(tmp_path)

    said = json.dumps(_ask(root))

    for value in ("123-45-6789", "PRIVATE_DEFAULT", "PRIVATE_SQL_BODY", "PRIVATE_ENV_VALUE"):
        assert value not in said


# ---- never stale -------------------------------------------------------------------------------


def test_an_edit_that_keeps_size_and_mtime_is_still_seen(tmp_path):
    root = _fastapi(tmp_path)
    detail = root / "static" / "components" / "Detail.js"
    before = _ask(root)
    stat = detail.stat()
    detail.write_text(_DETAIL.replace("window.app.Detail =", "window.app.Detbil ="))
    os.utime(detail, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    after = _ask(root, symbol="Detbil")

    assert after["files"][0]["path"] == "static/components/Detail.js"
    assert after["sourceDigest"] != before["sourceDigest"]


def test_a_deleted_file_leaves_the_map(tmp_path):
    root = _fastapi(tmp_path)
    before = _ask(root)
    (root / "static" / "components" / "Detail.js").unlink()

    after = _ask(root)

    assert "static/components/Detail.js" not in [f["path"] for f in after["files"]]
    assert _ask(root, symbol="Detail")["files"] == [], "no definer survives its file"
    assert after["sourceDigest"] != before["sourceDigest"]


def test_an_added_file_is_in_the_next_answer(tmp_path):
    root = _fastapi(tmp_path)
    _ask(root)
    chart = _write(root, "static/components/Chart.js", "window.app.Chart = function Chart() {};\n")

    out = _ask(root, symbol="Chart")

    assert out["files"][0]["path"] == "static/components/Chart.js"
    assert out["files"][0]["definitions"][0]["line"] == _line(chart, "window.app.Chart")


# ---- bounded ------------------------------------------------------------------------------------


def _large(tmp: Path) -> Path:
    root = tmp / "large"
    _stack(root, "fastapi-antd")
    for i in range(300):
        _write(root, f"static/gen/f{i:03}.js", "".join(
            f"window.app.Widget{i:03}x{j:02} = function () {{ return window.app.Widget{i:03}x{j:02}; }};\n"
            for j in range(20)))
    return root


def test_a_large_app_stays_inside_the_budget_and_says_how_much_it_left_out(tmp_path):
    root = _large(tmp_path)

    text = source_map.lookup(root, {})

    assert len(text.encode()) <= source_map.RESPONSE_BUDGET
    assert json.loads(text)["omittedCount"] > 0


def test_the_requested_path_outranks_the_overview_in_a_large_app(tmp_path):
    root = _large(tmp_path)

    text = source_map.lookup(root, {"paths": ["static/gen/f299.js"]})

    assert len(text.encode()) <= source_map.RESPONSE_BUDGET
    assert json.loads(text)["files"][0]["path"] == "static/gen/f299.js"


# ---- bound to the Build turn that offers it ----------------------------------------------------


def _shim_tools(*, chat: bool, offered: bool) -> set[str]:
    from sage.gateway.client import FakeGatewayClient
    from sage.router.model_control import ModelControl
    from sage.router.models import Mode, ModelCatalog
    from sage.shim.enforcement import EnforcementShim

    control = ModelControl(mode=Mode.IMPLEMENT)
    gateway = FakeGatewayClient()
    shim = EnforcementShim(control, ModelCatalog(*["sonnet"] * 6), gateway)
    names = ["read", "edit", "glob", "sage_source_map"]
    if chat:
        control.arm_chat("thread")
    if offered:
        control.arm_source_map()
    list(shim.handle({"model": "sonnet", "messages": [{"role": "user", "content": "x"}],
                      "tools": [{"type": "function", "function": {"name": n}} for n in names]},
                     project="p"))
    return {t["function"]["name"] for t in gateway.seen[-1][0]["tools"]}


def test_the_tool_is_withheld_from_a_build_turn_that_did_not_switch_it_on():
    assert "sage_source_map" not in _shim_tools(chat=False, offered=False)


def test_the_tool_is_offered_on_a_build_turn_that_switched_it_on():
    assert "sage_source_map" in _shim_tools(chat=False, offered=True)


def test_the_tool_is_never_offered_to_a_chat_turn():
    assert "sage_source_map" not in _shim_tools(chat=True, offered=False)


class _Asking(FakeOpenCode):
    """Calls the tool while the turn is live, the way OpenCode would, with the token it was sent."""

    orch = None
    answers: ClassVar[list] = []
    offered: ClassVar[list] = []

    def send_prompt(self, session_id, text, *args, **kwargs):
        import re

        token = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        project = self.orch.project(start_preview=False)
        self.offered.append(project.control.snapshot().source_map_offered)
        self.answers.append(self.orch.source_map_call({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "sage_source_map",
                       "arguments": {"token": token.group(1) if token else "", "symbol": "App"}},
        })["result"]["content"][0]["text"])
        return super().send_prompt(session_id, text, *args, **kwargs)


def _build_turn(tmp: Path, *, switched_on: bool):
    from sage.router.models import Mode

    from .test_chat_turn import _orch

    _Asking.answers, _Asking.offered = [], []
    orch, oc = _orch(tmp, client=lambda ws: _Asking(ws, [Turn(text="Updated.", writes={
        "src/App.tsx": "export default () => null;"})]))
    _Asking.orch = orch
    project = orch.project(start_preview=False)
    project.build_conversation = orch.create_thread()["id"]
    project.record.write_settings({**project.record.read_settings(), "source_map": switched_on})
    list(orch._build_stream("Make the panel blue.", mode=Mode.IMPLEMENT, is_approval=True))
    return orch, oc, project


@pytest.fixture
def _no_runtime_wait(monkeypatch):
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_a_build_turn_that_switched_it_on_reads_its_own_apps_map(tmp_path, _no_runtime_wait):
    _orch, _oc, project = _build_turn(tmp_path, switched_on=True)

    assert _Asking.offered[0] is True
    answer = json.loads(_Asking.answers[0])
    assert answer["files"][0]["path"] == "src/App.tsx"
    assert project.control.snapshot().source_map_offered is False, "disarmed when the turn ends"


def test_a_build_turn_that_did_not_switch_it_on_gets_no_map(tmp_path, _no_runtime_wait):
    _build_turn(tmp_path, switched_on=False)

    assert _Asking.offered[0] is False
    assert "Read and search the app's files instead" in _Asking.answers[0]
    assert "schemaVersion" not in _Asking.answers[0]


def test_a_token_that_is_not_the_build_turns_reads_no_map(tmp_path, _no_runtime_wait):
    orch, _oc, project = _build_turn(tmp_path, switched_on=True)
    tid = orch.create_thread()["id"]
    chat_token = orch._mint_live_read_token(tid, include_app_bindings=False)
    armed = project.control.arm_source_map()

    try:
        for token in ("lrt_not_a_token", chat_token):
            reply = orch.source_map_call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": "sage_source_map",
                                                     "arguments": {"token": token}}})
            assert "schemaVersion" not in reply["result"]["content"][0]["text"], token
    finally:
        project.control.disarm_source_map(armed)


def test_the_build_prompt_carries_one_inventory_and_never_the_map(tmp_path, _no_runtime_wait):
    _orch, oc, _project = _build_turn(tmp_path, switched_on=True)

    first = oc.prompts[0]["text"]
    assert first.count("Existing source paths (JSON array") == 1
    assert "schemaVersion" not in first and "relationships" not in first


def test_the_route_answers_off_the_event_loop_through_the_orchestrator(tmp_path, _no_runtime_wait):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from sage.orchestrator import app as control

    orch, _oc, _project = _build_turn(tmp_path, switched_on=True)
    with patch.object(control, "orchestrator", orch), TestClient(control.control_app) as client:
        r = client.post("/mcp/source-map", json={
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "sage_source_map", "arguments": {"token": "lrt_stale"}}})

    assert r.status_code == 200
    assert r.json()["id"] == 7
    assert "not current" in r.json()["result"]["content"][0]["text"]
