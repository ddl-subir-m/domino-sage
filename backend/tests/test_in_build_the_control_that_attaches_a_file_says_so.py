"""ADR-0048 — in Build, a mention is an Attachment, so the control says so.

Picking a Dataset file out of the rail said **Use here**, glossary term `Use in this
conversation`. In Chat that sentence is true: the click writes a Session context chip. In Build
the same click copies the bytes into the selected app, commits a manifest entry that rehydrates
the file on publish, and arms the sensitivity lock — and said none of it.

Every door into that fork now performs out loud. The file leaf gains the mode gate the folder row
already had; the folder tooltip shortens so the two grains read as one act rather than drifting
into two phrasings (ADR-0030); and the `@`-menu, which has no label to rename, carries its weight
as a receipt on the composer chip instead.

Rendered, not grepped: which words a row offers is a branch the component takes off the route and
the selected app, and a source read cannot tell a taken branch from a written one.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

_JS = Path(__file__).resolve().parent / "js"
APP = "Desk margins"

# One file under a folder and two at the root. Folders are collapsed until they are opened, so
# the leaves a paint draws are the root's — which is all these claims need, and the folder row is
# here for the grain-pairing test below.
_FILES = [
    {"path": "raw/2024/a.csv", "size": 1024},
    {"path": "top.csv", "size": 100},
    {"path": "margins.csv", "size": 200},
]


def _run(harness: str, payload) -> dict | list:
    out = subprocess.run(["node", str(_JS / harness)], input=json.dumps(payload),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _tree(**step) -> dict:
    return _run("dataset_folder_act_harness.mjs", step)


def _composer(steps: list[dict]) -> list[dict]:
    return _run("composer_context_harness.mjs", steps)


# ---- the file leaf ------------------------------------------------------------------------------


@needs_node
def test_in_build_the_file_leaf_says_it_attaches():
    """Four things happen on this click and the third of them commits a record. The ink is the
    short half, the way the folder row beside it already reads."""
    leaves = _tree(files=_FILES, app=APP)["leaves"]

    assert [leaf["name"] for leaf in leaves] == ["top.csv", "margins.csv"]
    assert {leaf["act"] for leaf in leaves} == {"Attach"}
    assert {leaf["tooltip"] for leaf in leaves} == {f"Attach file to {APP}"}
    assert {leaf["ariaLabel"] for leaf in leaves} == {f"Attach file to {APP}"}


@needs_node
def test_in_chat_the_file_leaf_still_says_use_here():
    """The same call, a different contract. Chat's click writes a Session context chip and nothing
    reaches a Built App, so the older pair of words is the true one there."""
    leaves = _tree(files=_FILES, app=APP, mode="chat")["leaves"]

    assert {leaf["act"] for leaf in leaves} == {"Use here"}
    assert {leaf["tooltip"] for leaf in leaves} == {"Use in this conversation"}
    assert {leaf["ariaLabel"] for leaf in leaves} == {"Use in this conversation"}


@needs_node
def test_the_glossary_term_stays_off_the_ink_in_both_modes():
    """A 320px dock spends its width on the file's name. Twenty-odd characters of glossary term ate
    it once already, and naming the app in the label would eat it again."""
    for mode in ("build", "chat"):
        for leaf in _tree(files=_FILES, app=APP, mode=mode)["leaves"]:
            assert leaf["act"] in ("Attach", "Use here")
            assert APP not in leaf["act"]
            assert leaf["act"] != leaf["tooltip"]


@needs_node
def test_the_leafs_hover_is_the_tooltip_the_folder_row_already_uses():
    """A native `title` and an antd `Tooltip` two rows apart read as two different affordances —
    one waits for the browser, one does not — for one act drawn at two grains."""
    for mode in ("build", "chat"):
        for leaf in _tree(files=_FILES, app=APP, mode=mode)["leaves"]:
            assert leaf["nativeTitle"] == ""
            assert leaf["tooltip"] != ""


@needs_node
def test_the_folder_and_the_file_name_one_act_at_two_grains():
    """`Attach this folder to X` beside `Attach file to X` is the drift ADR-0030 exists to stop."""
    out = _tree(files=_FILES, app=APP)

    assert {row["title"] for row in out["rows"]} == {f"Attach to {APP}"}
    assert {leaf["tooltip"] for leaf in out["leaves"]} == {f"Attach file to {APP}"}


@needs_node
def test_a_file_the_app_already_carries_is_a_mention_and_says_so():
    """`add_thread_context` guards the fork on `not row.get("path")` and the rail sets that path
    from the listing's `attached` flag, so this click copies nothing and commits nothing. It still
    puts the file in front of the assistant, which is exactly what `Use here` claims."""
    carried = [dict(f, attached=True, dest=f"public/data/revenue/{f['path']}") for f in _FILES]

    leaves = _tree(files=carried, app=APP)["leaves"]

    assert {leaf["act"] for leaf in leaves} == {"Use here"}
    assert {leaf["tooltip"] for leaf in leaves} == {"Use in this conversation"}


@needs_node
def test_with_no_app_selected_the_leaf_promises_nothing_it_cannot_do():
    """The fork is `mode === 'build' && an app is selected` — the client's own predicate for
    whether `attach_file` runs. With none selected the click falls back to the chat fetch, so the
    label that names an app would name nothing and promise a copy nobody receives."""
    leaves = _tree(files=_FILES, app=None)["leaves"]

    assert {leaf["act"] for leaf in leaves} == {"Use here"}
    assert {leaf["tooltip"] for leaf in leaves} == {"Use in this conversation"}


# ---- the table leaf one branch over --------------------------------------------------------------


@needs_node
def test_the_table_leaf_is_untouched_because_it_never_reaches_the_fork():
    """`kind: "table"` — no bytes, no manifest entry, no lock. It shares `LeafRow` with the Dataset
    file and must not share its words."""
    step = _run("data_source_scope_harness.mjs", [{"walk": ["DWH", "MARTS"]}])[-1]

    assert step["leaves"] == ["DIM_ACCOUNT", "FCT_USAGE_DAILY"]
    assert "Use here" in step["words"]
    assert "Attach" not in step["words"]


# ---- the receipt, where there is no label --------------------------------------------------------


_DATASET_FILE = {
    "id": "dsfile:ds_1:raw/2024/a.csv",
    "name": "a.csv",
    "kind": "file",
    "datasetId": "ds_1",
    "datasetRelPath": "raw/2024/a.csv",
    "datasetName": "Revenue",
    "parentId": "dataset:ds_1",
}


@needs_node
def test_a_dataset_file_mentioned_in_build_marks_its_chip_with_the_app():
    """The `@`-menu has no label to rename, so the chip carries the act's weight. Not a toast: a
    toast is gone in eight seconds and the Attachment outlives the Conversation."""
    report = _composer([{"drop": {"on": "build", "resource": _DATASET_FILE}}])[-1]

    assert report["build"] == ["sales.csv", "a.csv"]
    assert report["buildMarks"] == ["", "In Alpha"]


@needs_node
def test_the_same_mention_in_chat_gains_no_mark():
    """There it writes a Session context chip and no Attachment, so a mark naming an app would
    claim a copy that was never made. Asserted in BOTH composers: one Conversation has one context
    list, so a mark derived from the mode rather than from the act would appear on this chip the
    moment somebody opened Build."""
    report = _composer([{"drop": {"on": "chat", "resource": _DATASET_FILE}}])[-1]

    assert report["chat"] == ["sales.csv", "a.csv"]
    assert report["chatMarks"] == ["", ""]
    assert report["buildMarks"] == ["", ""]


@needs_node
def test_the_mark_names_the_app_the_bytes_went_to_not_the_one_selected_now():
    """One Conversation, two Built Apps. The bytes are in the tree they were copied into, and the
    manifest entry that rehydrates them on publish is that app's — so a mark re-derived from the
    current selection renames a file that never moved."""
    report = _composer([
        {"drop": {"on": "build", "resource": _DATASET_FILE}},
        {"selectApp": "app_beta"},
        {"drop": {"on": "build", "resource": dict(_DATASET_FILE, id="dsfile:ds_1:top.csv",
                                                  name="top.csv", datasetRelPath="top.csv")}},
    ])[-1]

    assert report["build"] == ["sales.csv", "a.csv", "top.csv"]
    assert report["buildMarks"] == ["", "In Alpha", "In Beta"]


@needs_node
def test_an_uploaded_file_gains_no_mark():
    """The fork takes a Dataset file and nothing else, so an Upload is Session context however
    Build it was added in."""
    report = _composer([{"drop": {"on": "build", "resource": {
        "id": "file:notes.csv", "name": "notes.csv", "kind": "file", "path": "notes.csv"}}}])[-1]

    assert report["build"] == ["sales.csv", "notes.csv"]
    assert report["buildMarks"] == ["", ""]
