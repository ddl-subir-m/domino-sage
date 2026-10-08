"""A Build's evidence survives a restart, and is believed only for the build it describes (#698).

When Sage restarted mid-build, an explicit retry rebuilt intent from the plan and the disk but lost
which files the build changed, which checks passed against which code, and what repair it was on.
`.sage/build-evidence.json` keeps exactly that, per app, and the existing recovery packet renders
it. What is pinned here is the trust boundary as much as the memory: only a check Sage ran can be
passed, a check is valid only for the code AND the Sage-owned runtime it ran against, a Sage
refresh is never the model's progress, and a record from another app, conversation or plan — or a
record that cannot be read — is never a basis for recovery.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sage import build_evidence
from sage.build_intent import BuildIntent
from sage.feedback.runner import FeedbackError, FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.workspace import manager

from .fake_opencode import Turn
from .test_retry_an_approved_plan import PLAN, BreakingOpenCode, OkFeedback, ScriptedGateway, _catalog
from .test_turn_path import ALREADY_DONE_REPLY, TABLE_PLAN, _get_built, _run
from .test_turn_path import _build as _turn_build

HELPER = "src/ErrorBoundary.tsx"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


_RED = FeedbackReport(ok=False, errors=[FeedbackError("src/App.tsx", 1, 1, "TS2304",
                                                      "Cannot find name 'x'.")])
_GREEN = FeedbackReport(ok=True, errors=[])


class ScriptedFeedback:
    """Each check answers with the next report; the last one repeats."""

    def __init__(self, *reports: FeedbackReport) -> None:
        self.reports = list(reports)

    def check(self, path: Path) -> FeedbackReport:
        return self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]


def _orch(tmp: Path, turns: list[Turn], *, feedback=None, break_on: set[int] | None = None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = BreakingOpenCode(ws, turns, break_on=break_on)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=feedback or OkFeedback(),
                        opencode_client=oc)
    oc.orch = orch
    orch.project(start_preview=False)
    return orch, oc


def _done(events: list[dict]) -> dict:
    return next(e for e in reversed(events) if e["type"] == "done")


def _app(orch):
    return orch.project(start_preview=False).app_for_turn()


def _record(orch) -> dict:
    return json.loads((_app(orch).path / ".sage" / "build-evidence.json").read_text())


def _restore(orch, record: dict | None = None) -> build_evidence.Restored:
    """Restore with the identity the record was written under, unless the test names another."""
    record = record or _record(orch)
    project = orch.project(start_preview=False)
    return build_evidence.restore(project.app_for_turn(), project.snapshot,
                                  conversation=record["conversationId"],
                                  intent_id=record["intentId"], plan=record["plan"])


def _failed_approved_build(tmp: Path):
    """Plan, approve; the build writes, its typecheck goes red, and the gateway dies on the repair."""
    orch, oc = _orch(tmp, [
        PLAN,
        Turn(writes={"src/App.tsx": "// half a table\n"}),
        Turn(writes={"src/App.tsx": "// still half\n"}),
    ], feedback=ScriptedFeedback(_RED, _GREEN), break_on={3})
    list(orch.build_stream("build me a consumption dashboard"))
    events = list(orch.approve_stream())
    assert _done(events)["decision"] == "gateway error"
    return orch, oc


def test_a_model_reply_that_says_tests_passed_cannot_create_a_passed_check(tmp_path: Path):
    claim = "All tests passed. Typecheck passed. Verified in the preview."
    orch, _oc = _orch(tmp_path, [PLAN] + [
        Turn(text=claim, writes={"src/App.tsx": f"// attempt {i}\n"}) for i in range(8)
    ], feedback=ScriptedFeedback(_RED))
    list(orch.build_stream("build me a consumption dashboard"))
    assert _done(list(orch.approve_stream()))["ok"] is False

    record = _record(orch)
    statuses = {check["kind"]: check["status"] for check in record["checks"]}
    assert record["state"] == "failed"
    assert statuses["typecheck"] == "failed"
    assert "passed" not in statuses.values()
    assert "passed" not in [check["status"] for check in _restore(orch).checks]


def test_a_query_only_change_invalidates_a_saved_check(tmp_path: Path):
    orch, _oc = _orch(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// the table\n"})])
    list(orch.build_stream("build me a consumption dashboard"))
    assert _done(list(orch.approve_stream()))["ok"] is True
    before = _restore(orch)
    assert {"kind": "typecheck", "status": "passed"} in before.checks

    (_app(orch).path / ".sage" / "queries.json").write_text('{"usage": {"sql": "select 1"}}')

    after = _restore(orch)
    assert after.checks == ()
    assert after.stale == len(before.checks)
    assert '"passed"' not in after.supplement


def test_a_sage_owned_helper_refresh_invalidates_a_dependent_check_but_is_not_model_progress(
        tmp_path: Path):
    orch, _oc = _orch(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// the table\n"})])
    list(orch.build_stream("build me a consumption dashboard"))
    assert _done(list(orch.approve_stream()))["ok"] is True
    project, app = orch.project(start_preview=False), _app(orch)
    assert HELPER in app.sage_owned_paths
    saved = _record(orch)
    assert {"kind": "typecheck", "status": "passed"} in _restore(orch, saved).checks
    code_before, runtime_before = build_evidence.digests(app, project.snapshot)
    baseline = project.snapshot.working_tree_hash()

    (app.path / HELPER).write_text("// refreshed by Sage\n")

    code_after, runtime_after = build_evidence.digests(app, project.snapshot)
    assert code_after == code_before
    assert runtime_after != runtime_before
    assert _restore(orch, saved).checks == ()
    recorder = build_evidence.Recorder(app, project.snapshot, conversation="c", attempt_id="t",
                                       intent_id="i", plan=None, baseline=baseline)
    recorder.begin()
    recorder.check("Typecheck", "passed")
    assert _record(orch)["changedFiles"] == []
    (app.path / "src" / "App.tsx").write_text("// the model's edit\n")
    recorder.check("Typecheck", "passed")
    assert [entry["path"] for entry in _record(orch)["changedFiles"]] == ["src/App.tsx"]


def test_a_helper_refresh_with_passed_checks_still_cannot_support_already_done(tmp_path: Path):
    """The previous turn ended ok with passed checks on record, but the only file it changed is one
    Sage owns. The evidence must not make that turn the model's progress."""
    orch, _oc, _gw = _turn_build(tmp_path, [
        Turn(text=TABLE_PLAN),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Refreshed the boundary.", writes={HELPER: "// only Sage's file\n"}),
        Turn(text=ALREADY_DONE_REPLY),
    ], verdict="BUILD")
    _get_built(orch)
    assert _done(_run(orch, "tidy the error boundary"))["ok"] is True
    record = _record(orch)
    assert record["state"] == "complete"
    assert record["changedFiles"] == []
    assert "passed" in {check["status"] for check in record["checks"]}

    events = _run(orch, "tidy the error boundary")

    assert _done(events)["decision"] == "pre_edit_limit"


