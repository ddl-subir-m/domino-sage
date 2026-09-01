"""`Use in {app}` binds an LLM Alias to the selected Built App (#127, ADR-0011).

WHAT WAS MISSING. `POST /api/bindings` has been live and uncalled since the vanilla `/builder` page
was deleted (`2e4a205`). The Workbench rail inherited the browse and remove halves of a Binding and
not the add half, so a Resource added to the Project AFTER a plan crossed from Chat could never
reach the app: `_bind_from_handoff` was the only writer there was. A person who added an Alias, saw
it in the rail and mentioned it in Build got a refusal pointing at a control that did not exist.

WHERE THE ACT LIVES. On the Project row's menu, directly above `Remove from {app}` — one act, one
place, in the section that owns the scope (ADR-0011). Build only: a Binding names exactly one app
(`CONTEXT.md`), and Chat shows no app to name. Language models only for now; the Model API door
(#128) and the Data Source door (#129) each carry a question this one does not.

THE ID SPACES, WHICH ARE THE WHOLE JOB — the same trap `test_requirement_dies_and_removal_names_its
_scope.py` names. A Project Resource id is PREFIXED (`llm_alias:al_1`); a Binding carries the BARE
id beside its kind (`al_1`). `POST /bindings` reads `id` and looks it up against the live listing,
so a prefixed id resolves to nothing, raises `LookupError`, answers 404 — and the rail, having
already asked for a refresh, redraws exactly as it was. The failure looks like success. That is why
the harness records the posted BODY and not just that the request happened.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_LLM_ALIAS, Binding
from sage.router.models import ModelCatalog

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _row(rows: list[dict], name: str, section: str) -> dict:
    """The one row under `section` whose name is `name`."""
    found = [r for r in rows if r["section"] == section and name in r["texts"]]
    assert len(found) == 1, f"{name} appears {len(found)} times under {section}: {rows}"
    return found[0]


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---- the request the act issues -------------------------------------------------------


@needs_node
def test_use_in_posts_the_bare_resource_id_not_the_prefixed_one():
    """`app_c` binds `al_2` only, so the Project's `llm_alias:al_1` is a row it does not hold — the
    one state the act exists for. The assertion is the body, because a 404 on a prefixed id leaves
    the panel looking precisely like a bind that worked."""
    step = _run([{"useIn": "Claude Sonnet 4", "thread": "thr_many", "select": "app_c"}])[-1]
    assert step["posted"] == [{"kind": "llm_alias", "id": "al_1"}]
    assert "POST /bindings" in step["calls"]


# ---- what the row says before anything is clicked -------------------------------------


@needs_node
def test_a_row_the_app_does_not_use_says_so():
    """The sign on the door. Absence was invisible before this: a bound row read "Required by {app}"
    and an unbound one read nothing at all, so a person who added an Alias to the Project saw it sit
    in the rail looking exactly as ready as one the app could actually call."""
    step = _run([{"panel": "thr_many", "select": "app_c"}])[-1]
    loose = _row(step["rows"], "Claude Sonnet 4", "In this project")
    assert "Not used by Rate curve viewer" in loose["texts"]


@needs_node
def test_a_row_the_app_already_uses_offers_no_second_way_to_add_it():
    """Passes on arrival — `canBind` excludes a required row — and is here because that exclusion is
    the whole of "one act, one place" (ADR-0011). Loosen the condition and the Project row grows a
    second door to a Binding the app already holds, beside a section that already says so."""
    step = _run([{"panel": "thr_many", "select": "app_a"}])[-1]
    bound = _row(step["rows"], "Claude Sonnet 4", "In this project")
    assert "Required by Desk dashboard" in bound["texts"]
    assert not any(i["label"] == "Use in Desk dashboard" for i in bound["items"])
    # And the act that DOES belong to a held Binding is still the app section's, untouched.
    in_app = _row(step["rows"], "Claude Sonnet 4", "In Desk dashboard")
    assert any(i["label"] == "Remove from Desk dashboard" for i in in_app["items"])
    assert not any(i["label"] == "Use in Desk dashboard" for i in in_app["items"])


@needs_node
def test_chat_offers_the_conversation_act_and_nothing_app_scoped():
    """The same row, one mode over. A Binding names exactly one app (`CONTEXT.md`) and Chat shows
    none, so both the act and the marker would be naming an app that is not on screen. What Chat
    keeps is the act it has always had, which the handoff turns into a Binding."""
    step = _run([{"panel": "thr_many", "select": "app_c", "mode": "chat"}])[-1]
    row = _row(step["rows"], "Claude Sonnet 4", "In this project")
    assert not any("Not used by" in t for t in row["texts"])
    assert not any(i["label"] == "Use in Rate curve viewer" for i in row["items"])
    assert any(i["label"] == "Use in this chat" for i in row["items"])


# ---- the refusal, which is the sentence that reported the bug -------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code", template=_template(tmp), gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", assets=None)


def test_the_refusal_names_the_app_and_the_act_and_stops_once_the_binding_exists(tmp_path: Path):
    """The reported bug, both halves. The old sentence sent people to a control that did not exist
    and said "connect", which the glossary bans outright; the new one names the app the Binding
    would belong to and the label that makes it. And once the Binding is there the turn stops
    refusing at all, which is the half that proves the door leads somewhere."""
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    proj.workspace.set_display_name("Gong sentiment")
    ref = {"kind": KIND_LLM_ALIAS, "id": "al_1", "name": "sonnet"}

    said, _ = orch._unusable_mentions(proj, None, None, [ref])
    assert "@sonnet" in said
    assert "Gong sentiment doesn't use it yet" in said
    assert "Use in Gong sentiment" in said
    assert "connect" not in said.lower()

    proj.workspace.update_bindings(
        lambda entries: [*entries, Binding(KIND_LLM_ALIAS, "al_1", "sonnet", "sonnet").to_dict()])
    assert orch._unusable_mentions(proj, None, None, [ref]) == ("", [])


def test_an_unnamed_app_is_called_what_the_rail_calls_it(tmp_path: Path):
    """`display_name` is "" until somebody renames an app, which is most apps most of the time. The
    sentence quotes a menu label back at the reader, so the two have to agree on the app's name or
    it sends them hunting for a row that says something else — which is how this bug worked the
    first time. `_app_display_name` is the rail's own answer, and this is why the message goes
    through it rather than reading the stored name directly."""
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    said, _ = orch._unusable_mentions(
        proj, None, None, [{"kind": KIND_LLM_ALIAS, "id": "a", "name": "s"}])
    assert "Unnamed Built App doesn't use it yet" in said
    assert "Use in Unnamed Built App" in said


def test_the_template_never_quotes_a_label_the_panel_cannot_draw():
    """The mistake this fix made twice. The panel draws `Use in {app name}`, so a string compiled
    into a Built App cannot quote the label it means — and the one label it CAN spell exactly,
    "Use in this chat", is the wrong scope sitting directly above the right one in the same menu.
    The old text promised "Use on", which named no control at all."""
    said = (Path(__file__).resolve().parents[2] / "template" / "react-vite" / "src"
            / "appLlm.ts").read_text()
    assert "choose Use on" not in said      # named no control at all
    assert "Use in this app" not in said    # a label the panel never draws
