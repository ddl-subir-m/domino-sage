"""#460 (ADR-0064): unsent work is a standing condition, and it is reported as a Problem.

#456 recorded a save that did not reach the remote and nothing drew it. `grep -rn "saveFailed"
backend/sage/workbench/` returned nothing, so a person's Chat work could be committed locally,
never sent, and findable only in dev tools.

What ships is not that field rendered. Three things rule it out and each has a test here: a save
commits the WHOLE tree so no Conversation can be named truthfully, the field is one in-memory slot
that dies with the process, and a refused push answers `ok: True` so the obvious client check draws
nothing. The condition is read from git instead — `git.unsent()`, refs a push or a fetch already
left behind — and `saveFailed` becomes the trigger that makes the server look.

The end of the chain is the drawer, so the chain is driven to the drawer: a real repo whose remote
refuses, through the real route, into the real store, chip and drawer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.resources.health import OWNER_YOU, UNSENT_WORK, problems, unsent_problem
from sage.workspace import git

from .test_a_problem_says_what_broke_and_who_owns_it import CLEAN
from .test_incoming_changes import _git, _orch, _project, _repo

REFUSED = "push failed: remote: error: hook declined to update refs/heads/main"
# What `_save_to_git` hands back on a refusal, and what the thread door receives. `ok` is TRUE
# here: that is the whole trap (ADR-0064), and only `rejected` says the remote said no.
REFUSED_SAVE = {"type": "saved", "ok": True, "pushed": False, "rejected": True, "detail": REFUSED}
# What the flush route answers. `landed` is `_chat_save_landed`'s verdict, composed server-side so
# the client never re-derives it — see `test_the_flush_door_reads_the_servers_verdict`.
REFUSED_FLUSH = {**REFUSED_SAVE, "landed": False}


def _refuse_pushes(tmp: Path) -> Path:
    """Make `_repo`'s bare remote decline a push while still serving a fetch. #459's fixture: a
    dead URL is a different bug, because `git.pull` raises there and the save already answers
    `ok: False`. A healthy remote that says no is the shape `rejected=True` actually describes."""
    hook = tmp / "remote.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    return hook


def _drawer(steps: list[dict]) -> list[dict]:
    """The real store, chip and drawer against a scripted `/api/health`, one step per Preflight."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "problem_chip_harness.mjs"
    out = subprocess.run(["node", str(harness)], input=json.dumps(steps),
                         check=False, capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _kicks(steps: list[dict]) -> dict:
    """What these steps, in order, cost in Preflights — and which of them threw at the caller."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "save_failed_preflight_harness.mjs"
    out = subprocess.run(["node", str(harness)], input=json.dumps({"steps": steps}),
                         check=False, capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _preflights(steps: list[dict]) -> int:
    return _kicks(steps)["preflights"]


# ---- the sentence ---------------------------------------------------------------------------


def test_unsent_work_is_one_problem_the_creator_owns():
    p = unsent_problem({"unsent": True, "detail": REFUSED})
    assert (p.id, p.owner) == (UNSENT_WORK, OWNER_YOU)
    # Never one Conversation. A save commits the whole tree, so naming one understates the blast
    # radius and sends a person to re-type one thing while the rest is equally unsent.
    for word in ("conversation", "Conversation", "thread", "Thread"):
        assert word not in p.message, p.message
    # And the remedy is a sentence, not a control: the drawer has never held an action, and a
    # Retry button there is its own decision made for all Problem kinds at once.
    assert p.fix.strip().endswith(".")


def test_gits_own_words_travel_in_the_quotation_and_nowhere_else():
    p = unsent_problem({"unsent": True, "detail": REFUSED})
    # Verbatim (ADR-0014). The half a person forwards to whoever owns the remedy.
    assert p.body == REFUSED
    assert REFUSED not in p.message and REFUSED not in p.fix


def test_the_condition_still_reports_after_the_words_are_gone():
    """The whole point of deriving it. `_last_push_refusal` dies with the process and the commits
    do not, so a container restart must cost the quotation and never the Problem."""
    p = unsent_problem({"unsent": True, "detail": None})
    assert p.id == UNSENT_WORK
    # Absent rather than empty: a reader is never shown a quotation with nothing in it.
    assert "body" not in p.to_dict()


def test_a_workspace_with_everything_sent_says_nothing():
    assert unsent_problem({"unsent": False, "detail": None}) is None
    assert problems(**CLEAN) == []


def test_a_git_read_that_did_not_answer_says_nothing_either():
    """The line on silence. `{}` is the route's fallback when the read itself failed, and "we could
    not check" may never be reported as "this is broken" — that is the one thing a Preflight may
    not do. It is also a DIFFERENT state from clean, which is why the fallback is not `False`."""
    assert unsent_problem({}) is None
    assert unsent_problem({"detail": REFUSED}) is None
    assert problems(**{**CLEAN, "unsent": {}}) == []


def test_the_id_carries_nothing_that_changes():
    """Constraint 3, at the unit. The toast fires once per id per session and survival is counted
    per id, so an id carrying the commit count or the branch head would mint a new Problem on every
    commit — re-toasting, resetting the count, and never surviving two Preflights."""
    ids = {unsent_problem({"unsent": True, "detail": d}).id
           for d in (None, REFUSED, "push failed: something else entirely")}
    assert ids == {UNSENT_WORK}
    assert UNSENT_WORK == "workspace-unsent-work"


# ---- read from git, not from the flag -------------------------------------------------------


def test_the_read_answers_from_git_and_carries_gits_words(tmp_path: Path):
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    assert orch.unsent_work() == {"unsent": False, "detail": None}

    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("a person's afternoon\n")
    saved = orch._save_to_git(project, "chat (turn)")
    # The trap the whole ticket exists for: the save that stranded the work answers `ok: True`.
    assert saved["ok"] is True and saved["rejected"] is True

    answer = orch.unsent_work()
    assert answer["unsent"] is True
    assert "push failed" in answer["detail"]

    # Constraint 6, and the only test that can tell the two designs apart. A container restart
    # takes the in-memory record with it and leaves every commit exactly where it was, so a reader
    # that had simplified back to the flag goes quiet here while the work is still in one place.
    orch._last_push_refusal = None
    assert orch.unsent_work() == {"unsent": True, "detail": None}
    # Put it back. Left cleared, this line stands in for the clearing below and the assertion
    # after the successful push passes whether or not anything clears it — a plant that proved
    # exactly that (#460).
    orch._last_push_refusal = answer["detail"]

    (tmp_path / "remote.git" / "hooks" / "pre-receive").unlink()
    orch._save_to_git(project, "chat (turn)")
    # Clears on its own once the push lands. Nobody acknowledges it, and the stale quotation goes
    # with it rather than outliving the condition it described.
    assert orch.unsent_work() == {"unsent": False, "detail": None}


def test_a_branch_that_was_never_pushed_reports_it_too(tmp_path: Path):
    """The worst case, and the one a naive mirror is silent on. `incoming()` reads a missing
    `origin/<branch>` as nothing to pull, which is right for it; here it means nothing has EVER
    been sent. Asserted through the Problem rather than the reader, because a reader that answers
    True and a route that never asks it look identical from a person's seat."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)
    _git(root, "checkout", "-q", "-b", "a-branch-nobody-has-seen")
    (root / "work.txt").write_text("an afternoon nobody else has\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "sage: local work")

    answer = orch.unsent_work()
    assert answer["unsent"] is True
    # Never pushed, so nothing ever refused: the condition stands with no quotation to carry.
    assert answer["detail"] is None
    assert unsent_problem(answer).id == UNSENT_WORK


def test_a_workspace_with_nobody_to_send_to_reports_nothing(tmp_path: Path):
    """A Project with no remote and a `/tmp` workspace that is not a repo root are both as saved as
    anyone can be. Neither is saved through a push, so neither can be owing one."""
    solo = tmp_path / "solo"
    solo.mkdir()
    _git(solo, "init", "-q")
    _git(solo, "config", "user.email", "t@t")
    _git(solo, "config", "user.name", "t")
    (solo / "f.txt").write_text("hi\n")
    _git(solo, "add", "-A")
    _git(solo, "commit", "-q", "-m", "one")
    orch, _oc = _orch(tmp_path, solo)
    _project(orch)
    assert orch.unsent_work()["unsent"] is False
    assert unsent_problem(orch.unsent_work()) is None


def test_an_uncommitted_tree_that_is_not_ahead_reports_nothing(tmp_path: Path):
    """Constraint 5. Sage commits on a timer, so an uncommitted file is ordinary and passes in
    seconds. Reporting it would cry wolf and teach people to ignore the chip."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)
    (root / "half-typed.txt").write_text("not committed yet\n")
    assert orch.unsent_work()["unsent"] is False


def test_a_project_that_is_not_bound_yet_is_unknown_and_not_clean(tmp_path: Path):
    """`{}`, the same "we could not check" the route's own fallback means — never `unsent: False`.

    The read cannot bind a Project of its own: it runs on the idle timer's thread and on every
    door that leaves Chat, and `project()` seeds a Project and can start a preview. But the
    commits are on the volume whether or not anything has bound it, so answering False would
    record a CLEAN Preflight about a workspace nobody had looked at. Both boot Preflights can land
    in that window, because the read that normally binds first falls back silently on the startup
    502 — and two false cleans are enough that the survival count never starts and the chip stays
    dark over work that exists in one place only.
    """
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    assert orch._project is None
    assert orch.unsent_work() == {}
    # Which is silence, and silence for the right reason: unknown, not clean.
    assert unsent_problem(orch.unsent_work()) is None


def test_a_sync_that_could_not_be_resolved_replaces_the_quotation(tmp_path: Path):
    """The stale-quotation path. `_save_to_git` returns before `git.push` when the pull could not
    be resolved, and `unsent()` is still True there — so whatever the LAST attempt said would be
    quoted under a Problem whose real blocker is a merge nobody could finish. A credential
    refusal read as the current cause sends a person to check credentials that are fine."""
    from sage.workspace import git as gitmod

    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("a person's afternoon\n")
    orch._save_to_git(project, "chat (turn)")
    assert "push failed" in orch.unsent_work()["detail"]

    class _Unresolved:
        status, detail, conflicts = "conflict-unresolved", "App.tsx still has markers", ["App.tsx"]

    monkey = orch._integrate_remote
    orch._integrate_remote = lambda _p: _Unresolved()
    try:
        (root / "more.txt").write_text("more\n")
        orch._save_to_git(project, "chat (turn)")
    finally:
        orch._integrate_remote = monkey

    answer = orch.unsent_work()
    assert answer["unsent"] is True, "the commits are still here, so the Problem still stands"
    assert "couldn't sync" in answer["detail"]
    assert "push failed" not in answer["detail"]
    assert gitmod.unsent(root) is True


def test_every_push_keeps_the_quotation_current_not_just_the_save_door(tmp_path: Path):
    """`git.push` has three call sites in this class and the Problem does not care which one ran.

    A Chat save is refused and stores refusal A. Pull and build then pushes successfully through
    its OWN site — `unsent()` goes False and the Problem goes with it, but A would sit in the slot
    until something overwrote it, ready to be quoted under the next refusal as though it were the
    reason. One recorder, every site.
    """
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("a person's afternoon\n")
    orch._save_to_git(project, "chat (turn)")
    assert "push failed" in orch.unsent_work()["detail"]

    # The other door sends it. Driven through `sync()` — Pull and build — rather than through the
    # recorder, because the recorder being right says nothing about whether that site calls it.
    (tmp_path / "remote.git" / "hooks" / "pre-receive").unlink()
    result = orch.sync()
    assert result["pushed"] is True, result
    assert git.unsent(root) is False
    assert orch.unsent_work() == {"unsent": False, "detail": None}


def test_the_flush_route_answers_whether_the_work_landed(tmp_path, monkeypatch):
    """The retry path the remedy names, answering the one question its caller has. Composed by the
    server because three of the four unhappy shapes carry no `rejected` key and `ok` is True for
    the one that does — `_chat_save_landed` is the only place that has ever had this right."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)
    _, client = _client(tmp_path, monkeypatch, orch)

    # Nothing dirty and nothing owing: the ordinary answer on every mode switch.
    assert client.post("/api/threads/save").json()["landed"] is True

    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("a person's afternoon\n")
    orch._chat_dirty = True
    body = client.post("/api/threads/save").json()
    # The trap, in the payload: `ok` says True and the work is not on the remote.
    assert body["ok"] is True
    assert body["landed"] is False
    assert orch.unsent_work()["unsent"] is True


# ---- through the route ----------------------------------------------------------------------


def _client(tmp_path, monkeypatch, orch):
    """The route over a real workspace, with the other six reads answering clean.

    The house pattern `CLEAN` already sets next door: a test naming one fault is a test where that
    fault is the only reason anything was reported. Without this a test repo reports a missing
    `domino_data` and an agent list a fake never loaded, and the chip counts three.
    """
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod
    from sage.resources.health import SAGE_AGENTS

    monkeypatch.setattr(appmod, "orchestrator", orch)
    # Both are process-wide state the route reads and writes. Through monkeypatch so one test's
    # Preflight cannot leave its verdict, or its survival count, behind for another.
    monkeypatch.setattr(appmod, "PREFLIGHT_SLOTS", dict(appmod.PREFLIGHT_SLOTS))
    monkeypatch.setattr(appmod, "_PREFLIGHT_SEEN", set())
    monkeypatch.setattr(orch, "resolved_agents", lambda: [{"name": n} for n in SAGE_AGENTS])
    monkeypatch.setattr(appmod, "data_library_ready", lambda: "")
    return appmod, TestClient(appmod.control_app)


def _stranded(tmp_path: Path):
    """A workspace holding commits a healthy remote refused."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("a person's afternoon\n")
    orch._save_to_git(project, "chat (turn)")
    return root, orch, project


def test_two_commits_between_sweeps_are_one_problem_under_one_id(tmp_path, monkeypatch):
    """Constraint 3, where it actually bites. One commit cannot tell a constant id from a volatile
    one: a volatile id is also unreported on its first sighting, so a single commit gives the same
    answer either way. Two commits between the sweeps is what separates them — a volatile id
    reports NOTHING on the second sweep, because the id it was seen under no longer exists."""
    root, orch, _p = _stranded(tmp_path)
    _, client = _client(tmp_path, monkeypatch, orch)

    assert client.get("/api/health").json()["problems"] == []          # seen once

    (root / "more.txt").write_text("and more\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "sage: more")
    (root / "yet-more.txt").write_text("and more again\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "sage: yet more")

    rows = client.get("/api/health").json()["problems"]
    assert [r["id"] for r in rows] == [UNSENT_WORK]
    assert rows[0]["owner"] == "you"
    assert "push failed" in rows[0]["body"]


def test_the_problem_clears_when_the_push_lands_and_nobody_acknowledges_it(tmp_path, monkeypatch):
    _root, orch, project = _stranded(tmp_path)
    _, client = _client(tmp_path, monkeypatch, orch)
    client.get("/api/health")
    assert [r["id"] for r in client.get("/api/health").json()["problems"]] == [UNSENT_WORK]

    (tmp_path / "remote.git" / "hooks" / "pre-receive").unlink()
    orch._save_to_git(project, "chat (turn)")
    assert client.get("/api/health").json()["problems"] == []


def test_a_workspace_whose_git_will_not_answer_costs_this_problem_alone(tmp_path, monkeypatch):
    """Inside `_read`, not above it. A route that reports Problems must not become one, and one
    dead dependency costs its own answer and not the other six."""
    _root, orch, _p = _stranded(tmp_path)
    _, client = _client(tmp_path, monkeypatch, orch)

    def _boom():
        raise RuntimeError("git is not answering here")

    monkeypatch.setattr(orch, "unsent_work", _boom)
    for _ in range(2):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert UNSENT_WORK not in [p["id"] for p in r.json()["problems"]]


def test_the_sweep_issues_no_network_call(tmp_path, monkeypatch):
    """Constraint 7, pinned rather than asserted in a comment. `incoming()`'s discipline: read the
    refs someone else already paid for. A fetch hidden in here would put a round trip on the boot
    of every Workbench, which is the shape ADR-0027 rejects by name."""
    _root, orch, _p = _stranded(tmp_path)
    _, client = _client(tmp_path, monkeypatch, orch)

    seen: list[tuple[str, ...]] = []
    real = git._git
    monkeypatch.setattr(git, "_git",
                        lambda path, *args, **kw: (seen.append(args), real(path, *args, **kw))[1])
    client.get("/api/health")
    client.get("/api/health")
    assert seen, "nothing was recorded — the interception missed"
    assert not [a for a in seen if a[0] in ("fetch", "push", "pull", "ls-remote", "clone")]


# ---- to the drawer --------------------------------------------------------------------------


def test_the_condition_reaches_the_drawer_in_the_group_a_person_can_act_on(tmp_path, monkeypatch):
    """The whole chain, and the acceptance criterion that says a field on a payload is not enough:
    a real repo whose remote refused, through the real route, into the real store and drawer."""
    _root, orch, _p = _stranded(tmp_path)
    _, client = _client(tmp_path, monkeypatch, orch)
    client.get("/api/health")
    rows = client.get("/api/health").json()["problems"]
    assert [r["id"] for r in rows] == [UNSENT_WORK]

    (lit,) = _drawer([{"problems": rows}])
    (group,) = lit["drawer"]["groups"]
    assert group["title"] == "You can fix"
    assert group["said"] == [rows[0]["message"], rows[0]["fix"]]
    # git's words inside the quotation block, and never mixed into Sage's own sentences.
    assert group["quoted"] == [rows[0]["body"]]
    assert rows[0]["body"] not in " ".join(group["said"])
    # Informs and never blocks: nothing on screen goes grey because this is true.
    assert lit["disabled"] == _drawer([{"problems": []}])[0]["disabled"]


def test_the_toast_fires_once_however_many_commits_follow(tmp_path, monkeypatch):
    """The other half of the constant id, and the half only a sequence can show. Three Preflights
    over a workspace that keeps committing announce the Problem once; a volatile id would announce
    it again every time and would be the reason to ignore the chip."""
    root, orch, _p = _stranded(tmp_path)
    _, client = _client(tmp_path, monkeypatch, orch)
    sweeps = []
    for n in range(3):
        (root / f"turn-{n}.txt").write_text("more work\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", f"sage: turn {n}")
        sweeps.append({"problems": client.get("/api/health").json()["problems"]})

    first, second, third = _drawer(sweeps)
    assert first["toasts"] == []                       # nothing survived one sighting yet
    assert len(second["toasts"]) == 1
    assert third["toasts"] == []
    assert second["chip"]["ariaLabel"] == "1 problem"
    assert third["chip"]["ariaLabel"] == "1 problem"


# ---- saveFailed is the trigger ---------------------------------------------------------------


def test_a_failed_save_asks_the_server_to_look_now():
    """Survival needs two consecutive Preflights. Without this kick the second sighting waits for
    the next failed turn or the next boot, so a person can be hours into work that exists in one
    place only before the chip lights. The shape a failed turn already uses."""
    assert _preflights([{"open": {"id": "t1", "saveFailed": REFUSED_SAVE}}]) == 1
    # Two opens, two Preflights: the second sighting is what the chip needs, and the condition is
    # still true because nothing about opening a Conversation sends anything.
    assert _preflights([{"open": {"id": "t1", "saveFailed": REFUSED_SAVE}},
                        {"open": {"id": "t2", "saveFailed": REFUSED_SAVE}}]) == 2


def test_the_retry_the_remedy_names_reports_its_own_refusal():
    """`fix` says "leaving {chat} will retry it", and ADR-0065 made that genuinely push an
    ahead-but-clean workspace. The flush answered with the refusal and `app.js` dropped it on the
    floor, so somebody who followed the sentence literally got the retry and no Preflight — the
    condition still true and still unreported. The same dict, so the same reader."""
    assert _preflights([{"flush": REFUSED_FLUSH}]) == 1
    # And the flush that found nothing owing, which is the ordinary answer on every mode switch.
    assert _preflights([{"flush": {"type": "saved", "ok": True, "pushed": False,
                                   "detail": "nothing to save", "landed": True}}]) == 0


def test_the_flush_door_reads_the_servers_verdict_and_not_the_fields():
    """The client re-deriving this is four chances to be wrong, and the first draft took one.

    Three of the four unhappy answers carry **no `rejected` key at all** — an unresolved merge, a
    raised exception, a save that fell over before git — so a kick testing `rejected` is silent on
    the very case it was added for: somebody leaves Chat with a merge stranding their commits.
    `ok` is the older trap (TRUE for a refused push) and `pushed: False` the louder one (a
    workspace with no remote says it and is saved). `landed` is `_chat_save_landed`, composed once
    on the server, which is the only place that has ever had this right.
    """
    unresolved = {"type": "saved", "ok": False, "pushed": False,
                  "detail": "couldn't sync with the repo — App.tsx still has markers",
                  "landed": False}
    blew_up = {"type": "saved", "ok": False, "pushed": False,
               "detail": "chat save failed", "landed": False}
    for answer in (unresolved, blew_up):
        assert "rejected" not in answer
        assert _preflights([{"flush": answer}]) == 1, answer

    # Saved, no remote: `ok` true, `pushed` FALSE, and it landed as far as anyone can land.
    assert _preflights([{"flush": {"type": "saved", "ok": True, "pushed": False,
                                   "detail": "committed (no remote)", "landed": True}}]) == 0
    # And the reader names none of the three fields a client must not judge on.
    store = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
             / "store.js").read_text()
    reader = store.split("noteSaveFailed(answer) {")[1].split("\n    },")[0]
    for trap in (".ok", ".pushed", ".rejected"):
        assert trap not in reader, (trap, reader)


def test_the_door_spends_two_preflights_on_one_standing_failure_and_no_more():
    """Survival needs two consecutive sightings, so two is what this door is for — and `saveFailed`
    is sticky until a save lands, so without a cap every click through the Conversation rail spends
    another Preflight. Six git spawns and a Binding listing each, on a workspace somebody is
    clicking around precisely BECAUSE it is broken. The route's cost model says boot, failed turn,
    failed save; a per-click probe is not in it."""
    opens = [{"open": {"id": f"t{n}", "saveFailed": REFUSED_SAVE}} for n in range(6)]
    assert _preflights(opens) == 2

    # A different failure gets its own two: the condition changed, so the count starts over.
    other = dict(REFUSED_SAVE, detail="push failed: remote: repository not found")
    assert _preflights(opens + [{"open": {"id": "t9", "saveFailed": other}}]) == 3


def test_a_save_that_landed_starts_the_count_over():
    """Which is what makes the two CONSECUTIVE rather than two ever. A workspace that recovers and
    breaks again is a new standing condition and gets its own sightings — a cap that never reset
    would report the first outage of a session and go quiet for the rest of it."""
    failing = {"open": {"id": "t1", "saveFailed": REFUSED_SAVE}}
    landed = {"open": {"id": "t2", "saveFailed": None}}
    assert _preflights([failing, failing]) == 2
    assert _preflights([failing, failing, failing]) == 2
    assert _preflights([failing, failing, landed, failing, failing]) == 4


def test_a_save_that_landed_asks_for_nothing():
    """`saveFailed` is None on every save that reached the remote, and opening a Conversation is
    not evidence that anything is wrong. A kick on every open is the background poll again."""
    assert _preflights([{"open": {"id": "t1", "saveFailed": None}}]) == 0
    assert _preflights([{"open": {"id": "t1"}}]) == 0


def test_a_preflight_that_went_wrong_cannot_break_the_door_it_was_kicked_from():
    """Fire-and-forget, proven rather than asserted in a comment. `refreshProblems` handles its own
    fetch rejection, but the toast and the render after it are the caller's without a `catch` —
    and the caller here is somebody opening a Conversation."""
    got = _kicks([{"open": {"id": "t1", "saveFailed": REFUSED_SAVE}, "toastThrows": True},
                  {"flush": REFUSED_FLUSH, "toastThrows": True}])
    assert got["rejected"] == []
    assert got["preflights"] == 2


def test_the_leave_chat_flush_hands_its_answer_to_the_reader():
    """The one piece of this a harness cannot drive, and it is named as such rather than dressed up.

    `app.js` fires the flush from a React effect and every harness here stubs `useEffect` to a
    no-op, so this is a source read — the weakest evidence in the file. It is narrow on purpose:
    what the reader DOES with that answer is driven above, for both shapes and both traps. All
    this pins is that the answer is handed over instead of dropped.
    """
    app = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "app.js").read_text()
    effect = app.split("if (route.mode !== 'chat')")[1].split("}, [route.mode]);")[0]
    assert "flushChat()" in effect
    assert "noteSaveFailed" in effect, effect


def test_the_field_is_the_trigger_and_never_the_surface():
    """Constraint 6, and the reason `saveFailed` is still in the payload at all. It is past tense —
    the save that failed already happened — and the glossary rules an error out of being a Problem
    by name. Nothing draws it, and what a person reads is the server's standing sentence."""
    js = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
    readers = sorted(p.name for p in sorted(js.rglob("*.js")) if "saveFailed" in p.read_text())
    # One reader, and it is the store. No component sees the field at all, so there is nothing to
    # render it into a Conversation — where a deployment fault reads as the assistant's answer.
    assert readers == ["store.js"]
    store = (js / "store.js").read_text()
    # Exactly one executable line reads the field, and it is inside the kick — so the field is
    # reacted to and never rendered. Pinned as a COUNT and a location rather than as a literal:
    # what that line may and may not test is `test_the_flush_door_reads_the_servers_verdict...`,
    # which drives it instead of quoting it.
    code = [ln.strip() for ln in store.splitlines()
            if "saveFailed" in ln and not ln.strip().startswith("//")]
    assert len(code) == 1, code
    reader = store.split("noteSaveFailed(answer) {")[1].split("\n    },")[0]
    assert code[0] in reader, code[0]
