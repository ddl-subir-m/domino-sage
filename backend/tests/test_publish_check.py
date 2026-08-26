"""A broken query is named before the deploy, not by the first viewer after it (#26).

#15 already runs this check at the end of every build turn, and the sentences go into AGENTS.md so
the agent meets them on its next turn. The gap this closes is the session that has no next turn: the
creator reads the plan, likes it, and publishes. Several minutes of cold start later, someone opening
the app gets a 503 carrying a sentence the creator could have read before any of it started.

So the thing worth pinning here is not that a check exists — `test_bound_schema.py` already covers
`catalog_problems` — but the two properties that make it useful at publish time and easy to lose:
the creator reads the SAME SENTENCE the viewer would (asserted against the running server, not
against a copy of the string), and publishing is NOT refused because of one. This informs. #12's
guards refuse, and the difference between the two is the whole reason this did not go there.

Nothing here reaches a network. The control plane and the Data Sources are fakes; the query check is
local disk and pure Python, which is also why it costs the publish flow nothing.
"""
from __future__ import annotations

import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import httpx

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.resources.builtapp import serve_module
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog

# The real app template, so the checker under test is the file that ships in every published app.
TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"

# `ds-dwh` in the fake is Shared, so #12's guards have nothing to say and a publish here fails or
# succeeds on this issue's terms alone.
SOURCE_ID = "ds-dwh"

# One placeholder the statement uses and the declaration never mentions. Picked because it is the
# mistake an agent actually makes, and because it needs no Binding gymnastics to arrange.
UNDECLARED = {"name": "revenue", "binding": SOURCE_ID,
              "sql": "SELECT REGION, SUM(ARR_USD) FROM FCT_SUBSCRIPTION_REVENUE WHERE MONTH >= :since",
              "params": []}
SOUND = {"name": "usage", "binding": SOURCE_ID,
         "sql": "SELECT ACCOUNT_ID, SEATS_ACTIVE FROM FCT_USAGE_DAILY WHERE USAGE_DATE >= :since",
         "params": [{"name": "since", "type": "date"}]}


def _template(tmp: Path, *, with_serve: bool = True) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    # No `serve.py` named in it: which entry script an app needs is #12's business, and this fixture
    # must be able to drop `serve.py` without the publish failing for a different reason.
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    if with_serve:
        shutil.copy2(TEMPLATE / "serve.py", t / "serve.py")
        (t / "src" / "sageQuery.ts").write_text("// placeholder")
        (t / "src" / "sageBase.ts").write_text("// placeholder")
    return t


def _orch(tmp: Path, cp: FakeControlPlane | None = None, *, with_serve: bool = True) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp, with_serve=with_serve),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(),
        control_plane=cp or FakeControlPlane(),
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)
    orch.bind_data_source(SOURCE_ID, "DWH", "MARTS", "FCT_USAGE_DAILY")
    return orch


def _write_queries(orch: Orchestrator, entries: list) -> Path:
    root = orch.project(start_preview=False).workspace.path
    (root / ".sage").mkdir(exist_ok=True)
    (root / ".sage" / "queries.json").write_text(json.dumps(entries))
    return root


# ---- what the creator is told ---------------------------------------------------------------------


def test_a_query_that_will_not_answer_is_named_before_the_deploy(tmp_path: Path):
    orch = _orch(tmp_path)
    _write_queries(orch, [UNDECLARED])

    result = orch.publish_check()

    assert result["checked"] is True
    assert len(result["queries"]) == 1
    assert "since" in result["queries"][0] and "revenue" in result["queries"][0]


def test_the_creator_reads_the_same_sentence_the_viewer_would(tmp_path: Path):
    # The criterion, proved against the server rather than against a second copy of the string. If
    # these ever drift, the creator is being told about something adjacent to what the app refuses.
    orch = _orch(tmp_path)
    root = _write_queries(orch, [UNDECLARED])

    told = orch.publish_check()["queries"]

    with _running(root) as base:
        r = httpx.post(f"{base}/api/queries/revenue", json={"params": {}})
    assert r.status_code == 503
    assert told == [r.json()["error"]]


