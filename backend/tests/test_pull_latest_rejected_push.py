"""#347: Pull latest must not read as clean when the merge works but the push is rejected."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace import git

from .test_incoming_changes import _orch, _project, _repo

_HARNESS = Path(__file__).resolve().parent / "js" / "pull_and_build_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _minimal_orch(tmp: Path, root: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    return Orchestrator(
        workspace_dir=root, template=template, gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage")


def _pull(sync_result: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"syncResult": sync_result}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_sync_keeps_merge_detail_and_rejected_push_detail_separate(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)

    monkeypatch.setattr(orch, "_integrate_remote",
                        lambda project: git.SyncResult("merged", [], "merged teammate changes"))
    monkeypatch.setattr(git, "push", lambda path: git.SaveResult(
        pushed=False, detail="push failed: rejected non-fast-forward", rejected=True))

    result = orch.sync()

    assert result["status"] == "merged"
    assert result["detail"] == "merged teammate changes"
    assert result["pushed"] is False
    assert result["rejected"] is True
    assert result["pushDetail"] == "push failed: rejected non-fast-forward"


def test_sync_no_remote_still_reads_as_success(tmp_path: Path):
    root = tmp_path / "mnt" / "code"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    orch = _minimal_orch(tmp_path, root)
    orch.project(start_preview=False)

    result = orch.sync()

    assert result["status"] == "no-remote"
    assert result["pushed"] is False
    assert result["rejected"] is False
    assert result["pushDetail"] == ""


def test_sync_up_to_date_still_reads_as_success(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)

    monkeypatch.setattr(orch, "_integrate_remote",
                        lambda project: git.SyncResult("up-to-date", [], "already up to date"))
    monkeypatch.setattr(git, "push", lambda path: git.SaveResult(
        pushed=True, detail="pushed", rejected=False))

    result = orch.sync()

    assert result["status"] == "up-to-date"
    assert result["pushed"] is True
    assert result["rejected"] is False
    assert result["pushDetail"] == "pushed"


@needs_node
def test_pull_and_build_stops_and_tells_the_person_when_push_is_rejected():
    result = _pull({
        "status": "merged",
        "conflicts": [],
        "pushed": False,
        "rejected": True,
        "detail": "merged teammate changes",
        "pushDetail": "push failed: rejected non-fast-forward",
    })

    assert result["calls"] == ["syncProject"]
    assert "committed locally" in result["thrown"]
    assert "not on the remote" in result["thrown"]
    assert "Pull latest again" in result["thrown"]
    assert "push failed: rejected non-fast-forward" in result["thrown"]


@needs_node
def test_pull_and_build_keeps_no_remote_as_success():
    result = _pull({
        "status": "no-remote",
        "conflicts": [],
        "pushed": False,
        "rejected": False,
        "detail": "this app has no git remote to pull from",
        "pushDetail": "",
    })

    assert result["calls"] == ["syncProject", "loadApps", "loadBuild", "sendBuildPrompt"]
    assert result["thrown"] == ""


@needs_node
def test_pull_and_build_keeps_up_to_date_as_success():
    result = _pull({
        "status": "up-to-date",
        "conflicts": [],
        "pushed": True,
        "rejected": False,
        "detail": "already up to date",
        "pushDetail": "pushed",
    })

    assert result["calls"] == ["syncProject", "loadApps", "loadBuild", "sendBuildPrompt"]
    assert result["thrown"] == ""
