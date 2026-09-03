"""The client half of ADR-0027: one chip, one drawer, one toast.

The server composes every Problem's sentence and this side renders them. So what is under test here
is placement and restraint rather than wording: the chip is absent when nothing is wrong, the toast
points instead of telling, the drawer sorts by who owns the remedy, and NOTHING anywhere goes grey
because a Problem is true.

That last one is the decision's own load-bearing claim. A Problem can be wrong — a permission-cache
blip reads as a missing Alias — and an informer that is wrong costs attention where a blocker that
is wrong costs the session. The two places Sage does refuse an act are older, server-side, and
untouched.

Driven through a Node harness because every question here is about the tree the browser builds, and
three of them are about a sequence: the toast fires once per Problem per session, so the second
Preflight is the interesting one.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_JS = _BACKEND / "sage" / "workbench" / "js"
API = (_JS / "api.js").read_text()
STORE = (_JS / "store.js").read_text()
SHELL = (_JS / "components" / "shell.js").read_text()
PROBLEMS = (_JS / "components" / "problems.js").read_text()
ASSIGNMENTS = (_JS / "components" / "model-assignments.js").read_text()
INDEX = (_BACKEND / "sage" / "workbench" / "index.html").read_text()
SHELL_CSS = (_BACKEND / "sage" / "workbench" / "css" / "shell.css").read_text()
TOKENS_CSS = (_BACKEND / "sage" / "workbench" / "css" / "tokens.css").read_text()

# Two Problems, one per owner, and the administrator's carries a quoted platform body — which is
# what the drawer has to keep out of Sage's own sentences.
MINE = {
    "id": "slot:plan",
    "message": "Sage's plan model is set to the LLM Alias ghost-model, which this account cannot use.",
    "fix": "Pick a different model for that slot.",
    "owner": "you",
}
THEIRS = {
    "id": "gateway",
    "message": "Sage cannot reach the LLM Gateway. Nothing will build until it answers.",
    "fix": "Ask your administrator to check the LLM Gateway.",
    "owner": "admin",
    "body": "ConnectError: [Errno 111] Connection refused",
}
BOTH = [MINE, THEIRS]


def _run(steps: list[dict]) -> list[dict]:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "problem_chip_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps(steps),
        check=False, capture_output=True, text=True, timeout=90,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---- the chip is absent when nothing is wrong ----------------------------------------------------


def test_a_deployment_with_nothing_wrong_draws_no_chip():
    """The whole reason a standing fault can have a permanent home in a bar this crowded."""
    (clean,) = _run([{"problems": []}])
    assert clean["chip"] is None
    # And the row says nothing about it either — not a cleared state, not an "all good" tick.
    assert not [w for w in clean["topnavWords"] if "problem" in w.lower()]
    assert clean["toasts"] == []


def test_a_problem_that_cleared_takes_the_chip_with_it():
    """It goes silent by itself, which is why it needs no dismiss: dismissal is for repetition."""
    lit, cleared = _run([{"problems": BOTH}, {"problems": []}])
    assert lit["chip"] is not None
    assert cleared["chip"] is None
    assert cleared["drawer"]["empty"] == ["Nothing needs your attention right now."]


# ---- the toast points, and points once ----------------------------------------------------------


def test_the_toast_fires_once_for_a_problem_and_not_again_this_session():
    first, second, third = _run([{"problems": BOTH}, {"problems": BOTH}, {"problems": BOTH}])
    assert len(first["toasts"]) == 1
    assert second["toasts"] == []
    assert third["toasts"] == []
    # The chip is still lit through all three. Silence is about the toast, never about the fault.
    assert [step["chip"]["ariaLabel"] for step in (first, second, third)] == (
        ["2 problems need your attention"] * 3)


def test_a_second_problem_arriving_toasts_for_itself_alone():
    """Counted per Problem, not per Preflight: the one already on the chip must not be re-announced,
    and the new one must not be swallowed by it."""
    first, second = _run([{"problems": [MINE]}, {"problems": BOTH}])
    assert first["toasts"] == [
        "1 problem needs your attention. Open the problem chip in the top bar to read it."]
    assert second["toasts"] == [
        "1 problem needs your attention. Open the problem chip in the top bar to read it."]
    # Two on the chip, one in the toast: the chip holds the standing count, the toast the new one.
    assert second["chip"]["ariaLabel"] == "2 problems need your attention"


def test_the_toast_carries_a_count_and_never_the_problems_words():
    """ADR-0011 stands: a toast may point at content, it may never BE the content. Five seconds is
    not long enough to read a fault, a remedy and a quoted platform body and decide."""
    (lit,) = _run([{"problems": BOTH}])
    (toast,) = lit["toasts"]
    assert "2" in toast
    for problem in BOTH:
        assert problem["message"] not in toast
        assert problem["fix"] not in toast
    assert THEIRS["body"] not in toast
    # And it says where the content is, so an attention pull has somewhere to go.
    assert "chip" in toast


def test_the_chip_carries_a_count_and_never_the_problems_words_either():
    """Icon-only, so the count is in the tooltip and in the label a screen reader gets. The row is
    chrome; a sentence somebody has to read belongs in the drawer."""
    (lit,) = _run([{"problems": BOTH}])
    assert lit["chip"]["tooltip"] == "2 problems need your attention. Open to read them."
    for problem in BOTH:
        assert problem["message"] not in " ".join(lit["topnavWords"])


def test_one_problem_is_counted_in_the_singular():
    (one,) = _run([{"problems": [MINE]}])
    assert one["chip"]["ariaLabel"] == "1 problem needs your attention"
    assert one["chip"]["tooltip"] == "1 problem needs your attention. Open to read it."


# ---- the drawer groups by owner -----------------------------------------------------------------


def test_the_drawer_groups_by_who_owns_the_remedy():
    """`owner` exists because most of the six are not the creator's to fix — and a creator still has
    to know, because those failures land on their build. Theirs first, so the group a reader can act
    on is the one they meet."""
    (lit,) = _run([{"problems": BOTH}])
    groups = lit["drawer"]["groups"]
    assert [g["title"] for g in groups] == ["Yours to fix", "Your administrator’s to fix"]
    assert groups[0]["said"] == [MINE["message"], MINE["fix"]]
    assert groups[1]["said"] == [THEIRS["message"], THEIRS["fix"]]


def test_a_group_with_nothing_in_it_is_not_drawn():
    """"Yours to fix — none" is a heading that promises a reader something to read."""
    (admin_only,) = _run([{"problems": [THEIRS]}])
    assert [g["title"] for g in admin_only["drawer"]["groups"]] == ["Your administrator’s to fix"]


def test_the_platforms_own_words_stay_inside_the_quotation():
    """Through the existing block, so a passed-through body reads as attribution rather than as
    Sage's own sentence (ADR-0014). Never rewritten, and never mixed into our half."""
    (lit,) = _run([{"problems": BOTH}])
    admin = lit["drawer"]["groups"][1]
    assert admin["quoted"] == [THEIRS["body"]]
    assert THEIRS["body"] not in " ".join(admin["said"])
    # And a Problem with nothing quoted gets no empty box.
    assert lit["drawer"]["groups"][0]["quoted"] == []


