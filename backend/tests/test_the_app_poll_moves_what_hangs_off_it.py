"""The 30s app poll moves the selected app AND what hangs off it (#95).

Build arms a 30-second `loadApps` poll so a teammate's push, a build running in another app,
and a selection made in a second tab all reach the screen without anyone clicking. It moved
`activeApp` and nothing else, so both app-scoped lists — `bindings` and `appAttachments` —
went on describing the app that was selected before. Two surfaces then paired one
app's name with another app's resources: the header's scope row (#92), which heads its lists
with the app name outright, and the resource panel's "In app" grouping.

WHAT THE FIX COSTS. The refresh is guarded on the app id actually changing. Unguarded it would
turn a no-op tick into three requests every 30 seconds, forever, in every open Build tab — worse
than the bug it fixes. The guard is the criterion, not a detail of it, so the tick that changes
nothing is asserted as hard as the tick that changes everything.

WHAT IT DOES NOT DO. It does not blank the lists on an app change. That is cheaper and never
shows a wrong pairing, but it flickers the row to an empty state whose copy says "nothing yet",
which for an app that ships two Bindings is a lie. The tests move BETWEEN two apps that both
carry records, so an implementation that clears instead of fetching fails them.

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


def _ticks() -> tuple[dict, dict]:
    """Two poll ticks in one run: one where the server moved the selection, one where it did not.

    Both directions carry records. `app_c` ships one Binding and no files, `app_a` ships two
    Bindings and a file, so every list has to change value rather than merely appear or clear.
    """
    return tuple(
        _run(
            [
                {"thread": "thr_many", "select": "app_c", "poll": "app_a"},
                {"thread": "thr_many", "select": "app_a", "poll": "app_a"},
            ]
        )
    )


def _body_of(rel: str, opener: str) -> str:
    """One function's source, so a claim about what a component reads is about that component."""
    src = (_WORKBENCH / "js" / rel).read_text()
    i = src.index("{", src.index(")", src.index(opener)))
    depth = 0
    for end in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[end], 0)
        if depth == 0:
            return src[i : end + 1]
    raise AssertionError(f"{opener} is not closed")


def _said(step: dict) -> str:
    """Every word the app's own list said, and nothing else on screen.

    That list was a row above the preview when this was written and is the App dependencies modal
    now (`624ff9b`, ADR-0035). What it is FOR is unchanged and is what this file asks about: it is
    the surface that pairs an app's name with an app's records, so a poll that moved one without
    the other prints the mismatch here."""
    deps = step["appDeps"] or {"title": "", "said": []}
    return " ".join([deps["title"] or ""] + deps["said"])


# ---- the tick that moves the app ---------------------------------------------------------


@needs_node
def test_a_poll_that_moves_the_app_moves_its_bindings_and_attachments():
    """The whole of #95. Both lists are read per app and both were left behind."""
    moved, _ = _ticks()
    assert moved["activeApp"] == "app_a"
    assert moved["activeName"] == "Desk dashboard"
    # app_a's records, not app_c's. `qwen-2-5` was the Binding a moment ago.
    assert moved["bindings"] == ["Claude Sonnet 4", "Market data EOD", "Churn risk"]
    assert moved["attachments"] == ["margins.csv", "legacy.csv"]


@needs_node
def test_the_move_refetches_rather_than_blanking_the_lists():
    """The empty state says "nothing yet", so clearing would make the row lie. Each list is
    filled from a read taken after the app changed, so each read has to show up as a request."""
    moved, _ = _ticks()
    assert "GET /bindings" in moved["calls"]
    assert "GET /project" in moved["calls"]


# ---- the tick that moves nothing ---------------------------------------------------------


@needs_node
def test_a_poll_that_changes_nothing_costs_the_one_read_it_always_cost():
    """The guard is the point. This tick runs every 30 seconds in every open Build tab for as
    long as the tab is open, and it answers the same app it answered last time."""
    _, still = _ticks()
    assert still["calls"] == ["GET /apps"]


@needs_node
def test_a_poll_that_changes_nothing_leaves_the_lists_where_they_were():
    """Not refetching must not mean losing them: the row renders off this state on every tick."""
    _, still = _ticks()
    assert still["activeApp"] == "app_a"
    assert still["bindings"] == ["Claude Sonnet 4", "Market data EOD", "Churn risk"]
    assert still["attachments"] == ["margins.csv", "legacy.csv"]


@needs_node
def test_a_failed_read_of_the_app_list_is_not_an_app_that_moved():
    """`apps()` answers empty for a 500 as readily as for a Project with no apps, so an id guard
    that trusts it treats every blip as an app change: three requests instead of one, and twice
    over once the next tick recovers."""
    step = _run(
        [{"thread": "thr_many", "select": "app_a", "poll": "app_c", "readFails": True}]
    )[-1]
    assert step["calls"] == ["GET /apps"]
    assert step["bindings"] == ["Claude Sonnet 4", "Market data EOD", "Churn risk"]
    assert step["attachments"] == ["margins.csv", "legacy.csv"]


# ---- the paths that already refreshed --------------------------------------------------


@needs_node
def test_switching_apps_by_hand_still_reads_each_record_once():
    """`selectApp` reloads the whole of Build, which refreshes both already. The cascade is
    off down that path, so this fix costs a hand-made app switch nothing — it fills the gap the
    poll had, and adds no second read to the moment that never had one."""
    step = _run([{"thread": "thr_many", "select": "app_c", "switchTo": "app_a"}])[-1]
    assert step["calls"].count("GET /bindings") == 1
    assert step["calls"].count("GET /project") == 1


# ---- the two surfaces that showed it -----------------------------------------------------


@needs_node
def test_the_header_row_names_the_new_app_over_the_new_apps_records():
    """#92's row heads its lists with the app name, so a stale list is a named wrong pairing
    rather than an ambiguous one. The name and the records have to arrive together."""
    moved, _ = _ticks()
    said = _said(moved)
    assert "Desk dashboard" in said
    assert "Market data EOD" in said
    assert "margins.csv" in said
    assert "Qwen 2.5" not in said


def test_the_resource_panel_reads_the_same_assignment_the_poll_writes():
    """The panel's "In app" grouping is the second surface. It is covered by the store fix only
    because it holds no app-scoped read of its own — it takes `activeApp` and `bindings` off one
    `store.get()`. A panel that fetched its own would need its own fix."""
    body = _body_of("components/resource-panel.js", "SW.ResourcePanel = function ResourcePanel(")
    read = body[: body.index("useState")]
    assert "SW.store.get()" in read
    for name in ("activeApp", "bindings"):
        assert re.search(rf"\b{name}\b", read), name
    assert not re.search(r"SW\.api\.(bindings|project)\b", body)
