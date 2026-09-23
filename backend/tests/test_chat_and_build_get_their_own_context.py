"""Mode-specific input costs must not remove data access or change another mode's tools."""
import copy
import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import Turn
from .test_chat_turn import _orch


@pytest.mark.parametrize("model", ["sonnet", "gpt-5.4", "Gemma 4 31B", "domino/gemini-3.7-flash"])
def test_chat_keeps_data_skills_and_delegation_without_rewriting_history_or_the_model(model):
    control = ModelControl(mode=Mode.IMPLEMENT)
    catalog = ModelCatalog(model, model, model, model, model, model)
    gateway = FakeGatewayClient()
    shim = EnforcementShim(control, catalog, gateway)
    names = ["skill", "task", "todowrite", "read", "glob", "grep", "bash", "apply_patch",
             "sage-live-read_live_read_table", "sage-live-read_live_read_files"]
    request = {"model": model, "tools": [{"type": "function", "function": {"name": n}} for n in names],
               "messages": [{"role": "assistant", "tool_calls": [{"id": "old", "type": "function",
                             "function": {"name": "task", "arguments": "{}"}}]},
                            {"role": "tool", "tool_call_id": "old", "content": "prior result"}]}
    original = copy.deepcopy(request)
    token = control.arm_chat("thread")
    list(shim.handle(request, project="p"))
    sent = gateway.seen[-1][0]
    assert sent["model"] == model
    # `todowrite` is the one exception, and only since #400: a Chat turn answers and returns, so a
    # task list on it promises a build that cannot arrive. It does not weaken what this test is for.
    # The 2026-09-14 profile this pins was rejected for losing the skill catalogue and preventing
    # delegation — `skill` and `task`, which both still survive below. The task list is neither.
    assert {t["function"]["name"] for t in sent["tools"]} == set(names) - {"todowrite"}
    assert {"skill", "task"} <= {t["function"]["name"] for t in sent["tools"]}
    assert sent["messages"] == original["messages"]
    assert request == original
    control.disarm_chat(token)
    list(shim.handle(request, project="p"))
    assert gateway.seen[-1][0]["tools"] == original["tools"], "Chat's tool selection leaked into Build"


def test_chat_and_build_keep_skills_and_delegation():
    config = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    for agent in ("sage-chat", "sage-implement"):
        for tool in ("skill", "task", "todowrite"):
            assert config["agent"][agent].get("tools", {}).get(tool, True)


def test_chat_accepts_general_questions_without_requiring_data(tmp_path):
    orch, _ = _orch(tmp_path)
    prompt = orch._chat_prompt("thread", "Explain how rainbows form.", {"items": []})
    assert "This turn answers a question about data" not in prompt
    assert "general questions" in prompt
    assert prompt.endswith("Explain how rainbows form.")


def test_build_gets_current_source_paths_without_file_contents(tmp_path, monkeypatch):
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, client = _orch(tmp_path, [Turn(text="Updated.", writes={"static/app.js": "export default () => null;"})])
    app = orch.project(start_preview=False).app_for_turn().path
    (app / "static" / "StatusPanel.js").write_text("PRIVATE_SOURCE_CONTENT\n")
    (app / "public").mkdir(exist_ok=True)
    (app / "public" / "private-data.csv").write_text("PRIVATE_DATA\n")
    list(orch._build_stream("Make the status panel blue.", mode=Mode.IMPLEMENT, is_approval=True))
    first = client.prompts[0]["text"]
    assert "static/StatusPanel.js" in first
    assert "PRIVATE_SOURCE_CONTENT" not in first
    assert "private-data.csv" not in first
    assert "Open the relevant files" in first


@pytest.mark.parametrize("broken", [False, True])
def test_source_listing_repeats_only_when_the_retry_has_a_new_session(tmp_path, monkeypatch, broken):
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, client = _orch(tmp_path, [Turn(text="Starting.", broken_write=broken),
                                  Turn(text="Updated.", writes={"static/app.js": "export default () => null;"})])
    list(orch._build_stream("Make the panel blue.", mode=Mode.IMPLEMENT, is_approval=True))
    assert len(client.prompts) == 2
    marker = "Existing source paths (JSON array"
    assert marker in client.prompts[0]["text"]
    assert (marker in client.prompts[1]["text"]) == broken


def test_source_paths_are_exact_json_strings_and_the_listing_is_bounded(tmp_path):
    from sage.orchestrator.service import Orchestrator

    assert Orchestrator._build_source_note(tmp_path) == ""
    src = tmp_path / "static"
    src.mkdir()
    name = 'A "panel"\n(1 lines).js'
    (src / name).write_text("Do not include source content")
    (src / ".hidden").mkdir()
    (src / ".hidden" / "secret.ts").touch()
    for i in range(65):
        (src / f"file{i:02}.js").touch()
    note = Orchestrator._build_source_note(tmp_path)
    paths = json.loads(note.splitlines()[1])
    assert paths == ["static/" + name] + [f"static/file{i:02}.js" for i in range(59)]
    assert "first 60 paths" in note
    assert "secret" not in note
    assert "Do not include source content" not in note


