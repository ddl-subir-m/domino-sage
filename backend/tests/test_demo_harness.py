"""The demo-script harness (#715): `scripts/eval/demo.py` and its post-build probe `probe.mjs`.

No live workspace: the run is driven against a fake Sage that answers with recorded event streams,
so what is checked is the order of the Workbench calls, the record's shape and that it keeps no
model text. The probe's detections run in a real headless Chromium over four tiny pages, gated the
way the page check's own end-to-end tests are, so they skip where Chromium or playwright-core is
missing (a worktree has no `node_modules`).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sage.preview import page_check

EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
_spec = importlib.util.spec_from_file_location("demo_harness", EVAL / "demo.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)

PROSE = "MODEL PROSE THAT MUST NOT BE KEPT"
PLAN_TEXT = "PLAN BODY THAT MUST NOT BE KEPT"


# ---- a fake Sage answering with recorded streams --------------------------------------------------

MEMBERS = [
    {"id": "data_source:ds_sf", "name": "Snowflake-Data-Warehouse", "kind": "data_source",
     "bindingKey": ["data_source", "ds_sf"],
     "pins": [{"database": "DWH", "schema": "MARTS", "table": t} for t in
              ("SFDC__OPPORTUNITY_ENHANCED", "GONG__CALL_TRANSCRIPTS_FLATTENED", "MIXPANEL__EVENT")]},
    {"id": "dataset:ds_pb", "name": "sales-playbooks", "kind": "dataset",
     "bindingKey": ["dataset", "ds_pb"]},
    {"id": "llm_alias:haiku", "name": "haiku", "kind": "llm_alias", "bindingKey": ["llm_alias", "haiku"]},
    {"id": "llm_alias:sonnet", "name": "sonnet", "kind": "llm_alias",
     "bindingKey": ["llm_alias", "sonnet"]},
]
SKILLS = [{"id": f"skill:{n}", "name": n, "kind": "skill"} for n in
          ("revops-conventions", "product-insights", "meddpicc", "deal-brief", "drift-metrics")]


def text(t=PROSE):
    return {"type": "agent", "kind": "text", "text": t}


def tool(name):
    return {"type": "agent", "kind": "tool", "tool": name, "detail": PROSE}


def chart():
    return {"type": "artifacts", "items": [{"kind": "chart", "role": "answer", "name": "a.vl.json"},
                                           {"kind": "table", "role": "working", "name": "w.csv"}]}


def done(**kw):
    return {"type": "done", "ok": True, "decision": "answered", **kw}


def built(overall="passed"):
    return done(decision="built", ok=overall != "failed",
                verification={"overall": overall, "stages": {"typecheck": "passed", "page": overall}})


CHAT = "/threads/th_1/chat/stream"
BUILD = "/project/build/stream"
APPROVE = "/project/build/approve"

# (path, events) in the order the run must ask for them.
STREAMS = [
    (CHAT, [text(), tool("sage_query"), chart(), done()]),                       # 1
    (CHAT, [{"type": "dataset-files", "datasetId": "ds_pb", "prompt": PROSE,    # 2 draws the card
             "rows": [{"kind": "file", "path": "cards/battlecards.md"},
                      {"kind": "file", "path": "cards/pricing.md"}]}, done(decision="asked")]),
    (CHAT, [tool("sage_query"), chart(), done()]),                               # 2 replayed
    (CHAT, [tool("sage_query"), text(), done()]),                                # 3 writes no chart
    (CHAT, [chart(), done()]),                                                   # 4
    (CHAT, [tool("tavily_tavily-search"), tool("tavily_tavily-search"), done()]),  # 5
    (CHAT, [tool("deal-desk_discount_approval"), done()]),                       # 6
    (CHAT, [{"type": "handoff-suggest"}, text(), done()]),                       # 7
    (APPROVE, [text(), built()]),                                                # the handoff's plan
    (BUILD, [{"type": "plan-proposed", "planId": "plan_8", "steps": 2, "plan": PLAN_TEXT},
             done(decision="awaiting approval")]),                               # 8 drafts a plan
    (APPROVE, [built()]),                                                        # 8 approved
    (BUILD, [{"type": "mentions-unresolved", "message": "not in app", "prompt": "replay me"},
             built()]),                                                          # 9
    (BUILD, [built()]),                                                          # 9, Add them and build
    (BUILD, [built()]),                                                          # 9b ends clean...
]
PROBES = [{"tabs": [{"tab": "Pipeline", "runtimeErrors": 0}]},
          {"tabs": [{"tab": "Usage drift", "invisibleCharts": 1}]},              # 8 clean, but found
          {"tabs": []}, {"tabs": []},
          {"tabs": [{"tab": "Product insights", "missingColumns": ["asks.sample"]}]}]  # 9b: found


class FakeSage:
    def __init__(self):
        self.app = ""
        self.calls: list[tuple[str, str, dict | None]] = []
        self.streams = list(STREAMS)
        self.diagnostics = 0

    def get(self, path, timeout=60.0):
        self.calls.append(("GET", path, None))
        if path == "/apps":
            return {"items": [], "selected": ""}
        if path == "/project/resources":
            return {"items": MEMBERS}
        if path == "/project/extensions":
            return {"items": SKILLS}
        if path.startswith("/plans/plan_7/markdown"):
            return {"path": "plans/plan_7.md",
                    "content": f"# Signal Room\n\n## Not doing\n- {PLAN_TEXT}\n\n## Done when\n- it works\n"}
        if path.startswith("/project/build-diagnostics/"):
            self.diagnostics += 1
            if self.diagnostics == 1:
                raise urllib.error.HTTPError(path, 404, "not yet", {}, None)
            return {"buildOutcome": {"status": "ok"},
                    "timing": {"spans": [{"name": "agent-turn.1", "retry_reason": "typecheck"}]}}
        return {}

    def post(self, path, body=None, timeout=120.0):
        self.calls.append(("POST", path, body))
        if path == "/threads":
            return {"id": "th_1"}
        if path == "/threads/th_1/handoff/plan":
            return {"handoff": {"status": "planned", "planId": "plan_7"}, "appName": "Signal Room"}
        if path == "/threads/th_1/handoff/confirm":
            return {"handoff": {"status": "bound", "appId": "app_1", "planId": "plan_7"}}
        if path == "/threads/th_1/crossing":
            return {"appId": "app_1", "refused": []}
        return {"id": "row"}

    def stream(self, path, body):
        self.calls.append(("STREAM", path, body))
        want, events = self.streams.pop(0)
        assert path == want, f"asked for {path}, the run's next step is {want}"
        return {"X-Sage-Turn-Id": f"turn_{len(STREAMS) - len(self.streams)}"}, events


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(demo, "LEDGER", tmp_path / "ledger.jsonl")
    return tmp_path


def fake_run(label="t1"):
    sage, probed = FakeSage(), []

    def probe(url):
        probed.append(url)
        return dict(PROBES[len(probed) - 1])

    run = demo.Demo(label, demo.parse_models(demo.DEFAULT_MODELS), sage,
                    base="http://127.0.0.1:8080/u/p/notebookSession/r1", probe=probe,
                    sleep=lambda s: None)
    run.prepare()
    run.go()
    run.finish()
    return run, sage, probed


def test_a_run_makes_the_workbench_calls_in_the_scripts_order(runs):
    _, sage, probed = fake_run()
    acts = [(m, p) for m, p, _ in sage.calls if m != "GET"]
    ctx = ("POST", "/threads/th_1/context")
    assert acts == [
        ("POST", "/project/model"), ("POST", "/threads"),
        ctx, ("STREAM", CHAT),                                    # 1 picks its table
        ctx, ctx, ("STREAM", CHAT),                               # 2 picks a table and the Dataset
        ("POST", "/threads/th_1/context/dataset/ds_pb/file"), ("STREAM", CHAT),
        ("STREAM", CHAT),                                         # 3: its table is already a chip
        ctx, ("STREAM", CHAT), ("STREAM", CHAT), ("STREAM", CHAT), ("STREAM", CHAT),
        ("POST", "/threads/th_1/handoff/plan"), ("POST", "/threads/th_1/handoff/confirm"),
        ("STREAM", APPROVE),
        ("STREAM", BUILD), ("STREAM", APPROVE),                   # 8 and its plan
        ctx, ctx, ("STREAM", BUILD),                              # 9 picks @haiku and @sonnet
        ("POST", "/threads/th_1/crossing"), ("STREAM", BUILD),    # Add them, and build again
        ("STREAM", BUILD),                                        # 9b
    ]
    assert not sage.streams
    assert not any("publish" in p for _, p, _ in sage.calls)
    assert probed == ["http://127.0.0.1:8080/u/p/notebookSession/r1/preview/app_1/"] * 5
    assert sage.app == "app_1"


def test_the_bodies_are_the_ones_the_workbench_sends(runs):
    _, sage, _ = fake_run()
    bodies = [(p, b) for m, p, b in sage.calls if m != "GET"]
    model = bodies[0][1]
    assert model == {"mode": "auto", "chat_model": "haiku",
                     "catalog": {"plan": {"model": "sonnet", "effort": "medium"},
                                 "implement": {"model": "haiku", "effort": None}}}
    table = bodies[2][1]
    assert table["kind"] == "data_source" and table["bindingKey"] == ["data_source", "ds_sf"]
    assert table["scope"] == {"database": "DWH", "schema": "MARTS", "table": "SFDC__OPPORTUNITY_ENHANCED"}
    assert table["resourceId"] == "table:ds_sf:DWH.MARTS.SFDC__OPPORTUNITY_ENHANCED"
    assert table["inBuild"] is False
    pick = next(b for p, b in bodies if p.endswith("/file"))
    assert pick == {"path": "cards/battlecards.md"}
    replay = [b for p, b in bodies if p == CHAT][2]
    assert replay["skipDatasetGate"] is True
    confirm = next(b for p, b in bodies if p.endswith("/confirm"))
    assert confirm == {"include": {"resources": True, "artifacts": True, "transcript": False},
                       "target": {"appId": "", "name": "Signal Room"}}
    approve = next(b for p, b in bodies if p == APPROVE)
    assert approve["plan_id"] == "plan_7" and approve["conversation"] == "th_1"
    assert approve["answers"] == "Which deals count as open? Every stage before Closed."
    assert "- No editing of Salesforce records.\n\n## Done when" in approve["plan_edits"]
    assert [b for p, b in bodies if p == APPROVE][1]["plan_id"] == "plan_8"
    nine = [b for p, b in bodies if p == BUILD][1]
    assert {"kind": "llm_alias", "id": "haiku", "name": "haiku"} in nine["resources"]
    alias = [b for p, b in bodies if p == "/threads/th_1/context"][-1]
    assert alias["kind"] == "llm_alias" and alias["inBuild"] is True
    assert [b for p, b in bodies if p == BUILD][2]["prompt"] == "replay me"


def test_the_record_keeps_numbers_and_never_model_or_plan_text(runs):
    fake_run()
    kept = (runs / "runs" / "t1" / "record.json").read_text()
    assert PROSE not in kept and PLAN_TEXT not in kept
    script = demo.load_script()
    assert not any(t["prompt"] in kept for t in [*script["chat"], script["handoff"], *script["build"]])
    record = json.loads(kept)
    turns = record["turns"]
    assert [t["step"] for t in turns] == [
        "chat 1", "chat 2", "chat 2: after the file card", "chat 3", "chat 4", "chat 5", "chat 6",
        "chat 7", "handoff: approve", "build 8", "build 8: approve", "build 9",
        "build 9: Add them and build", "build 9b"]
    assert [t["n"] for t in turns] == [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15]
    assert turns[0]["promptSha256"] == script["chat"][0]["sha256"]
    assert turns[0]["checks"] == {"artifacts": {"chart": 1}, "expected": "chart", "artifactWritten": True}
    assert turns[2]["checks"]["artifactWritten"] is True
    assert turns[3]["checks"]["artifactWritten"] is False
    assert turns[5]["checks"]["tool"] == {"label": "search", "events": 2, "called": True}
    assert turns[6]["checks"]["tool"] == {"label": "deal desk", "events": 1, "called": True}
    assert turns[7]["handoffOffered"] is True
    assert record["handoff"]["notDoingEdited"] is True and record["handoff"]["planId"] == "plan_7"
    assert record["handoff"]["appNamedSignalRoom"] is True
    assert turns[8]["diagnostics"]["repairs"] == ["typecheck"]
    assert "probe" not in turns[9] and turns[10]["probe"]["tabs"][0]["invisibleCharts"] == 1
    assert record["crossings"] == [{"step": "build 9", "refused": 0, "replayed": True}]
    ledger = (runs / "ledger.jsonl").read_text().splitlines()
    assert len(ledger) == 15 and json.loads(ledger[8])["step"] == "handoff: write a plan"


def test_the_summary_names_clean_builds_the_probe_found_something_in(runs):
    fake_run()
    summary = json.loads((runs / "runs" / "t1" / "summary.json").read_text())
    builds = [t for t in summary["turns"] if t["kind"] == "build"]
    assert builds[0] == {"n": 10, "step": "handoff: approve", "kind": "build", "decision": "built",
                         "ok": True, "verification": "passed",
                         "stages": {"typecheck": "passed", "page": "passed"},
                         "repairs": 1, "repairKinds": {"typecheck": 1}, "clean": True, "probe": []}
    assert summary["chatChecksFailed"] == ["chat 3: no chart written"]
    assert summary["cleanButProbeFound"] == [
        {"step": "build 8: approve", "findings": [{"tab": "Usage drift", "invisibleCharts": 1}]},
        {"step": "build 9b", "findings": [{"tab": "Product insights", "missingColumns": ["asks.sample"]}]},
    ]
    assert summary["builds"] == 5 and summary["cleanBuilds"] == 5


def test_a_turn_past_the_bound_is_refused_before_anything_is_sent(runs):
    demo.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    demo.LEDGER.write_text("{}\n" * demo.BOUND)
    sage = FakeSage()
    run = demo.Demo("t2", demo.parse_models(demo.DEFAULT_MODELS), sage, base="http://x",
                    probe=lambda url: {}, sleep=lambda s: None)
    run.prepare()
    run.tid = "th_1"
    with pytest.raises(demo.BoundReached):
        run.turn("chat 1", CHAT, {"prompt": "p"}, "p", "chat")
    with pytest.raises(demo.BoundReached):
        run.spend("handoff: write a plan")
    assert not [c for c in sage.calls if c[0] == "STREAM"]
    assert len(demo.LEDGER.read_text().splitlines()) == demo.BOUND


def test_the_client_refuses_to_publish_before_any_request():
    client = demo.Workspace("http://127.0.0.1:9/api")
    for method, call in (("POST", lambda: client.post("/publish", {})),
                         ("GET", lambda: client.get("/publish-status"))):
        with pytest.raises(PermissionError, match=method):
            call()


def test_a_prompt_that_drifted_from_its_digest_is_refused(tmp_path):
    doc = json.loads(demo.DEMO.read_text())
    doc["chat"][0]["prompt"] += " "
    (tmp_path / "demo.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="prompt 1"):
        demo.load_script(tmp_path / "demo.json")


def test_the_committed_prompts_match_their_digests():
    script = demo.load_script()
    assert [t["id"] for t in [*script["chat"], script["handoff"], *script["build"]]] == [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "9b"]


def test_models_parse_from_one_flag():
    assert demo.parse_models("chat=gpt-5.4@low,implement=sonnet") == {
        "chat": {"model": "gpt-5.4", "effort": "low"},
        "plan": {"model": "sonnet", "effort": "medium"},
        "implement": {"model": "sonnet", "effort": None}}
    with pytest.raises(ValueError, match="ask=haiku"):
        demo.parse_models("ask=haiku")


def test_mention_tokens_read_the_way_the_composer_reads_them():
    assert demo.mention_tokens_in("Use @a.csv. and @b, then @c.d and x@y @") == {"@a.csv", "@b", "@c.d"}
    assert demo.mention_tokens_in("follow @revops-conventions.") == {"@revops-conventions"}


def test_the_plan_edit_lands_at_the_end_of_not_doing_or_not_at_all():
    plan = "# P\n\n## Not doing\n* one\n* two\n\n## Done when\n- x\n"
    assert demo.add_not_doing(plan, "No editing.") == (
        "# P\n\n## Not doing\n* one\n* two\n* No editing.\n\n## Done when\n- x\n")
    assert demo.add_not_doing("# P\n\n## Done when\n- x\n", "No editing.") is None


# ---- the probe process: a hard timeout, and nothing left running ----------------------------------

def test_a_probe_that_overruns_is_killed_and_reaped():
    hang = [sys.executable, "-c", "import threading; threading.Event().wait()"]
    seen = []
    real = subprocess.Popen

    def spy(*a, **kw):
        proc = real(*a, **kw)
        seen.append(proc)
        return proc

    result = demo.run_probe(hang, timeout=0.2, popen=spy)
    assert result == {"error": "probe timed out after 0.2s", "reaped": True}
    assert seen[0].returncode is not None


def test_a_probe_report_is_read_and_a_missing_one_is_an_error():
    ok = demo.run_probe([sys.executable, "-c", "print('{\"tabs\": []}')"], timeout=30)
    assert ok == {"tabs": [], "reaped": True}
    bad = demo.run_probe([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(4)"],
                         timeout=30)
    assert bad == {"error": "probe exited 4 without a report: boom", "reaped": True}


# ---- the probe's four detections, in a real headless Chromium ---------------------------------------

_UNAVAILABLE = page_check.unavailable()
real_browser = pytest.mark.skipif(_UNAVAILABLE is not None,
                                  reason=f"real headless probe: {_UNAVAILABLE}")

# Two top-level tabs; the second shows the defect, the first is the control. The nested tab inside
# the first panel is not top-level and must not be clicked.
PAGE = """<!doctype html><html><body>
<div role="tablist"><button role="tab" id="t1">First</button><button role="tab" id="t2">Second</button></div>
<div role="tabpanel" id="p1">
  <div role="tablist"><button role="tab" onclick="throw new Error('nested tab clicked')">Inner</button></div>
  <table><tbody><tr><td>a</td><td>1</td></tr></tbody></table>
