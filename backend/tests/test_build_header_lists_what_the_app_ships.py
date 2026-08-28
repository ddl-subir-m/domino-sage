"""The Build header row says what the selected app ships (#92).

#87 reserved the row and left it empty; #85 decided what lands in it. Two Built Apps under one
Conversation ship different things, and until this row was filled the only thing on screen naming
resources was the composer's chips — which are the CONVERSATION's (#84) and stand still when the
app changes. Somebody choosing between two apps saw the same chips under both and could not tell
what either one would publish.

WHAT THE ROW READS. The declared record, never a scan of the app's code: `.sage/bindings.json` and
the attachments manifest, both written per app, both already re-read when the header's app control
changes app (`selectApp` -> `loadBuild`). ADR-0010 says why the declaration is the authoritative
answer and the derived scan advisory — a row that diffed against code would report a Binding made
two minutes ago, before the agent wrote a query, as one the app does not use.

WHAT IT DOES NOT DO. It removes nothing. Both kinds are listed in Project resources, and one act
wants one guard; a second Remove in the header would be a second one. It carries no usage label
either — that is #93, and the scan behind it has a cost question of its own.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_WORKBENCH = Path(__file__).resolve().parents[1] / "sage" / "workbench"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


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


def _build(thread: str = "thr_many", select: str | None = None, **extra) -> dict:
    return _run([{"build": thread, "select": select, **extra}])[-1]


def _texts(step: dict, prefix: str) -> list[str]:
    """What the elements whose class starts with `prefix` said, and nothing else on screen."""
    return [t for p in step["parts"] if p["className"].startswith(prefix) for t in p["texts"]]


def _said(step: dict) -> str:
    return " ".join(_texts(step, "sw-app-scope"))


def _app_scope_source() -> str:
    """The body of `AppScopeRow`, so a claim about what the row does not do is about the row."""
    src = (_WORKBENCH / "js" / "modes" / "builder.js").read_text()
    start = src.index("function AppScopeRow(")
    i = src.index("{", src.index(")", start))
    depth = 0
    for end in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[end], 0)
        if depth == 0:
            return src[i : end + 1]
    raise AssertionError("AppScopeRow is not closed")


# ---- what the row lists ----------------------------------------------------------------------


@needs_node
def test_the_row_names_the_selected_apps_bindings_and_its_attachments():
    """Two names, because they are two records with two consumers (ADR-0010) — no umbrella
    term invented here to cover both (#85 Q3)."""
    said = _said(_build(select="app_a"))
    assert "Bindings" in said
    assert "Market data EOD" in said and "Claude Sonnet 4" in said
    assert "Attachments" in said
    assert "margins.csv" in said


@needs_node
def test_switching_the_headers_app_control_reloads_the_row():
    """The whole point of the row. `refreshBindings` and the project read already run from
    `selectApp`, so this asserts the plumbing reaches the row rather than adding any."""
    a, c = _run([{"build": "thr_many", "select": "app_a"}, {"build": "thr_many", "select": "app_c"}])
    assert "Market data EOD" in _said(a)
    assert "Qwen 2.5" in _said(c)
    assert "Market data EOD" not in _said(c)
    assert "margins.csv" not in _said(c)


@needs_node
def test_a_kind_with_nothing_in_it_is_not_the_same_as_an_app_with_nothing():
    """`app_c` has Bindings and no files, `app_d` files and no Bindings. Naming a kind that is
    empty says the app ships something it does not; the empty state is for neither."""
    c, d = _run([{"build": "thr_many", "select": "app_c"}, {"build": "thr_many", "select": "app_d"}])
    assert "Bindings" in _said(c) and "Attachments" not in _said(c)
    assert "Attachments" in _said(d) and "Bindings" not in _said(d)
    assert "nothing yet" not in _said(c) + _said(d)


@needs_node
def test_an_app_with_neither_still_shows_the_row_and_says_what_would_land_in_it():
    """Hiding it would make the header jump the moment the first Binding lands, and would teach
    a first-time creator nothing. What/why/what-to-do, in one sentence.

    The what-to-do names the handoff, because the handoff is what fills both lists —
    `_promote_chat_file` writes the Attachment and `_bind_from_handoff` the Bindings. The
    composer's own upload does NOT: it writes a scratch file and a Conversation chip, so a
    sentence naming it would leave a first-timer doing as they were told and seeing no change."""
    step = _build(select="app_b")
    assert "sw-app-scope" in step["classes"]
    said = _said(step)
    assert "P&L report" in said  # whose row it is
    assert "nothing yet" in said  # why it is empty
    assert "Chat" in said and "Open Builder" in said  # what actually fills it


# ---- what the row does not do -----------------------------------------------------------------


@needs_node
def test_each_line_points_at_where_that_kind_is_managed():
    """Read-only is only half an answer: a row that reports a Binding and says nothing about
    where it is dealt with is the dead end the empty state was written to avoid."""
    step = _build(select="app_a")
    binds = [t for t in step["titles"] if "Market data EOD" in t]
    files = [t for t in step["titles"] if "margins.csv" in t]
    assert binds and all("Project resources" in t for t in binds)
    assert files and all("Project resources" in t for t in files)


def test_nothing_in_the_row_performs_a_removal():
    """One act, one guard. Unbind and detach both report the app source that still uses what
    just went (`service.unbind`, `service.detach_file`), and a second copy of either here would
    be a second guard to keep in step with the first."""
    body = _app_scope_source()
    assert "onClick" not in body
    assert re.findall(r"SW\.store\.(\w+)", body) == ["get"]


def test_the_row_does_not_read_the_conversations_context():
    """`state.attachments` is the Conversation's — the list the composer's chips draw, which must
    not follow the selected app (#84). This row wants the app's own, which is a different list."""
    assert re.search(r"\battachments\b", _app_scope_source()) is None


def test_the_row_carries_no_usage_label():
    """#93, blocked on this. The scan that would answer it walks the whole app tree
    (`_scan_app_sources`), which is a cost question of its own — and ADR-0010 keeps that answer
    advisory, so it can never be what this row reports."""
    body = _app_scope_source()
    assert "Not used" not in body and "unused" not in body.lower()


@needs_node
def test_the_row_costs_no_network_call_on_render():
    """It reports a written record, so it answers out of the store. Anything that renders per app
    switch has to — see ADR-0010 on `publish_check`'s discipline."""
    for select in ("app_a", "app_b", "app_c", "app_d"):
        assert _build(select=select)["renderCalls"] == []