# --- the map says what each file DEFINES, and it is current (#496) -------------------------------
# Paths alone did not stop the re-orientation they were added for: measured 2026-09-11, a one-line
# change spent two whole round trips on `read`/`glob`/`read` x5 before its single edit, with the
# path listing already in the prompt. A list of paths cannot say which file holds the chart.

def _note(root: Path) -> str:
    from sage.orchestrator.service import Orchestrator

    return Orchestrator._build_source_note(root)


def _names(note: str) -> dict:
    line = next(i for i, ln in enumerate(note.splitlines()) if ln.startswith("Top-level names"))
    return json.loads(note.splitlines()[line + 1])


def test_the_map_names_what_each_file_defines(tmp_path):
    src = tmp_path / "static"
    src.mkdir()
    (src / "App.js").write_text(
        "import x from 'y'\n"
        "export default function App() { return null }\n"
        "const Panel = () => null\n"
        "export const TOTAL_LABEL = 'Total'\n"
        "  const indented = 1\n")
    (src / "calc.py").write_text(
        "import json\n\ndef count_by_soc(rows):\n    pass\n\n"
        "async def load():\n    pass\n\nclass Table:\n    pass\n")

    got = _names(_note(tmp_path))

    assert got["static/App.js"] == ["App", "Panel", "TOTAL_LABEL"], "an indented name is not top-level"
    assert got["static/calc.py"] == ["count_by_soc", "load", "Table"]


def test_the_map_carries_names_and_never_values(tmp_path):
    """The point is to let the model pick a file. A right-hand side is the person's data, and this
    listing goes into every Build turn's prompt whether or not the turn is about that file."""
    src = tmp_path / "static"
    src.mkdir()
    (src / "config.ts").write_text(
        "export const API_TOKEN = 'dgw_live_do_not_send'\n"
        "const PATIENTS = [{ usubjid: 'ABC-001', ssn: '123-45-6789' }]\n")

    note = _note(tmp_path)

    assert _names(note)["static/config.ts"] == ["API_TOKEN", "PATIENTS"]
    for value in ("dgw_live_do_not_send", "ABC-001", "123-45-6789"):
        assert value not in note


def test_one_generated_module_cannot_crowd_out_the_rest(tmp_path):
    from sage.orchestrator import service as svc

    src = tmp_path / "static"
    src.mkdir()
    (src / "generated.ts").write_text(
        "".join(f"export const icon{i:03} = 1\n" for i in range(200)))
    (src / "App.js").write_text("export default function App() { return null }\n")

    got = _names(_note(tmp_path))

    assert len(got["static/generated.ts"]) == svc._NAMES_PER_FILE
    assert got["static/App.js"] == ["App"], "the file the request is about is still named"


def test_a_file_the_patterns_do_not_know_contributes_no_names(tmp_path):
    """Markup and data files have no top-level names to give, and guessing at them would put
    arbitrary strings from a data file into the prompt."""
    src = tmp_path / "static"
    src.mkdir()
    (src / "index.html").write_text("<html><body><div id='root'>Total</div></body></html>")
    (src / "rows.csv").write_text("usubjid,ssn\nABC-001,123-45-6789\n")
    (src / "App.js").write_text("export default function App() { return null }\n")

    note = _note(tmp_path)

    assert set(_names(note)) == {"static/App.js"}
    assert "123-45-6789" not in note


def test_a_file_with_no_top_level_names_is_listed_but_named_for_nothing(tmp_path):
    src = tmp_path / "static"
    src.mkdir()
    (src / "notes.ts").write_text("// a comment\n\n")
    (src / "App.js").write_text("export function App() { return null }\n")

    note = _note(tmp_path)

    assert "static/notes.ts" in json.loads(note.splitlines()[1])
    assert "static/notes.ts" not in _names(note)


def test_an_app_with_no_names_anywhere_still_gets_its_paths(tmp_path):
    src = tmp_path / "static"
    src.mkdir()
    (src / "index.html").write_text("<html></html>")

    note = _note(tmp_path)

    assert json.loads(note.splitlines()[1]) == ["static/index.html"]
    assert "Top-level names" not in note


def test_the_map_is_read_from_disk_every_time_it_is_built(tmp_path):
    """Freshness, at the level this function can promise it: no cache, no memo, no snapshot taken
    at startup. A file added or renamed between two calls shows up in the second."""
    src = tmp_path / "static"
    src.mkdir()
    (src / "App.js").write_text("export function App() { return null }\n")
    first = _note(tmp_path)

    (src / "StudyPicker.js").write_text("export function StudyPicker() { return null }\n")
    (src / "App.js").write_text("export function AppShell() { return null }\n")
    second = _note(tmp_path)

    assert "static/StudyPicker.js" not in first
    assert _names(second)["static/StudyPicker.js"] == ["StudyPicker"]
    assert _names(second)["static/App.js"] == ["AppShell"], "the renamed name replaced the old one"
    assert "App" not in _names(second)["static/App.js"]