def test_restart_and_explicit_retry_restore_evidence_without_reviving_an_old_continuation_token(
        tmp_path: Path):
    orch, _oc = _failed_approved_build(tmp_path)
    record = _record(orch)
    assert record["state"] == "failed"
    assert record["activeRepair"] == "typecheck_repair"
    assert [entry["path"] for entry in record["changedFiles"]] == ["src/App.tsx"]
    app_id = _app(orch).app_id
    token = orch.project(start_preview=False).context_continuations.offer(
        conversation=record["conversationId"], app_id=app_id,
        intent=BuildIntent.for_direct("build me a consumption dashboard")).continuation_id

    # The restart: a new process over the same durable workspace. The planning session it resumes
    # was created by the old fake, so the new one writes into the app it is told about.
    ws = tmp_path / "mnt" / "code"
    oc = BreakingOpenCode(_app(orch).path, [Turn(writes={"src/App.tsx": "// the table\n"})])
    restarted = Orchestrator(workspace_dir=ws, template=tmp_path / "template",
                             gateway=ScriptedGateway(), catalog=_catalog(), project_id="Sage",
                             feedback=OkFeedback(), opencode_client=oc)
    oc.orch = restarted
    restarted.project(start_preview=False)
    assert restarted.claim_context_continuation(
        token, record["conversationId"], app_id)[0] == "invalid"

    events = list(restarted.build_stream("try again"))

    assert _done(events)["ok"] is True
    sent = oc.prompts[0]["text"]
    assert 'App-relative paths that attempt changed (JSON array): ["src/App.tsx"]' in sent
    assert "Active repair objective: typecheck_repair" in sent
    assert "Add the consumption table" in oc.intents[0].authoritative_plan
    assert "Add the consumption table" not in sent
    assert restarted.project(start_preview=False).context_continuations.latest() is None


