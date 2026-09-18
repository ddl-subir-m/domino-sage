import json
from pathlib import Path

from sage.driver.opencode import with_attachment_listing

from .test_chat_turn import _orch


def test_sage_chat_prompt_is_the_agents_md_file():
    root = Path(__file__).resolve().parents[2]
    prompt = (root / "template" / "chat" / "AGENTS.md").read_text()
    cfg = json.loads((root / "opencode.json").read_text())
    assert cfg["agent"]["sage-chat"]["prompt"] == prompt
    assert cfg["agent"]["sage-chat"]["permission"]["edit"] == "allow"
    assert cfg["agent"]["sage-chat"]["permission"]["bash"] == "allow"


def test_sage_chat_prompt_teaches_visuals_without_being_asked():
    prompt = (Path(__file__).resolve().parents[2] / "template" / "chat" / "AGENTS.md").read_text()
    assert "They do not have to ask for a visual" in prompt
    assert "heatmap PNG" in prompt
    assert "ax.imshow" in prompt
    assert "greeting, thanks" in prompt


def test_sage_chat_prompt_names_the_pandas_shape_that_breaks_a_table():
    """Stating the `{title, columns, rows}` schema was not enough on its own: a turn asked what was
    in an uploaded forecast JSON reached for the pandas one-liner, wrote a bare record array, and
    the card showed "No data" beside a chart that had plotted the same rows. Naming the two idioms
    that produce that file, and the one that does not, is the part that was missing."""
    prompt = (Path(__file__).resolve().parents[2] / "template" / "chat" / "AGENTS.md").read_text()
    assert 'df.to_json(path)' in prompt
    assert 'orient="columns"' in prompt
    assert 'df.to_json(orient="records")' in prompt
    assert 'json.dump(df.to_dict("records"), f)' in prompt
    assert "df.values.tolist()" in prompt
    assert "positional array" in prompt


def test_chat_attachment_listing_tells_the_agent_to_read_the_file():
    out = with_attachment_listing(
        "what data is there in @desk.csv",
        [{"name": "desk.csv", "path": ".sage/scratch/desk.csv",
          "summary": "2 rows", "detail": ""}],
        chat=True,
    )
    assert "what data is there in @desk.csv" in out
    assert ".sage/scratch/desk.csv" in out
    assert "read the file at the path shown" in out
    assert "built app MUST" not in out


def test_build_attachment_listing_still_warns_not_to_copy_into_src():
    out = with_attachment_listing(
        "use this csv",
        [{"name": "desk.csv", "path": "public/data/x/uploads/desk.csv",
          "summary": "2 rows", "detail": ""}],
    )
    assert "built app MUST" in out


