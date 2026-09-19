"""ADR-0063 in the transcript: a turn's steps fold behind one face, and a folded row is not read.

The fold is the smaller half. `.table.json` is the only Artifact kind that costs a round trip, and
#451 measured what an investigation's worth of them costs — sixty serial reads through the Domino
proxy before the first card drew. #451 made those reads concurrent; this makes most of them never
happen, which is why it follows.

Driven through `js/working_reads_fold_harness.mjs`, which loads the real store AND the real
transcript components in one sandbox and clicks the real control. Both halves are needed and
neither is enough: a store-only harness can see that a folded row was not fetched but not that
anything exists to fetch it with, and a component-only harness has no store to fetch through.

What decides the role is `test_a_working_read_is_folded_out_of_the_answer.py`. This file takes the
role as given and asks what the person ends up looking at.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "working_reads_fold_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(thread: dict, files: dict, open_fold: bool = False,
         live: list[dict] | None = None, flutter: bool = False,
         kinds: list[dict] | None = None, seed: list[dict] | None = None) -> dict:
    spec = {"thread": thread, "files": files, "open": open_fold, "flutter": flutter}
    if live is not None:
        spec["live"] = live
    if kinds is not None:
        spec["kinds"] = kinds
    if seed is not None:
        spec["seedArtifacts"] = seed
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(spec),
                         check=False, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _thread(thread_id: str, artifacts: list[dict], text: str = "Here is what I found.") -> dict:
    return {"id": thread_id, "history": [{"type": "agent", "kind": "text", "text": text},
                                         {"type": "done", "artifacts": artifacts}]}


def _table(path: str, title: str, role: str | None = None) -> dict:
    row = {"kind": "table", "path": path, "title": title}
    if role is not None:
        row["role"] = role
    return row


def _body(title: str) -> dict:
    return {"title": title, "columns": ["Column", "Rows"], "rows": [["ID", 412]]}


def _files(paths: list[str]) -> dict:
    """Each file titled with its own basename, so a card can be named in an assertion."""
    return {p: {"body": _body(p.rsplit("/", 1)[-1])} for p in paths}


def _types(out: dict) -> list[str]:
    return [b["type"] for b in out["blocks"]]


def _fold(out: dict) -> dict | None:
    return next((b for b in out["blocks"] if b["type"] == "working_reads_fold"), None)


@needs_node
def test_the_steps_of_an_investigation_are_not_read_until_somebody_opens_them():
    """The measured shape, in miniature: many catalogue probes, one answer.

    `beforeOpening` is the whole claim. Every one of those folded files is on disk and readable
    here — the harness would serve them — and the transcript asked for none of them.
    """
    tid = "thr_gong"
    probes = [f"examples/{tid}/probe-{i}.table.json" for i in range(8)]
    answer = f"examples/{tid}/monitoring-customers.table.json"
    out = _run(
        _thread(tid, [_table(p, f"Probe {i}", "working") for i, p in enumerate(probes)]
                + [_table(answer, "Monitoring customers", "answer")]),
        _files(probes + [answer]),
    )

    assert out["beforeOpening"] == [answer], "a folded row was read before anyone asked for it"
    assert _fold(out)["items"] == probes
    assert _fold(out)["count"] == 8
    # The answer still draws, and it is the only card on screen.
    assert out["tables"] == ["monitoring-customers.table.json"]
    assert "Sage read 8 tables to answer this" in out["words"]
    assert "Show the steps" in out["words"]


@needs_node
def test_opening_the_fold_reads_exactly_the_rows_behind_it():
    """The other half of the same claim: deferred, not dropped. The receipt is still reachable —
    a person asking *why do you believe that* has a way back to the read that decided it, which is
    what ADR-0063 refuses to give up by never publishing probes at all."""
    tid = "thr_open"
    probes = [f"examples/{tid}/probe-{i}.table.json" for i in range(3)]
    answer = f"examples/{tid}/answer.table.json"
    out = _run(
        _thread(tid, [_table(p, f"Probe {i}", "working") for i, p in enumerate(probes)]
                + [_table(answer, "Answer", "answer")]),
        _files(probes + [answer]),
        open_fold=True,
    )

    assert out["beforeOpening"] == [answer]
    assert sorted(out["requests"]) == sorted([*probes, answer])
    assert out["tables"] == [*[f"probe-{i}.table.json" for i in range(3)], "answer.table.json"]
    assert "Hide the steps" in out["words"]


@needs_node
def test_a_turn_with_no_steps_draws_no_face():
    """Constraint 3. No empty fold and no "0 tables" — a control that opens onto nothing is the
    dead end the rest of this card exists to avoid."""
    tid = "thr_plain"
    answer = f"examples/{tid}/answer.table.json"
    out = _run(_thread(tid, [_table(answer, "Answer", "answer")]), _files([answer]))

    assert _types(out) == ["text", "table"]
    assert "to answer this" not in out["words"]
    assert out["opens"] is False


@needs_node
def test_a_thread_written_before_the_role_existed_draws_exactly_as_it_did():
    """The load-bearing fallback, seen from the transcript.

    Every Artifact in every current Thread carries no role. A silent default of `working` would
    fold the entire history of every conversation on the day this ships, so an absent role draws —
    and these rows are read on open, exactly as they were.
    """
    tid = "thr_old"
    paths = [f"examples/{tid}/t{i}.table.json" for i in range(4)]
    out = _run(_thread(tid, [_table(p, f"Table {i}") for i, p in enumerate(paths)]),
               _files(paths))

    assert _types(out) == ["text", "table", "table", "table", "table"]
    assert sorted(out["beforeOpening"]) == sorted(paths)
    assert _fold(out) is None


@needs_node
def test_the_fold_sits_where_the_first_step_sat():
    """One fold per turn, in place, so the answer's prose and the answer's tables still read
    continuously down the transcript.

    TWO answers after the steps, not one. With a single trailing answer, "where the first step
    sat" and "at the end of the turn" put the fold in the same place, and the assertion below
    cannot tell them apart — a plant that moved it to the end passed this test unchanged.
    """
    tid = "thr_order"
    first = f"examples/{tid}/a-answer.table.json"
    probe_one = f"examples/{tid}/b-probe.table.json"
    probe_two = f"examples/{tid}/c-probe.table.json"
    middle = f"examples/{tid}/d-answer.table.json"
    last = f"examples/{tid}/e-answer.table.json"
    out = _run(
        _thread(tid, [_table(first, "First", "answer"), _table(probe_one, "Probe one", "working"),
                      _table(probe_two, "Probe two", "working"),
                      _table(middle, "Middle", "answer"), _table(last, "Last", "answer")]),
        _files([first, probe_one, probe_two, middle, last]),
    )

    assert _types(out) == ["text", "table", "working_reads_fold", "table", "table"]
    assert [b["path"] for b in out["blocks"] if b["type"] == "table"] == [first, middle, last]
    assert _fold(out)["items"] == [probe_one, probe_two]


@needs_node
def test_one_step_is_counted_as_one_table():
    """The face is read by a person, so it says "1 table" rather than "1 tables"."""
    tid = "thr_one"
    probe = f"examples/{tid}/probe.table.json"
    answer = f"examples/{tid}/answer.table.json"
    out = _run(_thread(tid, [_table(probe, "Probe", "working"), _table(answer, "A", "answer")]),
               _files([probe, answer]))

    assert "Sage read 1 table to answer this" in out["words"]


@needs_node
def test_two_turns_fold_separately():
    """One fold per turn, not one per Thread. A fold that spanned turns would put a later turn's
    steps above an earlier turn's answer."""
    tid = "thr_two"
    first = [f"examples/{tid}/one-probe.table.json", f"examples/{tid}/one-answer.table.json"]
    second = [f"examples/{tid}/two-probe.table.json", f"examples/{tid}/two-answer.table.json"]
    thread = {"id": tid, "history": [
        {"type": "user", "text": "first question"},
        {"type": "done", "artifacts": [_table(first[0], "P1", "working"),
                                       _table(first[1], "A1", "answer")]},
        {"type": "user", "text": "second question"},
        {"type": "done", "artifacts": [_table(second[0], "P2", "working"),
                                       _table(second[1], "A2", "answer")]},
    ]}
    out = _run(thread, _files(first + second))

    folds = [b for b in out["blocks"] if b["type"] == "working_reads_fold"]
    assert [f["items"] for f in folds] == [[first[0]], [second[0]]]
    assert sorted(out["beforeOpening"]) == sorted([first[1], second[1]])


