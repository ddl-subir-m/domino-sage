"""A chart is a picture of the rows, so it follows the rows (#255, ADR-0045).

With **Kept rows** off the PNG is written, renders in the session that drew it, and never reaches
git. The mechanism is one ignore rule at the Project root — `examples/**/*.png` — rather than a
refusal to write or a promise to leave the file untracked: the gate Chat writes through reads
**write tool names**, and a `savefig` from the agent's shell never passes through it, while an
untracked file is one `git add -A` away from being committed anyway. A rule matched on path holds
regardless of who wrote the file or how.

With Kept rows on the rule comes out and charts are committed, as today.

This is #222 reopened on purpose, for exactly the charts that matter. The half of #222 that stays
fixed is the `/examples` line beside it: tables, statements and the manifest still commit, so
`_unignore_chat_artifacts` still owns that line and this rule must not disturb it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import sage.orchestrator.service as svc
from sage.workspace import git

from .test_a_restarted_builder_still_has_the_charts_its_conversations_made import (
    CHART,
    _chart,
    _git,
    _orch,
    _repo,
)

RULE = "examples/**/*.png"
EXAMPLES = "/examples"


def _ignored(root: Path) -> list[str]:
    return (root / ".gitignore").read_text().split()


# ---- off: written, shown, not committed ---------------------------------------------------------


def test_a_chart_is_written_and_not_committed_while_kept_rows_is_off(tmp_path: Path):
    """The whole claim, end to end and in git's own answer. The file is on disk — which is why the
    session that drew it still shows the chart — and `ls-files` does not know about it."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    _repo(root)
    _chart(root)

    orch._unignore_chat_artifacts(project)
    git.commit_all(root, "a chat turn")

    chart = "examples/thr_a/chart1_events_by_drug.png"
    assert (root / chart).read_bytes() == CHART  # still on disk, still renders
    assert chart not in set(_git(root, "ls-files").split())


def test_a_savefig_from_the_agents_shell_is_covered_too(tmp_path: Path):
    """The reason the rule is an ignore rule and not an interception. Nothing here goes through a
    write tool: the bytes land the way `matplotlib.savefig` lands them, and the answer is the same
    because the rule matches on path."""
    orch, root = _orch(tmp_path)
    orch._unignore_chat_artifacts(orch.project(start_preview=False))
    _repo(root)

    drawn = root / "examples" / "thr_shell" / "spend_by_month.png"
    drawn.parent.mkdir(parents=True, exist_ok=True)
    drawn.write_bytes(CHART)
    git.commit_all(root, "after a shell turn")

    assert "examples/thr_shell/spend_by_month.png" not in set(_git(root, "ls-files").split())