def test_the_prompt_never_promises_python_it_may_not_have():
    """Live read is served over MCP, and OpenCode does not reliably connect it — measured live on
    2e284e7: a workspace idle for seven minutes, then a first Chat turn handed NO Live read tools,
    and `opencode_connected` still reading "never" two model calls later. So the prompt must still
    say something about their absence; saying nothing left the same question answering one turn and
    refusing the next, decided by nothing.

    What it must NOT say is where this test moved. The prompt used to send that case to Python and
    then forbid the turn from naming the missing tool. On a bounded turn there is no Python —
    `READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS` takes the shell and the ability to write a file
    — so the send named a lane the turn does not have, and the ban closed the only true exit. The
    turn improvised a /tmp write-and-run it had just said it could not do, and died at 240s with no
    answer (#412, ADR-0058).
    """
    root = Path(__file__).resolve().parents[2]
    md = (root / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    lines = md.split("\n")

    def says(probe: str) -> None:
        """Assert the pack says `probe`, and that `probe` could ever have matched.

        The pack is wrapped by hand and `opencode.json` keeps each wrap as an escaped newline, so
        a probe straddling one matches nothing in either file. That failure is silent and reads
        exactly like a passing check on a file nobody edited — which is how #412 could have been
        reported done while undone. Requiring the probe to land inside a single line makes a
        re-wrap fail loudly here instead of quietly weakening the check.
        """
        assert "\n" not in probe, probe
        assert any(probe in line for line in lines), probe

    # The absence is still addressed, and the honest exit is now the instructed one. Refusing is
    # NOT the instructed default: the measured good outcome above is a turn that kept `bash` and
    # answered from Python, and the condition this bullet fires on (Live read absent) is
    # independent of the one that takes Python away (`read_only_turn`). So it has to say both.
    says("not in your list this turn, use what you do have — do not improvise")
    says("Whether Python is available to you this turn")
    says("is something you can see in your own tool list: if it is there, use it")

    # And the exit it opens is the pack's OWN sanctioned form, not a competing one: :23-25 already
    # says never to call yourself blocked or unable, and to name the missing THING and what you
    # would do once it is there. :21 still forbids naming a tool to the person. A bullet telling
    # the turn to "say what you cannot do" would contradict :23 and re-create #412 the other way
    # up — an instructed refusal in place of an instructed improvisation.
    says("say what you would need in order to")
    says("what you would do once it is there")
    assert "say what you cannot do" not in md

    # No route back to any of the four claims, in the copy the model is actually sent.
    # `test_sage_chat_prompt_is_the_agents_md_file` pins the two equal; asserting here as well
    # means a future split cannot land this bug on the live side alone. The THIRD copy — the
    # per-turn prose built in `_chat_prompt` — is not covered by this loop and has its own test
    # below; nothing else pins it.
    prompt = json.loads((root / "opencode.json").read_text(encoding="utf-8"))[
        "agent"]["sage-chat"]["prompt"]
    for gone in ("Use Python below when the answer needs the",
                 "query the data with Python instead",
                 "everything they do, the Python below also does",
                 "never name a tool to them as the reason"):
        assert gone not in md, gone
        assert gone not in prompt, gone


def test_the_turn_prompt_does_not_promise_python_either(tmp_path: Path):
    """The third copy, and the one with the loudest voice. `template/chat/AGENTS.md` and
    `opencode.json` are pinned equal above, but the same instruction was taught again per turn in
    `_chat_prompt`, and a turn prompt is what the model quotes back at the person (#398).

    It carried both defects in its own words: "query the data with Python instead — a missing tool
    is never a reason to tell someone you cannot see their data". Fixing only the pack would have
    left the live sentence intact and the fix cosmetic, and no test reached this string (#412).
    """
    orch, _ = _orch(tmp_path)
    prompt = orch._chat_prompt("thr_412", "which accounts score highest?", {"items": []})

    # The same four the pack is checked against, because "nothing pins it" was true of this copy
    # until now and a re-introduction here would be phrased in its own words, not the pack's.
    for gone in ("Use Python below when the answer needs the",
                 "query the data with Python instead",
                 "everything they do, the Python below also does",
                 "never name a tool to them as the reason",
                 "a missing tool is never a reason"):
        assert gone not in prompt, gone

    # Both halves, matching the pack: use what is there, and if the calculation has no lane, ask
    # in the sanctioned form rather than refusing. The turn prompt is the copy the model quotes
    # back (#398), so a refuse-only version of it outranks the pack's "use what you do have".
    assert "use what you do have" in prompt
    assert "say what you would need in order to answer it" in prompt
    # Never the tool itself: `AGENTS.md:21` forbids naming one to the person, and the turn prompt
    # carries no tool-naming ban of its own to fall back on.
    assert "say so plainly" not in prompt


def test_the_turn_writes_one_artifact_not_two():
    """A second file is a second write step, and a step is a whole round trip through the gateway.

    Measured on 2026-09-11: a Chat turn cost 7-8 model steps and 73-84% of its wall clock sat
    inside them. The prompt used to ask for a chart AND a table whenever the person might want
    both, and for a matrix it asked for both outright — so the rule that produced the extra step
    was in the prompt, not in the question.

    Both copies, because `template/chat/AGENTS.md` is the source of truth and `opencode.json` is
    what the model is actually sent. Editing one alone is the failure this asserts against.
    """
    root = Path(__file__).resolve().parents[2]
    md = (root / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    prompt = json.loads((root / "opencode.json").read_text(encoding="utf-8"))[
        "agent"]["sage-chat"]["prompt"]
    for probe in ("Write **one** of them, whichever fits the answer — not both",
                  "square matrix is a heatmap PNG"):
        assert probe in md, probe
        assert probe in prompt, probe
    # The rule it replaced, in either of the two shapes it had.
    assert "Write both when they would copy" not in md
    assert "square matrix is **both**" not in md


def test_the_turn_is_told_to_do_the_work_in_one_script():
    """Looking in one step and computing in the next doubles the round trips for no extra answer.

    The `.unique()` line is the one the prompt itself used to invite a separate look with, so it
    carries the same instruction rather than contradicting it."""
    root = Path(__file__).resolve().parents[2]
    md = (root / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    prompt = json.loads((root / "opencode.json").read_text(encoding="utf-8"))[
        "agent"]["sage-chat"]["prompt"]
    for probe in ("**Do the whole job in one script.**",
                  "inside the script you are already running, not in a"):
        assert probe in md, probe
        assert probe in prompt, probe


def test_the_turn_is_told_that_what_it_prints_it_pays_for_again():
    """Tool output is the one transcript cost Sage cannot cap from the outside.

    `opencode.json` has no tools block and the driver never sees a tool result on the way in, so a
    `bash` that prints a whole frame is stored in the session verbatim and re-sent on every step
    after it. Measured on 2026-09-11, a step's time to first byte tracked its payload — ~1.5s at
    14KB against ~4s at 321KB — so what one step prints is what every later step waits for. The
    prompt is the only lever there is, which is why this rule is pinned rather than left to taste.
    """
    root = Path(__file__).resolve().parents[2]
    md = (root / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    prompt = json.loads((root / "opencode.json").read_text(encoding="utf-8"))[
        "agent"]["sage-chat"]["prompt"]
    for probe in ("**Print little.**",
                  "re-read on every step that follows it",
                  "never the script itself"):
        assert probe in md, probe
        assert probe in prompt, probe


def test_the_turn_is_told_to_format_numeric_diagnostics_before_printing():
    """The consumer-book VLTA diagnosis printed raw pandas slices.

    The file had no person-shaped columns in the reported transcript, but raw financial floats can
    carry 10 or 11 digits after a decimal point. The gateway's PII rule reads that as a phone
    number, and then the useful answer is lost. The prompt cannot change the policy, so it teaches
    the chat agent to keep stdout small and fixed precision.
    """
    root = Path(__file__).resolve().parents[2]
    md = (root / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    prompt = json.loads((root / "opencode.json").read_text(encoding="utf-8"))[
        "agent"]["sage-chat"]["prompt"]
    for probe in ("Format numeric diagnostics before printing them.",
                  "10 or 11 digits in a row",
                  "to_string(float_format=...)",
                  "small summary dict with fixed precision"):
        assert probe in md, probe
        assert probe in prompt, probe