def test_a_problem_is_never_drawn_in_the_transcript():
    """A deployment fault rendered in a Conversation reads as the assistant's answer. Wrong
    provenance, and ADR-0027 rejects it by name."""
    # Two readers of the list, and that is the whole of it: the chip and the drawer it opens.
    readers = sorted(
        path.name for path in sorted((_JS / "components").glob("*.js"))
        if "problems" in path.read_text() or "openProblems" in path.read_text()
    )
    assert readers == ["problems.js", "shell.js"]
    # No transcript block type for one, in either mode's renderer.
    blocks = (_JS / "components" / "message-blocks.js").read_text()
    assert "problem" not in blocks.replace("sw-plan-card-problem", "")


# ---- inform only ---------------------------------------------------------------------------------


def test_nothing_goes_grey_because_a_problem_is_true():
    """The decision's load-bearing claim. A chip that also locked controls would turn one dead
    service into a jail — which is the boot failure this whole ADR started from, where a dead
    Project listing gave a full-page wall to somebody who could still have built."""
    clean, lit = _run([{"problems": []}, {"problems": BOTH}])
    assert clean["disabled"] == []
    assert lit["disabled"] == clean["disabled"]


def test_the_chip_is_the_only_thing_a_problem_adds_to_the_row():
    """Not a banner, not a greyed mode, not a second copy of the sentence."""
    clean, lit = _run([{"problems": []}, {"problems": BOTH}])
    added = [w for w in lit["topnavWords"] if w not in clean["topnavWords"]]
    assert added == [
        "2 problems need your attention. Open to read them.",
        "2 problems need your attention",
    ]