@needs_node
def test_a_fold_whose_rows_cannot_be_read_offers_the_way_back():
    """A row behind the fold can be unreadable, and it is read at the one moment somebody is
    watching.

    The fold has no failed state of its own, and this is why: `blocksForArtifacts` already catches
    per ROW, so an unreadable file comes back as its own "Open the file" link, in its own place in
    the order, beside the rows that did read. A second failure state over the top of that would be
    a branch the store cannot produce — unrun code that reads as care.
    """
    tid = "thr_broken"
    probe = f"examples/{tid}/probe.table.json"
    answer = f"examples/{tid}/answer.table.json"
    out = _run(
        _thread(tid, [_table(probe, "Probe", "working"), _table(answer, "A", "answer")]),
        {probe: {"fail": "network"}, answer: {"body": _body("answer.table.json")}},
        open_fold=True,
    )

    # The read is attempted, and what came back is a link to the file rather than a card — the
    # per-item fallback `blocksForArtifacts` already had. The fold did not swallow the failure.
    assert probe in out["requests"]
    assert out["files"] == 1
    assert out["tables"] == ["answer.table.json"]


@needs_node
def test_a_live_turn_folds_its_steps_once_and_not_twice():
    """The live reducer is a SECOND reader of a turn's Artifact list, and the server hands it the
    same list twice — once as `artifacts`, once again on the `done` that closes the turn.

    The dedupe that stands between one file and two cards matches on the paths it can see in the
    blocks already on the message, and a FOLDED row is in no block: it draws no card, by design.
    So the repeat sailed through and the turn grew a second face, each one counting the same
    tables — measured, before the fold's own `items` were added to that set.

    Driven live rather than on reload because ADR-0062 was bitten by exactly this gap: a filter
    proved in the three history functions said nothing about the one path a person actually
    watches.
    """
    tid = "thr_live"
    probe = f"examples/{tid}/probe.table.json"
    answer = f"examples/{tid}/answer.table.json"
    artifacts = [_table(probe, "Probe", "working"), _table(answer, "Answer", "answer")]
    out = _run(
        {"id": tid, "history": []},
        _files([probe, answer]),
        live=[{"type": "agent", "kind": "text", "text": "Here is what I found."},
              {"type": "artifacts", "items": artifacts},
              {"type": "done", "artifacts": artifacts, "ok": True}],
    )

    folds = [b for b in out["blocks"] if b["type"] == "working_reads_fold"]
    assert [f["items"] for f in folds] == [[probe]]
    assert out["words"].count("to answer this") == 1
    # And the live turn defers its reads too, not only the reload.
    assert out["beforeOpening"] == [answer]


