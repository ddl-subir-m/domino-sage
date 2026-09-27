"""The 30s app poll refreshes this tab's row and does not install another tab's selection.

Build arms a 30-second `loadApps` poll so a teammate's push and a build running in another app
reach the screen without anyone clicking. The selected flag in that answer is the process
default. This tab already has an app — the one in `?app=` — so the poll refreshes that row's
name and leaves `bindings` and `appAttachments` where they are. A tab with no app, or whose app
is gone from the list, still adopts the selected row.

The tick that changes nothing costs one `GET /apps`. A tick whose selected flag names a
different app costs the same one read: refetching the lists would be the move this poll no
longer makes.

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
def test_a_poll_does_not_move_this_tabs_app_or_its_records():
    """Another tab's selection is a different answer from `/apps`. This tab keeps the app it is
    showing, and the lists that hang off it."""
    stayed, _ = _ticks()
    assert stayed["activeApp"] == "app_c"
    assert stayed["activeName"] == "Rate curve viewer"
    assert stayed["bindings"] == ["Qwen 2.5"]
    assert stayed["attachments"] == []


@needs_node
def test_a_poll_that_does_not_change_the_app_does_not_refetch_its_lists():
    """The lists are already this app's. Refetching them because another tab selected something
    else would be the move this poll no longer makes."""
    stayed, _ = _ticks()
    assert stayed["calls"] == ["GET /apps"]


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
def test_the_header_row_keeps_this_tabs_app_over_this_tabs_records():
    """A poll that names a different selected app must not put that app's name over these lists,
    or these lists under that name."""
    stayed, _ = _ticks()
    said = _said(stayed)
    assert "Rate curve viewer" in said
    assert "Qwen 2.5" in said
    assert "Desk dashboard" not in said
    assert "margins.csv" not in said


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
