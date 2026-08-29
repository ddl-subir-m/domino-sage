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
wants one guard; a second Remove in the header would be a second one.

WHAT IT MARKS (#93). A Binding the app's own source never calls carries the word beside its name.
That answer is DERIVED, so it arrives written rather than scanned: `_record_resource_usage` runs at
the end of a build turn and `/api/bindings` serves it as `used`, because `_scan_app_sources` walks
the whole app tree and this row redraws on every app switch. It stays advisory in both directions —
it gates nothing, and an app no turn has scanned is marked nothing.

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


# ---- the usage label (#93) ----------------------------------------------------------------------


def _flat(step: dict) -> str:
    """What the row says, in order, with the runs of whitespace the spans leave collapsed. Order is
    the claim here: a mark is only right if it is against the right name."""
    return re.sub(r"\s+", " ", _said(step))


@needs_node
def test_a_binding_the_apps_source_never_calls_is_marked():
    """The advisory answer, beside the record it qualifies (ADR-0010). `app_a` records three and
    its source calls two of them, which is the only fixture that can show the mark landing on the
    right name rather than merely appearing somewhere in the row."""
    said = _flat(_build(select="app_a"))
    assert "Market data EOD (not used)" in said
    assert "Claude Sonnet 4 (not used)" not in said
    assert "Churn risk (not used)" not in said


@needs_node
def test_only_the_exception_is_marked():
    """A word beside every name is a word nobody reads. The row already says the app ships these —
    what it did not say is which one nothing calls."""
    assert _flat(_build(select="app_a")).count("(not used)") == 1


@needs_node
def test_an_app_no_build_turn_has_scanned_carries_no_mark():
    """`app_c`'s Binding comes back with no `used` at all, which is the shape the backend serves for
    an app no turn has left an answer for. "Nobody has looked" is not "nothing uses it", and
    labelling it unused would be a wrong answer where no answer is a true one."""
    said = _flat(_build(select="app_c"))
    assert "Qwen 2.5" in said
    assert "not used" not in said.lower()


@needs_node
def test_the_tooltip_says_what_the_mark_means_and_that_it_blocks_nothing():
    """Two words beside a name cannot say what looked or when, and a creator who reads "not used"
    as "this will not publish" has been told the opposite of ADR-0010. The strip also truncates, so
    the tooltip is where a narrow preview's reader finds which name the mark was on."""
    title = next(t for t in _build(select="app_a")["titles"] if "Market data EOD" in t)
    assert "Market data EOD (not used)" in title
    assert "last build" in title
    assert "publishes either way" in title
    # The pointer stays last in both kinds' tooltips — it is the only half the reader can act on.
    assert title.endswith("remove it there")


def test_the_mark_is_a_word_and_never_a_control():
    """The label is advisory and gates nothing — no publish, no bind, no unbind (ADR-0010). A
    control here would be a fourth thing acting on the answer, and `test_nothing_in_the_row_performs
    _a_removal` above says why the row holds none at all."""
    body = _app_scope_source()
    assert "Button" not in body and "danger" not in body
    # `used === false`, never a truthy test: `undefined` is the unscanned app, and `!b.used` would
    # fold it in with the scanned ones and mark every Binding of an app built before the scan.
    assert re.search(r"\.used\s*===\s*false", body)


@needs_node
def test_the_row_costs_no_network_call_on_render():
    """It reports a written record, so it answers out of the store. Anything that renders per app
    switch has to — see ADR-0010 on `publish_check`'s discipline."""
    for select in ("app_a", "app_b", "app_c", "app_d"):
        assert _build(select=select)["renderCalls"] == []