@pytest.mark.parametrize(("field", "value", "diagnostic"), [
    ("appId", "another-app", "wrong_app"),
    ("conversationId", "another-conversation", "wrong_conversation"),
    ("plan.version", 99, "wrong_plan"),
    ("plan.digest", "0" * 64, "wrong_plan"),
])
def test_another_app_conversation_or_plan_cannot_supply_recovery_evidence(
        tmp_path: Path, field: str, value, diagnostic: str):
    orch, _oc = _failed_approved_build(tmp_path)
    expected = _record(orch)
    assert _restore(orch, expected).supplement
    saved = json.loads(json.dumps(expected))
    if field.startswith("plan."):
        saved["plan"][field.split(".", 1)[1]] = value
    else:
        saved[field] = value
    (_app(orch).path / ".sage" / "build-evidence.json").write_text(json.dumps(saved))

    restored = _restore(orch, expected)

    assert restored.supplement == ""
    assert restored.diagnostic == diagnostic


@pytest.mark.parametrize("checks", [
    [{"kind": "x" * 200, "status": "passed", "codeDigest": "", "runtimeDigest": ""}],
    [{"kind": f"k{i}", "status": "passed", "codeDigest": "", "runtimeDigest": ""}
     for i in range(build_evidence.CHECKS_MAX + 1)],
], ids=["unbounded-kind", "too-many-checks"])
def test_a_record_with_unbounded_checks_is_not_rendered(tmp_path: Path, checks: list[dict]):
    orch, _oc = _failed_approved_build(tmp_path)
    expected = _record(orch)
    (_app(orch).path / ".sage" / "build-evidence.json").write_text(
        json.dumps({**expected, "checks": checks}))

    restored = _restore(orch, expected)

    assert restored.supplement == ""
    assert restored.diagnostic == "corrupt"


def test_a_completed_build_of_the_same_plan_is_not_carried_into_the_next_one(tmp_path: Path):
    orch, _oc = _orch(tmp_path, [PLAN])
    project, app = orch.project(start_preview=False), _app(orch)

    def attempt() -> build_evidence.Recorder:
        recorder = build_evidence.Recorder(app, project.snapshot, conversation="c",
                                           attempt_id="t", intent_id="i", plan=None,
                                           baseline=project.snapshot.working_tree_hash())
        recorder.begin()
        return recorder

    first = attempt()
    (app.path / "src" / "App.tsx").write_text("// first attempt\n")
    first.finish("failed")
    attempt().finish("failed")
    assert [entry["path"] for entry in _record(orch)["changedFiles"]] == ["src/App.tsx"]

    attempt().finish("complete")
    attempt()

    assert _record(orch)["changedFiles"] == []


def _oversized() -> str:
    return json.dumps({"schemaVersion": 1, "padding": "x" * build_evidence.STORE_MAX_BYTES})