def test_the_chip_cannot_be_dismissed():
    """Dismissal is for repetition, and one element that is either lit or absent does not repeat. A
    dismissable chip would let somebody hide a dead model slot and report the build it broke."""
    chip = SHELL.split("function ProblemChip")[1].split("function TopNav")[0]
    for control in ("closable", "onClose", "dismiss", "Dismiss", "Hide"):
        assert control not in chip
    # The store has no writer for it either, so nothing else can hide it on the chip's behalf.
    assert "dismissProblem" not in STORE


def test_no_new_refusal_reaches_the_client():
    """The two that exist are `_turn_slot_refusal` and `publish_problems`, both server-side, both
    older than this, and both staying. This side adds none."""
    for gate in ("problems.length &&", "problems.length ?", "if (state.problems.length) return",
                 "problems.length > 0 &&"):
        assert gate not in STORE
        assert gate not in SHELL


def test_the_per_alias_problem_in_the_model_panel_is_left_alone():
    """A different question with no overlap: that one is prevention at PICK time — this model will
    not answer, so the menu row is offered and refused. The chip is about slots already assigned."""
    assert "sw-assignment-problem" in ASSIGNMENTS
    assert "state.problems" not in ASSIGNMENTS
    assert "openProblems" not in ASSIGNMENTS


# ---- when the Preflight runs ---------------------------------------------------------------------


def test_the_boot_asks_twice_because_survival_is_counted_on_the_server():
    """A Problem is reported only when the Preflight before it found the same one, and that count is
    process-wide. So one ask can never report anything on a Workbench that is first through the
    door, whatever is wrong — and the chip would be dead code."""
    boot = STORE.split("async init()")[1].split("// Scope ---")[0]
    assert boot.count("refreshProblems()") == 2
    assert "PREFLIGHT_SETTLE_MS" in boot


def test_nothing_polls_for_problems():
    """One gateway listing multiplied by every open Workbench, forever, to learn what the next turn
    reports for free. ADR-0027 rejects it by name."""
    for line in STORE.splitlines():
        if "refreshProblems" in line:
            assert "setInterval" not in line, line
    # The one timer it does set is the boot's second ask, and it is a `setTimeout`.
    assert STORE.count("setTimeout(() => store.refreshProblems()") == 1


def _preflights(frames: list[dict]) -> int:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "problem_preflight_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps({"frames": frames}),
        check=False, capture_output=True, text=True, timeout=90,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])["preflights"]


def test_a_turn_that_answered_pays_for_no_preflight():
    assert _preflights([
        {"type": "delta", "text": "Six million rows.", "final": True},
        {"type": "agent", "kind": "text", "text": "Six million rows."},
        {"type": "done", "ok": True, "decision": "answered"},
    ]) == 0


def test_a_turn_that_failed_asks_once_however_many_frames_said_so():
    """One failed turn, one Preflight. A turn that fell over on its tenth tool call is still one."""
    assert _preflights([
        {"type": "error", "message": "The model did not answer."},
        {"type": "error", "message": "And again."},
        {"type": "done", "ok": False, "decision": "failed"},
    ]) == 1


