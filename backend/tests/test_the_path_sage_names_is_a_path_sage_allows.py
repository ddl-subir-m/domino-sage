"""Every destination Sage NAMES to the Chat agent has to be one Sage ALLOWS (#415).

Until this landed they disagreed, in the one direction nobody notices: the prompts said `/tmp`
and `chat_path_allowed` had never permitted it. On the unbounded lane that was invisible, because
`bash` writes `/tmp` without consulting either gate — MEASURED against the shipped
opencode-ai 1.18.4 binary: `ShellTool.collect` fills the `dirs` set that `ShellTool.ask` gates on
ONLY when argv[0] is in `{cd, chdir, popd, pushd, push-location, set-location}` or
`{rm, cp, mv, mkdir, touch, chmod, chown, cat, …}`, and `python` is in neither. So
`python -c '…download_file("…", "/tmp/x")'` never reaches the permission at all, and the bash
tool's own description says as much: "External workdir values require external_directory
approval; best-effort command-argument path warnings are advisory only."

On the read-only lane there is no shell (`READ_ONLY_DENIED`), so the write tool is all the turn
has, and it refused `/tmp` from the day both existed. The turn had nowhere to put a file and
said so in the only words it had — "I can only read files, not execute them here" (ADR-0058).
#407's `external_directory: "deny"` did not cause that; it made the read half loud too.

These tests hold the two halves together from both ends, because the failure was never in one
file: the prompt and the allowlist are edited by different people for different reasons and
nothing but a test makes them argue.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.shim.chat_paths import chat_path_allowed

ROOT = Path(__file__).resolve().parents[2]
TID = "thr_01abc"

# A destination always has a trailing separator — `/tmp/x.csv`, never bare `/tmp`. Matching on
# the slash lets a prompt keep saying "not /tmp" (which the Chat prompt deliberately does) while
# still failing the moment anything names a path under it again.
TMP_DESTINATION = "/tmp/"


# ---- the allowlist ------------------------------------------------------------------------

def test_scratch_is_allowed_for_this_thread_and_nothing_else_under_it():
    assert chat_path_allowed(f".sage/scratch/{TID}/fetch.py", TID)
    assert chat_path_allowed(f".sage/scratch/{TID}/q3.csv", TID)
    assert chat_path_allowed(f"/mnt/code/.sage/scratch/{TID}/q3.csv", TID)

    # Another Thread's scratch, like another Thread's Artifacts, is not this turn's.
    assert not chat_path_allowed(f".sage/scratch/thr_other/q3.csv", TID)


def test_scratch_is_scoped_by_thread_because_uploads_already_live_beside_it():
    """The reason the prefix is `.sage/scratch/<threadId>/` and not `.sage/scratch/`.

    A flat allow reads as the simpler answer and is one level too wide: an uploaded attachment
    lands at `.sage/scratch/<name>.csv` (and `.sage/scratch/uploads/…`, `.sage/scratch/datasets/…`
    — see `test_attach_upload.py`). Those are the person's files, shown in the rail. Flat, a
    scratch write could overwrite one, and the person would see a file they uploaded quietly
    become something the model wrote.
    """
    assert not chat_path_allowed(".sage/scratch/my_data.csv", TID)
    assert not chat_path_allowed(".sage/scratch/uploads/sales.csv", TID)
    assert not chat_path_allowed(".sage/scratch/datasets/sales_2026/train.csv", TID)


def test_tmp_stays_refused_so_the_closed_door_is_not_reopened_while_fixing_the_prompt():
    """The prompt moved off `/tmp`; the allowlist must not move onto it.

    Allowing `/tmp` is the fix that suggests itself, and it hands the Chat agent Sage's own
    OpenCode server log — `tempfile.gettempdir() / "sage-opencode.log"`, which on a Domino
    container is `/tmp/sage-opencode.log`. See the companion test in
    `test_a_permission_that_cannot_be_asked.py`, which holds the same door shut in `opencode.json`.
    """
    assert not chat_path_allowed("/tmp/x.py", TID)
    assert not chat_path_allowed("/tmp/sage-opencode.log", TID)
    assert not chat_path_allowed(f"/tmp/{TID}/x.py", TID)


def test_the_thread_record_dir_is_still_not_the_scratch_dir():
    """Why scratch did not simply reuse `.sage/threads/<threadId>/`, which was already allowed.

    That directory is not gitignored — deliberately: it holds Chat history that is committed.
    Scratch written there lands in the person's repository, which is the outcome the prompt line
    naming `/tmp` existed to prevent in the first place. The difference is invisible from the
    shim, so it is written down here.
    """
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".sage/scratch/" in gitignore
    assert ".sage/threads/" not in gitignore


# ---- what the prompts actually say -----------------------------------------------------------

def _static_prompts() -> dict[str, str]:
    """Every prompt shipped as text, derived rather than listed.

    Naming the files is what let this defect sit in four places while a ticket described two.
    `opencode.json`'s agents are iterated, so an agent added later is swept without anyone
    remembering to add it here.
    """
    out = {"template/chat/AGENTS.md": (ROOT / "template" / "chat" / "AGENTS.md").read_text()}
    cfg = json.loads((ROOT / "opencode.json").read_text())
    for agent, block in cfg.get("agent", {}).items():
        prompt = block.get("prompt")
        if isinstance(prompt, str):
            out[f"opencode.json:agent.{agent}"] = prompt
    return out


@pytest.mark.parametrize("where", sorted(_static_prompts()))
def test_no_shipped_prompt_names_a_destination_under_tmp(where):
    assert TMP_DESTINATION not in _static_prompts()[where]


def test_the_chat_prompt_names_the_scratch_dir_it_is_allowed_to_write():
    prompt = (ROOT / "template" / "chat" / "AGENTS.md").read_text()
    assert ".sage/scratch/<threadId>/" in prompt
    # The Dataset route and the scratch-code line are two separate instructions and both used to
    # say `/tmp`. Fixing one and not the other is the shape of this defect, so both are pinned.
    assert '.download_file("<file>", ".sage/scratch/<threadId>/<file>")' in prompt
    assert "Scratch code you need in order to run" in prompt


def test_the_shipped_config_prompt_is_the_agents_md_file():
    """`opencode.json` is a hand-copied mirror. `test_sage_chat_prompt.py` already pins them by
    equality; this repeats it here so a run of THIS file alone still catches a half-applied fix."""
    cfg = json.loads((ROOT / "opencode.json").read_text())
    assert cfg["agent"]["sage-chat"]["prompt"] == (
        ROOT / "template" / "chat" / "AGENTS.md").read_text()


# ---- what the backend RENDERS ----------------------------------------------------------------

def test_the_rendered_dataset_lines_send_fetches_to_scratch():
    """The prompt lines the backend builds, rendered — not the source read for the branch.

    Two of them name a `download_file` destination, they are 25 lines apart in `service.py`, and
    the ticket for this defect named only one. Rendering both is what makes the pair checkable.
    """
    from sage.orchestrator.service import _chat_context_line

    unmounted = _chat_context_line(
        {"kind": "dataset", "name": "sales", "id": "ds-1", "project": "Demo"})
    in_dataset = _chat_context_line(
        {"kind": "file", "name": "q3.csv", "id": "ds-1", "datasetId": "ds-1",
         "datasetName": "sales", "datasetRelPath": "q3.csv"})

    for line in (unmounted, in_dataset):
        assert TMP_DESTINATION not in line
        assert ".sage/scratch/<threadId>/" in line
