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
    """Everything the app's own list says, title included.

    That list was a strip under the app identity row when this was written (#92). `624ff9b` moved
    it behind the header's own `…` and ADR-0035 gave it the panel's `In {app}` section too, so it
    is now the App dependencies modal and the whole of the app's scope. The title is read with the
    words because that is where the app's NAME went."""
    deps = step["appDeps"] or {"title": "", "said": []}
    return " ".join([deps["title"] or ""] + deps["said"])


def _app_scope_source() -> str:
    """The body of `AppDependenciesModal`, so a claim about what the list does is about the list."""
    src = (_WORKBENCH / "js" / "modes" / "builder.js").read_text()
    start = src.index("function AppDependenciesModal(")
    i = src.index("{", src.index(")", start))
    depth = 0
    for end in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[end], 0)
        if depth == 0:
            return src[i : end + 1]
    raise AssertionError("AppDependenciesModal is not closed")


# ---- what the row lists ----------------------------------------------------------------------


@needs_node
def test_the_row_names_the_selected_apps_bindings_and_its_attachments():
    """Two names, because they are two records with two consumers (ADR-0010) — no umbrella
    term invented here to cover both (#85 Q3). Named by what the app needs and carries rather
    than by the record's own word, which is the only place either identifier reached a person
    (ADR-0025)."""
    said = _said(_build(select="app_a"))
    assert "Needs to run" in said
    assert "Market data EOD" in said and "Claude Sonnet 4" in said
    assert "Files it carries" in said
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
def test_a_kind_with_nothing_in_it_is_named_and_says_so():
    """`app_c` has Bindings and no files, `app_d` files and no Bindings.

    THIS CLAIM INVERTED, and the reversal is the point. #92 refused to name an empty kind because
    the row was a GLANCE: naming one says the app ships something it does not. ADR-0011 wanted the
    opposite of the surface somebody arrives at intending to act, where "Files it carries — none"
    answers *is my file in this app?* directly. The two surfaces merged in `624ff9b`, and ADR-0035
    kept the destination's rule, because that is what this list now is."""
    c, d = _run([{"build": "thr_many", "select": "app_c"}, {"build": "thr_many", "select": "app_d"}])
    assert "Needs to run" in _said(c) and "Files it carries — none" in _said(c)
    assert "Files it carries" in _said(d) and "Needs to run — none" in _said(d)
    # The empty-app sentence is still for neither: one empty kind is not an empty app.
    assert "Nothing yet" not in _said(c) + _said(d)


@needs_node
def test_an_app_with_neither_still_shows_the_list_and_says_what_would_land_in_it():
    """Hiding it would make the header jump the moment the first Binding lands, and would teach
    a first-time creator nothing. What/why/what-to-do, in one sentence.

    The what-to-do names the handoff, because the handoff is what fills both lists —
    `_promote_chat_file` writes the Attachment and `_bind_from_handoff` the Bindings. The
    composer's own upload does NOT: it writes a scratch file and a Conversation chip, so a
    sentence naming it would leave a first-timer doing as they were told and seeing no change."""
    step = _build(select="app_b")
    assert step["appDeps"] is not None
    said = _said(step)
    assert "P&L report" in said  # whose list it is
    assert "Nothing yet" in said  # why it is empty
    assert "Chat" in said and "Open Builder" in said  # what actually fills it


# ---- what the row does not do -----------------------------------------------------------------


@needs_node
def test_every_row_carries_the_act_rather_than_a_pointer_to_it():
    """Read-only was only half an answer: a strip that reported a Binding and said nothing about
    where it is dealt with is the dead end the empty state was written to avoid. It answered with a
    POINTER — each kind's tooltip named `Project resources` — because the acts lived elsewhere.

    They live here now (ADR-0035), so the row carries them instead. A pointer is a promise the
    destination can act; the shortest way to keep that promise is to be the destination."""
    step = _build(select="app_a")
    menus = [m for m in step["menus"] if any(i["key"] == "remove" for i in m["items"])]
    # One per record the app holds: three Bindings and two files in this fixture.
    assert len(menus) == 5, menus
    assert all(any(i["label"].startswith("Remove from ") for i in m["items"]) for m in menus)
    # And nothing on screen still sends the reader somewhere else for it.
    assert not [t for t in step["titles"] if "Project resources" in t]


def test_the_removal_lives_here_and_exactly_once():
    """THIS CLAIM INVERTED. #92 kept the strip read-only on the argument that unbind and detach
    each report the app source that still uses what just went, so a second copy of either would be
    a second guard to keep in step with the first. That argument held while this was a summary
    BESIDE the panel's `In {app}` section. `624ff9b` removed the section and ADR-0035 moved the
    list itself here, so this is no longer a second copy — it is the only one, which is what
    ADR-0011 asks for. The `one act, one guard` rule is unchanged and now satisfied by there being
    one list rather than by this one declining to act."""
    body = _app_scope_source()
    called = set(re.findall(r"SW\.store\.(\w+)", body))
    assert {"removeBindingFromApp", "removeAttachmentFromApp"} <= called
    # And it still reads the record rather than scanning for it.
    assert "get" in called


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
    # The Scope sits between the name and the mark, because both qualify the record and the Scope is
    # the one a person can act on (#142). The mark still lands on this name and no other.
    assert "Market data EOD not scoped yet (not used)" in said
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
    as "this will not publish" has been told the opposite of ADR-0010.

    The sentence used to hang off the KIND, because the strip compressed a whole kind onto one line
    and truncated it — so the tooltip was also where a narrow reader found which name the mark was
    on. Neither is true of a list with a row per record: the mark sits on the row it qualifies, so
    the tooltip has only the one job left."""
    title = next(t for t in _build(select="app_a")["titles"] if "not used" in t)
    assert "last build" in title
    assert "publishes either way" in title
    # No pointer any more: the act is on this row's own menu, which is the half the reader can act
    # on and no longer somewhere else.
    assert "Project resources" not in title


def test_the_mark_is_a_word_and_never_a_control():
    """The label is advisory and gates nothing — no publish, no bind, no unbind (ADR-0010). A
    control here would be a fourth thing acting on the answer.

    Asked of the MARK rather than of the whole list, which is the narrowing ADR-0035 forces: the
    list does hold controls now, because the acts moved here. What must not become one is the
    answer they are drawn beside."""
    body = _app_scope_source()
    mark = body[body.index("mark &&"):body.index("sw-appdeps-unused") + 200]
    assert "Button" not in mark and "danger" not in mark
    assert "onClick" not in mark
    # `used === false`, never a truthy test: `undefined` is the unscanned app, and `!b.used` would
    # fold it in with the scanned ones and mark every Binding of an app built before the scan.
    assert re.search(r"\.used\s*===\s*false", body)


@needs_node
def test_the_row_costs_no_network_call_on_render():
    """It reports a written record, so it answers out of the store. Anything that renders per app
    switch has to — see ADR-0010 on `publish_check`'s discipline."""
    for select in ("app_a", "app_b", "app_c", "app_d"):
        assert _build(select=select)["renderCalls"] == []