</div>
<div role="tabpanel" id="p2" hidden>%s</div>
<script>
document.getElementById('t1').onclick = () => { p1.hidden = false; p2.hidden = true; };
document.getElementById('t2').onclick = () => { p1.hidden = true; p2.hidden = false; %s };
</script></body></html>"""

# The Highcharts object the probe reads: `charts[]`, each with `renderTo` and visible `series` whose
# `points` carry a `graphic.element`. The real library is not in the repo; these are real SVG marks.
CHART = """<div id="c1"><svg width="200" height="100">
  <rect id="m1" x="10" y="10" width="20" height="50" fill="none"></rect>
  <rect id="m2" x="40" y="10" width="20" height="50" fill="#123456" fill-opacity="0"></rect>
  <rect id="m3" x="70" y="10" width="0" height="50" fill="#123456"></rect>
</svg></div>
<div id="c0"><svg width="200" height="100"><rect id="ok" x="0" y="0" width="5" height="5" fill="red"></rect></svg></div>"""
CHART_JS = """const pt = (id) => ({ graphic: { element: document.getElementById(id) } });
window.Highcharts = { charts: [undefined,
  { renderTo: c1, series: [{ visible: true, points: [pt('m1'), pt('m2'), pt('m3')] }] },
  { renderTo: c0, series: [{ visible: true, points: [pt('ok')] }] }] };"""

CASES = {
    "runtime": ("", "setTimeout(() => { throw new Error('boom'); }); console.error('bad');",
                {"runtimeErrors": 2}),
    "crash": ("<main><h2>The app crashed while rendering</h2></main>", "", {"crashScreen": True}),
    "column": ("", """fetch('./api/preview/runtime-error', {method: 'POST', body: JSON.stringify(
                 {message: "query deals has no column 'amount'; columns are id, stage"})});""",
               {"missingColumns": ["deals.amount"]}),
    "chart": (CHART, CHART_JS, {"invisibleCharts": 1}),
    "table": ("""<table><tbody><tr><td> </td><td>1</td></tr><tr><td></td><td>2</td></tr>
                 <tr class="ant-table-measure-row"><td>m</td></tr></tbody></table>""", "",
              {"blankFirstColumnTables": 1}),
}


class _Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.strip("/").split("/")[-1]
        # Anything else (the favicon) is an empty 204, so no 404 reaches the console as an error.
        body = (PAGE % CASES[name][:2]).encode() if name in CASES else b""
        self.send_response(200 if body else 204)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def pages():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    thread = threading.Thread(target=server.serve_forever, name="probe-pages")
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/preview/app_1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@real_browser
@pytest.mark.parametrize("case", ["runtime", "crash", "column", "chart", "table"])
def test_the_probe_reports_each_defect_on_the_tab_that_shows_it(pages, case):
    command = ["node", str(demo.PROBE), f"{pages}/{case}", str(page_check.find_chromium()), "200"]
    result = demo.run_probe(command, timeout=60)
    assert result.get("error") is None, result
    assert [t["tab"] for t in result["tabs"]] == ["First", "Second"]
    found = [{k: v for k, v in t.items() if k != "tab" and v} for t in result["tabs"]]
    assert found == [{}, CASES[case][2]]