@pytest.mark.parametrize(("contents", "diagnostic"), [
    (None, "missing"),
    ("{not json", "corrupt"),
    (json.dumps({"schemaVersion": 1, "appId": 7}), "corrupt"),
    (_oversized(), "oversized"),
    (json.dumps({"schemaVersion": 2}), "unsupported_version"),
], ids=["missing", "unparsable", "ill-typed", "oversized", "unsupported-version"])
def test_a_missing_corrupt_or_oversized_record_falls_back_to_intent_plus_disk_recovery(
        tmp_path: Path, caplog, contents: str | None, diagnostic: str):
    orch, _oc = _failed_approved_build(tmp_path)
    expected = _record(orch)
    path = _app(orch).path / ".sage" / "build-evidence.json"
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    project = orch.project(start_preview=False)
    project.active_plan_record_id = expected["plan"]["recordId"]

    with caplog.at_level(logging.INFO, logger="sage.build_evidence"):
        restored = _restore(orch, expected)
        packet = Orchestrator._context_rollover_packet(
            project, BuildIntent.for_direct("build me a consumption dashboard"), "",
            "implementation")

    assert restored.supplement == ""
    assert restored.diagnostic == diagnostic
    assert diagnostic in caplog.text
    assert "Current implementation objective" in packet
    assert "Existing source paths" in packet
    assert "Recorded evidence" not in packet


def test_the_rollover_packet_carries_the_still_valid_evidence(tmp_path: Path):
    orch, _oc = _failed_approved_build(tmp_path)
    expected = _record(orch)
    project = orch.project(start_preview=False)
    project.build_conversation = expected["conversationId"] or project.build_conversation
    project.active_plan_record_id = expected["plan"]["recordId"]

    packet = Orchestrator._context_rollover_packet(
        project, BuildIntent.for_direct("build me a consumption dashboard"), "", "implementation")

    assert 'App-relative paths that attempt changed (JSON array): ["src/App.tsx"]' in packet
    assert "Active repair objective: typecheck_repair" in packet


def test_a_failed_write_disables_evidence_and_leaves_no_staging_file(tmp_path: Path, caplog):
    orch, _oc = _orch(tmp_path, [PLAN])
    project, app = orch.project(start_preview=False), _app(orch)
    (app.path / ".sage" / "build-evidence.json").mkdir(parents=True)
    recorder = build_evidence.Recorder(app, project.snapshot, conversation="c", attempt_id="t",
                                       intent_id="i", plan=None,
                                       baseline=project.snapshot.working_tree_hash())

    with caplog.at_level(logging.WARNING, logger="sage.build_evidence"):
        recorder.begin()
        recorder.check("Typecheck", "passed")

    assert "write failed" in caplog.text
    assert not list((app.path / ".sage").glob(".build-evidence.json.*.tmp"))
    assert build_evidence.restore(app, project.snapshot, conversation="c", intent_id="i",
                                  plan=None).supplement == ""


def test_the_record_stays_out_of_the_projects_commits_while_the_apps_other_records_do_not(
        tmp_path: Path):
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not on PATH")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run([git, "init", "-q", "."], cwd=tmp_path, env=env, check=True)
    (tmp_path / ".gitignore").write_text("\n".join(manager._PROJECT_IGNORE) + "\n")
    evidence, bindings = "apps/a1/.sage/build-evidence.json", "apps/a1/.sage/bindings.json"
    for rel in (evidence, bindings):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    ignored = subprocess.run([git, "check-ignore", evidence, bindings], cwd=tmp_path, env=env,
                             capture_output=True, text=True, check=False).stdout.split()

    assert ignored == [evidence]


def test_the_supplement_stays_inside_its_budget_and_says_what_it_omitted(tmp_path: Path):
    orch, _oc = _orch(tmp_path, [PLAN])
    project, app = orch.project(start_preview=False), _app(orch)
    recorder = build_evidence.Recorder(app, project.snapshot, conversation="c", attempt_id="t",
                                       intent_id="i", plan=None,
                                       baseline=project.snapshot.working_tree_hash())
    recorder.begin()
    for i in range(60):
        (app.path / "src" / f"{'screen' * 30}{i:02d}.tsx").write_text(f"// {i}\n")
    recorder.check("Typecheck", "passed")
    assert len(_record(orch)["changedFiles"]) == 60

    restored = build_evidence.restore(app, project.snapshot, conversation="c", intent_id="i",
                                      plan=None)

    assert 0 < len(restored.supplement.encode("utf-8")) <= build_evidence.SUPPLEMENT_MAX_BYTES
    assert restored.omitted > 0
    assert f"{restored.omitted} more changed path" in restored.supplement
    assert '{"kind":"typecheck","status":"passed"}' in restored.supplement