@needs_node
def test_a_path_a_later_turn_answered_with_is_never_folded_by_an_earlier_probe():
    """One `.table.json` name can be written by two turns, and then one of their verdicts is wrong.

    `live_read_query` slugs an untitled read to `query-result` every time and `result.record`
    overwrites rather than uniquifying, so a probe in turn one and an answer in turn two land on
    the same file. The transcript's dedupe keeps the FIRST row it saw of a repeated path — that is
    older than this ticket and it is what puts one card on screen for one file — so the answer
    would have inherited the probe's `working` and been folded away, with the fold's face counting
    it as a step. The fold's face does not say what was in the rows, so nobody could have found
    out.

    Resolved in the direction ADR-0063 fixes for every other absence: a path that is an answer
    ANYWHERE in the Thread is drawn everywhere in it.
    """
    tid = "thr_reused"
    shared = f"examples/{tid}/query-result.table.json"
    probe = f"examples/{tid}/schemas.table.json"
    thread = {"id": tid, "history": [
        {"type": "user", "text": "which tables hold calls"},
        {"type": "done", "artifacts": [_table(probe, "Schemas", "working"),
                                       _table(shared, "Query result", "working")]},
        {"type": "user", "text": "how many are there"},
        {"type": "done", "artifacts": [_table(shared, "Query result", "answer")]},
    ]}
    out = _run(thread, _files([shared, probe]))

    folds = [b for b in out["blocks"] if b["type"] == "working_reads_fold"]
    assert [f["items"] for f in folds] == [[probe]], "the answer was folded with the probe"
    assert [b["path"] for b in out["blocks"] if b["type"] == "table"] == [shared]
    assert out["beforeOpening"] == [shared]


@needs_node
def test_opening_the_fold_twice_before_it_lands_still_reads_each_row_once():
    """An impatient double-click must not cost a second set of round trips.

    The read fires on a CONDITION — open, and holding nothing — rather than on the click, which is
    right: a fold can be open without anyone having pressed it. But open → shut → open before the
    first read lands puts that condition back exactly as it was, and the rows are still null, so a
    second full batch starts behind the first. On the sixty-read investigation this card is for,
    that is sixty duplicate reads through the Domino proxy — the cost #451 measured and this
    ticket exists to defer.
    """
    tid = "thr_flutter"
    probes = [f"examples/{tid}/probe-{i}.table.json" for i in range(4)]
    answer = f"examples/{tid}/answer.table.json"
    out = _run(
        _thread(tid, [_table(p, f"Probe {i}", "working") for i, p in enumerate(probes)]
                + [_table(answer, "Answer", "answer")]),
        _files([*probes, answer]),
        open_fold=True,
        flutter=True,
    )

    assert sorted(out["requests"]) == sorted([*probes, answer]), (
        f"a row was read more than once: {out['requests']}")


