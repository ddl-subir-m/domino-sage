import json
from pathlib import Path


def _implement_prompt() -> str:
    config = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    return config["agent"]["sage-implement"]["prompt"]


def test_the_implement_prompt_asks_for_one_round_of_looking_not_two():
    """Discovery, not editing, is what a Build turn spends its round trips on.

    MEASURED 2026-09-11 on the dogfood workspace (`sage_rev` 584ccb9), one small real change
    ("add a footer line under the main chart"), 38.0s, typecheck clean. The turn's six model
    calls, read off `/api/diag/timing`:

        call 1  scope       1.5s                    no tools
        call 2  plan        1.3s    req   3KB       no tools
        call 3  plan        2.0s    req  79KB       read, glob
        call 4  plan        2.8s    req  86KB       read x5
        call 5  plan        4.1s    req 109KB       apply_patch
        call 6  implement   3.0s    req 111KB       no tools

    Exactly ONE `apply_patch` in the whole turn, against SEVEN discovery calls spread over TWO
    whole round trips — the agent located a file in call 3 and only opened what it had found in
    call 4. So "batch the edits" had nothing to batch, and the cut worth making is the second
    round of looking: call 3 cost 2.0s to learn where to read.

    The prompt already said "don't over-explore" while this turn happened, so counting reads is
    not the instruction that works. Naming the ROUND TRIP is: the agent cannot see that a second
    message costs a second call, and reads — unlike edits to one file — have nothing to gain from
    going one at a time.
    """
    prompt = _implement_prompt()
    assert "ONE message" in prompt
    assert "round trip" in prompt


def test_the_implement_prompt_does_not_read_as_permission_to_batch_edits():
    """The workspace AGENTS.md forbids exactly what this rule allows, one step later.

    `template/react-vite/AGENTS.md`: "Send one edit at a time to a given file. Several edits to the
    same file go out in parallel, so every one after the first is applied against a file that
    already changed under it and comes back rejected." A reader who takes "do it all in one
    message" as a general habit races itself on the next file and the turn makes no progress, so
    the read rule has to carry the contrast itself rather than leave the agent to infer it.
    """
    prompt = _implement_prompt()
    assert "edits to a single file, which must go one at a time" in prompt


def test_the_read_rule_is_about_round_trips_and_never_about_which_files():
    """A latency rule must not become the thing that keeps data out of a request.

    A Build turn reads fewer files under this rule, so it also carries less of the user's data to
    the gateway — which is a side effect and not a control. If the rule were ever written as "do
    not read data files", a later edit made for speed would silently reopen a governance hole, and
    the real fix (`read` reaching past the live-read path, caught live on 2026-09-11) would look
    already done. Keep the two apart: this rule counts messages, never names a path.
    """
    prompt = _implement_prompt()
    rule = next(line for line in prompt.split("\n") if "ONE message" in line)
    for forbidden in ("public/data", ".sage/", "dataset", "PII", "sensitive"):
        assert forbidden not in rule, f"the read rule has drifted into governance: {forbidden!r}"