def test_a_turn_stopped_on_purpose_asks_for_nothing():
    """Stop, Cancel, a context change and every gate decision are turns that ended as asked. Asking
    after one of those is the background poll again, arrived at by somebody pressing buttons."""
    assert _preflights([{"type": "stopped", "message": "Stopped."},
                        {"type": "done", "ok": False, "decision": "stopped"}]) == 0
    assert _preflights([{"type": "done", "ok": False, "decision": "cancelled"}]) == 0
    assert _preflights([
        {"type": "error", "contextChanged": True, "prompt": "how many rows?",
         "message": "Your context changed since you asked this."},
        {"type": "done", "ok": False, "decision": "context changed"},
    ]) == 0


def test_a_turn_waiting_on_a_person_is_not_a_failed_turn():
    """The five gate decisions. Each one stopped to ask the reader something, and each one leaves a
    card on screen with a button on it — the turn did not go wrong, it is waiting."""
    for decision in ("awaiting approval", "architecture ready", "reset offered",
                     "incoming changes", "model unavailable"):
        assert _preflights([{"type": "done", "ok": False, "decision": decision}]) == 0, decision


def test_a_failed_turn_asks_again_and_a_deliberate_ending_does_not():
    """A turn that has just failed is the one moment after boot when the answer is worth a gateway
    listing — and very often the reason the turn failed. A Stop, a Cancel, a context change and
    every gate decision are turns that ended on purpose; re-asking after one of those is the poll
    again, arrived at by somebody pressing buttons."""
    ended = STORE.split("function endedBadly")[1].split("\n  }")[0]
    assert "ev.type === 'error'" in ended
    asked_for = STORE.split("const ASKED_FOR = ")[1].split(";")[0]
    for deliberate in ("stopped", "cancelled", "context changed", "plan moved on",
                       "GATE_DECISIONS"):
        assert deliberate in asked_for
    # And a turn refused before its stream opened is covered too — that is where the older
    # `_turn_slot_refusal` lands, on exactly the dead slot the chip is about.
    assert STORE.count("store.refreshProblems();") >= 3


# ---- the wiring ----------------------------------------------------------------------------------


def test_the_readiness_probe_and_the_problem_route_are_two_different_calls():
    """Both were called `health`, which is how /healthz came to be read for one field of its body
    while the composed Problems went unasked for by anybody at all."""
    assert "health: () => request('/health')" in API
    assert "healthz: async () => {" in API
    assert "fetch('./healthz')" in API
    # The boot reads the probe for the picker's open-weight list, under its own name.
    assert "SW.api.healthz()" in STORE
    assert "healthz.open_weight_models" in STORE


def test_the_drawer_reuses_the_one_platform_error_block():
    assert "SW.PlatformError" in PROBLEMS
    # Loaded after the file that defines it, or it is undefined when the page reads this one.
    assert INDEX.index("problems.js") > INDEX.index("platform-error.js")


def test_the_chip_is_an_icon_target_a_pointer_can_hit():
    """Icon-only, so a tooltip is mandatory and the target owes a pointer at least 24px."""
    chip = SHELL.split("function ProblemChip")[1].split("function TopNav")[0]
    assert "Tooltip" in chip
    assert "aria-label" in chip
    assert "sw-icon-btn" in chip  # the shared target every other control on this row uses
    shared = TOKENS_CSS.split(".sw-icon-btn {")[1].split("}")[0]
    assert "width: 30px" in shared and "height: 30px" in shared
    assert ".sw-problem-chip" in SHELL_CSS


def test_the_chip_sits_in_row_one_on_the_right():
    """A deployment-scoped control belongs away from the modes that are normal work, and away from
    Row 2, which is scoped to one project — the call `manageUrl` already made for itself. A Problem
    outlives the project you are standing in."""
    (lit,) = _run([{"problems": BOTH}])
    assert lit["chip"]["row"] == "topnav"
    # Past the spacer, which is what puts it in the right-hand cluster with the account controls.
    assert lit["chip"]["rightOf"] == "sw-topnav-spacer"
    # Row 2 knows nothing about it.
    subnav = SHELL.split("function SubNav")[1].split("function Dock")[0]
    assert "Problem" not in subnav and "problems" not in subnav