def test_every_broken_query_at_once(tmp_path: Path):
    # For the reason a refusal carries every problem: fixing one, publishing, and being told about
    # the next is how a creator discovers their own app one deploy at a time.
    orch = _orch(tmp_path)
    _write_queries(orch, [UNDECLARED, {"name": "orphan", "sql": "SELECT 1", "params": []}])

    assert len(orch.publish_check()["queries"]) == 2


# ---- and what it does NOT do ----------------------------------------------------------------------


def test_a_broken_query_does_not_refuse_the_publish(tmp_path: Path):
    # The line between this and #12. A broken query re-exports nothing and is one screen of an app
    # that may be fine everywhere else, so it is the creator's call — publish reaches the control
    # plane exactly as it would with no queries at all.
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    _write_queries(orch, [UNDECLARED])

    assert orch.publish()["published"] is True
    assert cp.published


def test_publish_check_changes_nothing(tmp_path: Path):
    # A read, so the creator may click Publish, read the warning, go and fix it, and be looking at
    # the same app they were before. Nothing is deployed and nothing is written.
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    root = _write_queries(orch, [UNDECLARED])
    before = sorted((p.name, p.read_bytes()) for p in (root / ".sage").iterdir() if p.is_file())

    orch.publish_check()

    assert sorted((p.name, p.read_bytes()) for p in (root / ".sage").iterdir() if p.is_file()) == before
    assert not cp.published


# ---- the quiet cases, which are the common ones ---------------------------------------------------


def test_a_catalog_that_holds_together_says_nothing(tmp_path: Path):
    orch = _orch(tmp_path)
    _write_queries(orch, [SOUND])

    assert orch.publish_check() == {"checked": True, "queries": []}


def test_an_app_with_no_queries_says_nothing(tmp_path: Path):
    # Every app Sage built before #13, and most of them since. Nothing is added to their publish.
    orch = _orch(tmp_path)

    assert orch.publish_check() == {"checked": True, "queries": []}


def test_an_app_sage_cannot_check_does_not_report_a_clean_bill(tmp_path: Path):
    # A template carrying no `serve.py` — Sage has no checker to run, which is a different answer
    # from "no problems" and is reported as one. The UI publishes on it, because a check that could
    # not run is not a reason to stand between a creator and their app.
    orch = _orch(tmp_path, with_serve=False)
    _write_queries(orch, [UNDECLARED])

    assert orch.publish_check() == {"checked": False, "queries": []}


# ---- over the route -------------------------------------------------------------------------------


def test_the_route_answers_with_the_problems(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    _write_queries(orch, [UNDECLARED])
    monkeypatch.setattr(appmod, "orchestrator", orch)

    r = TestClient(appmod.control_app).get("/api/publish-check")

    assert r.status_code == 200
    assert r.json()["checked"] is True
    assert len(r.json()["queries"]) == 1


def test_the_route_fails_loudly_rather_than_reporting_a_clean_app(tmp_path: Path, monkeypatch):
    # A 502 is what the UI treats as "nothing to say", so this is only about the log and the shape:
    # a checker that raised must not come back as `queries: []` and be read as a clean catalog.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    monkeypatch.setattr(orch, "publish_check", _raise)
    monkeypatch.setattr(appmod, "orchestrator", orch)

    r = TestClient(appmod.control_app, raise_server_exceptions=False).get("/api/publish-check")

    assert r.status_code == 502
    assert "queries" not in r.json()


def _raise() -> dict:
    raise RuntimeError("no")


@contextmanager
def _running(root: Path):
    """The app's own server over this workspace, on a throwaway port. `dist/` need not be built —
    nothing here fetches a page."""
    serve = serve_module(TEMPLATE)
    assert serve is not None
    srv = serve.build_server(root / "dist", host="127.0.0.1", port=0, project_root=root,
                             executor=None)
    t = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)