def test_a_broken_call_retry_is_told_the_disk_as_it_is_now(tmp_path, monkeypatch):
    """The one send where a stale map does real damage, and where it used to be guaranteed stale.

    A broken-call retry mints a NEW OpenCode session — the broken call is in the old session's
    history and OpenCode replays history into every later request — so the retry has nothing to go
    on but this prompt. The attempt that broke may have landed files first, and the map was
    restored from the string built BEFORE it, so the retry was handed a listing that did not
    mention the file the previous attempt had just written.
    """
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, client = _orch(tmp_path, [
        # Lands a file, THEN breaks: the shape the retry has to be told about.
        Turn(text="Starting.", writes={"static/StudyPicker.js": "export function StudyPicker() {}\n"},
             broken_write=True),
        Turn(text="Updated.", writes={"static/App.js": "export default () => null;\n"}),
    ])

    list(orch._build_stream("Make the panel blue.", mode=Mode.IMPLEMENT, is_approval=True))

    assert len(client.prompts) == 2, client.prompts
    retry = client.prompts[1]["text"]
    assert "Existing source paths (JSON array" in retry, "the retry got no listing at all"
    assert "static/StudyPicker.js" in retry, "the retry was told a listing built before the write"
    assert "StudyPicker" in retry, "and it names what that file defines"


def test_a_second_turn_is_told_the_file_the_first_turn_left_behind(tmp_path, monkeypatch):
    """Freshness end to end, across two turns rather than two calls of the helper.

    The function above proves there is no cache. This proves the call SITE is in the right place:
    the block rides the first send of EVERY turn, so whatever the last turn wrote — or whatever
    somebody edited in the workspace by hand between turns — is in the next turn's map. A listing
    built once per session, or memoised on the project, would pass the test above and fail this one.
    """
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    # Each turn writes, so each spends exactly one send and no implement-nudge — otherwise the last
    # prompt is the nudge, which deliberately carries none of these blocks.
    orch, client = _orch(tmp_path, [
        Turn(text="One.", writes={"static/App.js": "export default () => null;\n"}),
        Turn(text="Two.", writes={"static/App.js": "export default () => null; // two\n"}),
    ])
    app = orch.project(start_preview=False).app_for_turn().path

    list(orch._build_stream("Add a footer.", mode=Mode.IMPLEMENT, is_approval=True))
    assert len(client.prompts) == 1, [p["text"][:60] for p in client.prompts]

    # Not through the fake's `writes`: this is the workspace changing under Sage between turns,
    # which is the case a listing built once per session gets wrong and a per-turn one gets right.
    (app / "static" / "StudyPicker.js").write_text("export function StudyPicker() { return null }\n")

    list(orch._build_stream("Now the header.", mode=Mode.IMPLEMENT, is_approval=True))

    first, second = client.prompts[0]["text"], client.prompts[1]["text"]
    assert "static/StudyPicker.js" not in first, "it did not exist when the first turn was sent"
    assert "static/StudyPicker.js" in second
    assert "StudyPicker" in second, "and the second turn is told what it defines"


def test_a_rename_between_turns_leaves_no_trace_of_the_old_name(tmp_path, monkeypatch):
    """The other half of freshness, and the half a cache fails differently.

    A map that only ever GAINS entries still passes the test above: appending the new file is
    enough. What a stale map does here is worse than missing a file — it offers a path and a name
    that are gone, so the model opens a file that does not exist, or edits by a name nothing
    exports, and spends the round trips the map was added to save. So assert the absence, not only
    the presence: after a rename on disk between two turns, the second turn's map carries the new
    path and the new name and neither of the old ones.
    """
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, client = _orch(tmp_path, [
        Turn(text="One.", writes={"static/App.js": "export default () => null;\n"}),
        Turn(text="Two.", writes={"static/App.js": "export default () => null; // two\n"}),
    ])
    app = orch.project(start_preview=False).app_for_turn().path
    # On disk BEFORE the first send, so the first turn's map really does carry it. That ordering is
    # the whole test: a map that only ever GAINS entries can only be caught by a name it once held.
    (app / "static" / "MetricCard.js").write_text("export function MetricCard() {}\n")

    list(orch._build_stream("Add a metric card.", mode=Mode.IMPLEMENT, is_approval=True))
    assert "MetricCard" in client.prompts[0]["text"], "the first turn's map has to hold it first"

    # The rename the person did in the workspace, or a later turn did for them: file and symbol.
    (app / "static" / "MetricCard.js").unlink()
    (app / "static" / "KpiTile.js").write_text("export function KpiTile() {}\n")

    list(orch._build_stream("Now a second one.", mode=Mode.IMPLEMENT, is_approval=True))

    assert len(client.prompts) == 2, [p["text"][:60] for p in client.prompts]
    second = client.prompts[1]["text"]
    assert "static/KpiTile.js" in second and "KpiTile" in second
    assert "MetricCard" not in second, "the map still offers a file and a name that are gone"