@needs_node
def test_a_fold_whose_rows_are_all_blank_says_so_instead_of_opening_onto_nothing():
    """A `.table.json` that is there but blank gets neither a card nor a link — inside this fold
    exactly as outside it. When every folded row is one of those, the fold opens onto an empty
    bordered box with a Hide button, which reads as a card that failed to load rather than as
    files with nothing in them.

    The face still counts them, because `count` is what the turn WROTE. The sentence is what makes
    the gap between the two numbers readable instead of a dead end."""
    tid = "thr_blank_fold"
    probes = [f"examples/{tid}/probe-{i}.table.json" for i in range(2)]
    answer = f"examples/{tid}/answer.table.json"
    files = {p: {"content": "   "} for p in probes}
    files[answer] = {"body": _body("answer.table.json")}
    out = _run(
        _thread(tid, [_table(p, f"Probe {i}", "working") for i, p in enumerate(probes)]
                + [_table(answer, "Answer", "answer")]),
        files,
        open_fold=True,
    )

    assert "Sage read 2 tables to answer this" in out["words"]
    assert "there is nothing to show here" in out["words"]
    assert out["tables"] == ["answer.table.json"]


@needs_node
def test_every_card_the_fold_can_hold_is_one_the_disclosure_preference_leaves_alone():
    """The fold is a SECOND renderer of Artifact blocks and it does not go through `pushBlock`.

    Every other block on a message is routed through the store's marking pass, which consults
    `HIDDEN_BY_DATA_ACCESS` — the table with no default that `test_the_preference_governs_exactly
    _two_things` protects. `hydrateArtifacts` hands its blocks straight to `SW.MessageBlock`, so
    that table is not consulted behind this face.

    There is no defect today: every kind this builder can emit is a `shown` row. This derives that
    rather than asserting it, so the day one of them flips to hide, it reds here instead of
    hiding everywhere EXCEPT behind a fold, which is the shape nobody would go looking for.
    """
    tid = "thr_kinds"
    kinds = [
        {"kind": "chart", "path": f"examples/{tid}/trend.png", "title": "Trend"},
        {"kind": "table", "path": f"examples/{tid}/summary.table.json", "title": "Summary"},
        {"path": f"examples/{tid}/report.html", "title": "Report"},
        {"path": f"examples/{tid}/notes.txt", "name": "notes.txt"},
    ]
    out = _run({"id": tid, "history": []},
               {f"examples/{tid}/summary.table.json": {"body": _body("Summary")}},
               kinds=kinds)

    drawn = out["foldKinds"]
    assert [k["type"] for k in drawn] == ["image", "table", "page", "file"], (
        f"the builder emits a kind this claim has not been checked against: {drawn}")
    assert [k["hides"] for k in drawn] == [False, False, False, False], (
        f"a card behind the fold would now be hidden everywhere else: {drawn}")


@needs_node
def test_a_live_turn_and_a_reload_agree_about_a_path_an_earlier_turn_answered_with():
    """Two readers of one Artifact list must give one answer.

    The reload pass and the SSE reducer are separate functions over the same rows, and only the
    first had the upgrade that stops an earlier turn's answer being folded by a later turn's
    probe. So the same row folded while the person watched and drew after a refresh — a card that
    appears when you reload is worse than either behaviour on its own, because nothing on screen
    says which one is right.

    Driven live here; `test_a_path_a_later_turn_answered_with_is_never_folded_by_an_earlier_probe`
    is the reload half, and the two assert the same shape on purpose.
    """
    tid = "thr_agree"
    shared = f"examples/{tid}/query-result.table.json"
    out = _run(
        {"id": tid, "history": []},
        _files([shared]),
        # The earlier turn's row, as the session already holds it when the new turn arrives.
        seed=[_table(shared, "Query result", "answer")],
        live=[{"type": "agent", "kind": "text", "text": "Re-checking that."},
              {"type": "artifacts", "items": [_table(shared, "Query result", "working")]},
              {"type": "done", "artifacts": [_table(shared, "Query result", "working")],
               "ok": True}],
        kinds=[],
    )

    assert [b["type"] for b in out["blocks"] if b["type"] == "working_reads_fold"] == [], (
        "the live turn folded a path this Thread had already answered with")
    assert [b["path"] for b in out["blocks"] if b["type"] == "table"] == [shared]