def test_the_table_beside_the_chart_still_commits(tmp_path: Path):
    """Only the picture follows the rows this way. A `.table.json` commits its shape, the `.sql`
    carries no values, and the manifest is what makes the card exist at all — so the `/examples`
    line `_unignore_chat_artifacts` owns still has to come out."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    _repo(root)
    _chart(root)
    for name in ("events.table.json", "events.sql"):
        (root / "examples" / "thr_a" / name).write_text("{}")

    orch._unignore_chat_artifacts(project)
    git.commit_all(root, "a chat turn")

    tracked = set(_git(root, "ls-files").split())
    assert "examples/thr_a/events.table.json" in tracked
    assert "examples/thr_a/events.sql" in tracked
    assert EXAMPLES not in _ignored(root)


# ---- on: the rule comes out and the charts go in ------------------------------------------------


def test_turning_kept_rows_on_commits_the_charts(tmp_path: Path):
    """The opt-in is what buys #222 back. The chart was ignored, never tracked, so removing the
    rule is the whole of it — the save that records the decision stages the file with it."""
    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    _repo(root)
    _chart(root)

    orch.set_kept_rows(True)

    assert RULE not in _ignored(root)
    assert "examples/thr_a/chart1_events_by_drug.png" in set(_git(root, "ls-files").split())


def test_turning_kept_rows_back_off_ignores_the_next_chart(tmp_path: Path):
    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    _repo(root)
    orch.set_kept_rows(True)

    orch.set_kept_rows(False)

    assert RULE in _ignored(root)


def test_turning_kept_rows_back_off_stops_committing_the_charts_already_committed(tmp_path: Path):
    """An ignore rule says nothing about a file git already tracks, and this is the order the
    switch is most likely to be moved in: on, charts pushed, then off on finding out. Without this
    every one of them is committed again on the next save, and the sentence beside the switch is
    false for the Project it matters most to.

    The bytes stay on disk — the Conversation that drew them still shows them — and they stay in
    history, which is the loss ADR-0046 says is not undoable. What stops is the next commit."""
    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    _repo(root)
    orch.set_kept_rows(True)
    _chart(root)
    git.commit_all(root, "with the opt-in on")
    chart = "examples/thr_a/chart1_events_by_drug.png"
    assert chart in set(_git(root, "ls-files").split())

    orch.set_kept_rows(False)

    assert chart not in set(_git(root, "ls-files").split())
    assert (root / chart).read_bytes() == CHART  # still on disk, still on screen


def test_only_the_charts_are_untracked(tmp_path: Path):
    """The table beside the chart commits either way, so taking it out of the index would be a
    second loss this decision never asked for."""
    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    _repo(root)
    orch.set_kept_rows(True)
    _chart(root)
    (root / "examples" / "thr_a" / "events.table.json").write_text("{}")
    git.commit_all(root, "with the opt-in on")

    orch.set_kept_rows(False)

    assert "examples/thr_a/events.table.json" in set(_git(root, "ls-files").split())


def test_a_workspace_inside_somebody_elses_repo_does_not_touch_its_index(tmp_path: Path):
    """The local-dev lie (#20). Sage's own source tree tracks plenty of PNGs, and a root that is
    not its own repo would have the enclosing one answer about them."""
    outer = tmp_path / "outer"
    root = outer / "mnt" / "code"
    (root / "examples" / "thr_a").mkdir(parents=True)
    (root / "examples" / "thr_a" / "chart1_events_by_drug.png").write_bytes(CHART)
    _repo(outer)
    _git(outer, "add", "-A")
    _git(outer, "commit", "-q", "-m", "the enclosing repo")
    tracked = "mnt/code/examples/thr_a/chart1_events_by_drug.png"
    assert tracked in set(_git(outer, "ls-files").split())

    orch = _orch(outer)[0]
    orch.project(start_preview=False)

    assert tracked in set(_git(outer, "ls-files").split())
    # And the guard is what does it: `ls-files` run from the workspace root DOES see the enclosing
    # repo's copy of these paths, so without `is_repo_root` there is something here to remove.
    from sage.workspace import git as gitmod
    assert gitmod.tracked_under(root, "examples")


def test_the_rule_is_applied_on_the_way_in_rather_than_only_on_the_toggle(tmp_path: Path):
    """A Project decided this once, possibly in another Builder. The next one reads the answer out
    of the committed settings file and writes the rule its own working tree needs."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert RULE in _ignored(root)

    project.record.set_kept_rows(True)
    orch._unignore_chat_artifacts(project)
    assert RULE not in _ignored(root)


# ---- the file people also edit stays valid ------------------------------------------------------


def test_toggling_either_way_leaves_the_rest_of_the_ignore_file_alone(tmp_path: Path):
    orch, root = _orch(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("# mine\nnode_modules\ndist\n")
    orch.project(start_preview=False)

    orch.set_kept_rows(True)
    orch.set_kept_rows(False)

    kept = (root / ".gitignore").read_text()
    assert "# mine" in kept
    assert {"node_modules", "dist"} <= set(kept.split())
    assert kept.endswith("\n") and "\n\n" not in kept


def test_the_rule_is_written_once_however_many_times_it_is_asked_for(tmp_path: Path):
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    orch._unignore_chat_artifacts(project)
    orch._unignore_chat_artifacts(project)
    assert _ignored(root).count(RULE) == 1


def test_a_project_that_keeps_its_charts_out_of_git_by_hand_is_not_overridden(tmp_path: Path):
    """Turning the opt-in on says what Sage may commit, not what the person's own rules may not.
    Only the line Sage wrote comes out."""
    orch, root = _orch(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("*.png\nexamples/thr_private/\n")
    orch.project(start_preview=False)

    orch.set_kept_rows(True)

    assert {"*.png", "examples/thr_private/"} <= set(_ignored(root))


def test_a_read_only_volume_does_not_take_the_project_down(tmp_path: Path, monkeypatch):
    """Same floor as the un-ignore beside it: this runs while `self._project` is assigned, so a
    raise here would 500 the request that triggered the attach and then be cached away."""
    
    orch, _root = _orch(tmp_path)
    project = orch.project(start_preview=False)

    def refuse(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(svc, "ensure_ignore_line", refuse)
    orch._unignore_chat_artifacts(project)  # no raise


# ---- what the restored transcript is told -------------------------------------------------------


def test_only_a_chart_a_project_declined_to_keep_is_given_the_reason(tmp_path: Path):
    """`missing` is a `stat` and answers "is it here?". `notKept` answers "because of this
    decision?", and only a PNG in a Project that did not opt in earns it — the card that names the
    setting must not be shown to a Project that turned it on and lost a file some other way."""
    from sage.workspace.threads import ThreadStore

    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    store = ThreadStore(root)
    thread = store.create()["id"]
    store.record_artifact(thread, path=f"examples/{thread}/spend_by_month.png")
    store.record_artifact(thread, path=f"examples/{thread}/spend.table.json")

    off = {r["name"]: r.get("notKept") for r in orch.get_thread(thread)["artifacts"]}
    orch.set_kept_rows(True)
    on = {r["name"]: r.get("notKept") for r in orch.get_thread(thread)["artifacts"]}

    assert off == {"spend_by_month.png": True, "spend.table.json": None}
    assert on == {"spend_by_month.png": None, "spend.table.json": None}


def test_a_handoff_never_names_a_chart_that_is_not_there(tmp_path: Path):
    """A card may say a chart is not here; a digest that names one is a wrong instruction. The
    handoff digest is the only record of the link between a Conversation and the app it built
    (ADR-0006), and the Build agent reads the paths in it as files it can open."""
    from sage.workspace.threads import ThreadStore

    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(root)
    thread = store.create()["id"]
    store.record_artifact(thread, path=f"examples/{thread}/spend_by_month.png")
    store.record_artifact(thread, path=f"examples/{thread}/spend.table.json")
    (root / "examples" / thread).mkdir(parents=True, exist_ok=True)
    (root / "examples" / thread / "spend.table.json").write_text("{}")

    named = orch._handoff_sheet_payload(store, thread, project, "# Plan\n", {})["artifacts"]

    assert [r["name"] for r in named] == ["spend.table.json"]


def test_the_turn_prompt_never_names_a_chart_that_is_not_there(tmp_path: Path):
    """The @mention picker is one door to a dead path and the smaller one. The prompt lists every
    Artifact the Thread wrote under "Already written in this Thread" and tells the model to read
    what it lists, on every turn — so a restart in a Project with Kept rows off would hand over the
    same dead path again and again."""
    from sage.workspace.threads import ThreadStore

    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    store = ThreadStore(root)
    thread = store.create()["id"]
    store.record_artifact(thread, path=f"examples/{thread}/spend_by_month.png")
    store.record_artifact(thread, path=f"examples/{thread}/spend.table.json")
    (root / "examples" / thread).mkdir(parents=True, exist_ok=True)
    (root / "examples" / thread / "spend.table.json").write_text("{}")

    said = orch._chat_prompt(thread, "chart it again", {"items": []},
                             artifacts=store.read_artifacts(thread))

    assert "spend.table.json" in said
    assert "spend_by_month.png" not in said


def test_a_restored_transcript_is_told_which_artifacts_it_does_not_have(tmp_path: Path):
    """The manifest commits, the chart does not, so a reopened Conversation holds a row naming a
    file that is not there. The row reads the same either way, so the card cannot tell on its own —
    and a card that finds out by letting an `<img>` fail has already drawn the broken image."""
    from sage.workspace.threads import ThreadStore

    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    store = ThreadStore(root)
    thread = store.create()["id"]
    store.record_artifact(thread, path=f"examples/{thread}/spend_by_month.png")
    store.record_artifact(thread, path=f"examples/{thread}/spend.table.json")
    (root / "examples" / thread).mkdir(parents=True, exist_ok=True)
    (root / "examples" / thread / "spend.table.json").write_text("{}")

    said = {row["name"]: row.get("missing") for row in orch.get_thread(thread)["artifacts"]}

    assert said == {"spend_by_month.png": True, "spend.table.json": None}


def test_a_chart_in_the_session_that_drew_it_is_not_called_missing(tmp_path: Path):
    """The loss is a restart, not the turn. A person watching their own chart appear sees it."""
    from sage.workspace.threads import ThreadStore

    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    store = ThreadStore(root)
    thread = store.create()["id"]
    store.record_artifact(thread, path=f"examples/{thread}/spend_by_month.png")
    drawn = root / "examples" / thread / "spend_by_month.png"
    drawn.parent.mkdir(parents=True, exist_ok=True)
    drawn.write_bytes(CHART)

    assert orch.get_thread(thread)["artifacts"][0].get("missing") is None


def test_the_transcript_the_card_is_drawn_from_carries_the_absence_too(tmp_path: Path):
    """The manifest feeds the gallery; a reopened Conversation is rebuilt from the event log. The
    card comes from the log, so the answer has to be on both or the sentence never lands."""
    from sage.workspace.threads import ThreadStore

    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    store = ThreadStore(root)
    thread = store.create()["id"]
    gone = f"examples/{thread}/spend_by_month.png"
    store.append_history(thread, {"type": "artifacts", "items": [{"name": "c.png", "path": gone}]})
    store.append_history(thread, {"type": "done", "artifacts": [{"name": "c.png", "path": gone}]})

    history = orch.get_thread(thread)["history"]

    assert [ev["items"][0]["missing"] for ev in history if ev["type"] == "artifacts"] == [True]
    assert [ev["artifacts"][0]["missing"] for ev in history if ev["type"] == "done"] == [True]


def test_a_candidate_frames_rows_are_not_called_missing(tmp_path: Path):
    """`items` is on candidate frames too, and those rows name tables rather than files. Reading
    every list with a path in it would have marked them all."""
    from sage.workspace.threads import ThreadStore

    orch, root = _orch(tmp_path)
    orch.project(start_preview=False)
    store = ThreadStore(root)
    thread = store.create()["id"]
    store.append_history(thread, {"type": "candidates", "items": [{"path": "DWH.MARTS.SPEND"}]})

    assert orch.get_thread(thread)["history"][0]["items"][0] == {"path": "DWH.MARTS.SPEND"}


# ---- and the block the restored transcript draws ------------------------------------------------

_JS_HARNESS = Path(__file__).resolve().parent / "js" / "table_artifact_harness.mjs"


def _open_thread(thread: dict) -> list[dict]:
    import json
    import shutil

    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    steps = [{"thread": thread}, {"open": thread["id"]}]
    out = subprocess.run(["node", str(_JS_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])[-1]["images"]


def test_reopening_the_conversation_hands_the_card_the_absence_and_the_day():
    """The join, driven through the real `openThread`. The server marks the row; this is the walk
    that has to carry the mark onto the block the card is given."""
    art = {"id": "art_1", "kind": "chart", "name": "spend_by_month.png",
           "title": "spend by month", "path": "examples/thr_a/spend_by_month.png",
           "producedAt": "2026-03-04T09:15:00Z", "missing": True}
    images = _open_thread({
        "id": "thr_a",
        "history": [{"type": "user", "text": "chart it"},
                    {"type": "artifacts", "items": [art]}],
        "artifacts": [art],
    })

    assert len(images) == 1
    assert images[0]["missing"] is True
    assert images[0]["producedAt"] == "2026-03-04T09:15:00Z"
    assert images[0]["title"] == "spend by month"


def test_a_chart_the_clone_does_have_is_an_ordinary_image_block():
    art = {"id": "art_1", "kind": "chart", "name": "spend_by_month.png",
           "title": "spend by month", "path": "examples/thr_a/spend_by_month.png",
           "producedAt": "2026-03-04T09:15:00Z"}
    images = _open_thread({
        "id": "thr_a",
        "history": [{"type": "artifacts", "items": [art]}],
        "artifacts": [art],
    })

    assert images[0]["missing"] is False
    assert images[0]["src"]
