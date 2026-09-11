"""A Dataset chip gives the agent a FOLDER. It never gave it the names in that folder.

The turn then tells the agent to read those files, not to grep (`public/data/` is gitignored and
every attachment is a symlink, so a search returns no matches even when the value IS there) and not
to search elsewhere for a substitute. Listing is the only door left. So it lists — and when the
folder is missing or empty, nothing it is allowed to do next can help, so it lists again. Observed
live: five identical `ls -R public/data/<slug>/uploads` calls in one turn, to the 600s ceiling.

Four states, one of which is always true, so the sentence is never silent.
"""
from pathlib import Path

from sage.orchestrator.service import (
    FOLDER_COLLAPSE_THRESHOLD,
    _chat_context_line,
    _context_folder_state,
)

_CHIP = {"kind": "dataset", "name": "sales_2026", "path": "public/data/sales_2026/uploads"}


def _folder(root: Path) -> Path:
    d = root / "public" / "data" / "sales_2026" / "uploads"
    d.mkdir(parents=True)
    return d


def test_it_names_the_files_it_found(tmp_path: Path):
    d = _folder(tmp_path)
    (d / "q3.csv").write_text("a\n1\n")
    (d / "q4.csv").write_text("b\n2\n")

    state = _context_folder_state(tmp_path, _CHIP)

    assert state == "Read these files: `q3.csv`, `q4.csv`."


def test_it_names_a_nested_file_by_the_subpath_that_opens_it(tmp_path: Path):
    """`attach_folder` keeps a Dataset's own tree, so a name alone would not open the file."""
    d = _folder(tmp_path)
    (d / "2026" / "01").mkdir(parents=True)
    (d / "2026" / "01" / "part.csv").write_text("a\n1\n")

    assert _context_folder_state(tmp_path, _CHIP) == "Read these files: `2026/01/part.csv`."


def test_it_stops_naming_at_the_collapse_threshold(tmp_path: Path):
    """Per-file lines are right for five files and ruinous for two hundred, on every turn, forever
    — the reason FOLDER_COLLAPSE_THRESHOLD exists. One number, one rule, both surfaces."""
    d = _folder(tmp_path)
    for i in range(FOLDER_COLLAPSE_THRESHOLD + 4):
        (d / f"part_{i:02d}.csv").write_text("a\n1\n")

    state = _context_folder_state(tmp_path, _CHIP)

    assert state.count("`") == FOLDER_COLLAPSE_THRESHOLD * 2
    assert "and 4 more in that folder — list it for the rest." in state


def test_an_empty_folder_says_so_instead_of_saying_nothing(tmp_path: Path):
    _folder(tmp_path)

    state = _context_folder_state(tmp_path, _CHIP)

    assert "holds no files" in state
    assert "Do not read anything else in its place." in state


def test_a_folder_that_is_not_there_says_so(tmp_path: Path):
    """The stale `public/data` link put the agent one app away from its own attachment. The file
    was on the mount and the rail drew it; the path in the prompt held nothing."""
    state = _context_folder_state(tmp_path, _CHIP)

    assert "cannot read that folder" in state
    assert "Do not read anything else in its place." in state


def test_files_that_all_refuse_to_open_are_not_reported_as_no_files(tmp_path: Path):
    """A rehydrate can leave the symlinks dangling. Calling that "no files" sends the person looking
    for an attachment they already made."""
    d = _folder(tmp_path)
    (d / "gone.csv").symlink_to(tmp_path / "mnt" / "data" / "gone.csv")

    state = _context_folder_state(tmp_path, _CHIP)

    assert "names 1 file(s) and not one of them opens" in state


def test_the_row_carries_the_state_and_asks_for_the_path_back_when_it_fails(tmp_path: Path):
    """The wiring, not the rendering. A ban with no exit is what turned a wrong path into a loop:
    the row now says what to do when the path it names does not open."""
    d = _folder(tmp_path)
    (d / "q3.csv").write_text("a\n1\n")

    line = _chat_context_line(_CHIP, folder_note=_context_folder_state(tmp_path, _CHIP))

    assert "files at public/data/sales_2026/uploads." in line
    assert "Read these files: `q3.csv`." in line
    assert "say so and name it, and stop" in line


def test_the_row_keeps_the_old_sentence_when_there_is_nowhere_to_look(tmp_path: Path):
    """No workspace means naming files would be a guess, and a guess here is the original bug."""
    assert "Read those files." in _chat_context_line(_CHIP)


def test_the_turn_scopes_its_no_listing_rule_to_where_it_writes(tmp_path: Path):
    """The rule was always about the Artifact OUTPUT folder — "that folder already exists ... write
    the file there" — but it was written as a flat "do not list directories", and the model has no
    way to see the scope. Read flat it forbids the one move a Dataset chip leaves open, so the turn
    was banned from listing and given nothing else that could work."""
    from sage.assets.provider import FakeAssetProvider
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=template, gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", assets=FakeAssetProvider(),
    )

    prompt = orch._chat_prompt("t1", "what is in my data?", {"items": []})

    assert "do not list directories to find where to write" in prompt
    assert "Write the file there; do not list directories. " not in prompt
