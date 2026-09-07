"""An Upload's row must not name a Built App that Chat does not draw (#147 follow-up).

The subtitle answers "where does this file reach", and it named an app in both modes. Chat draws no
app rail, so the name there points at a row nobody can see — and it always pointed at the SAME one,
because a Project holds a Built App from birth: `activeApp` is never absent, `_app_display_name`
falls back to `Unnamed Built App`, and the second branch of that ternary ("not in any app yet") was
therefore dead. Somebody who had built nothing at all, and had just dragged two files in from their
own machine, was told both were "Chat-only — not in Unnamed Built App".

Build keeps the name. There the rail draws a row wearing that exact placeholder label, so the
sentence is a destination rather than a phantom, and dropping the name from both modes would have
cost the one reading that works.

Only running it shows either: the branch is a render-time ternary over the router's mode, and the
name it reaches for is settled by `/apps`. The harness mounts the real panel, once per mode, over
one Upload and one never-built app.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "scratch_scope_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

# What the rail calls an app nobody has built or renamed — the state this was reported in.
PLACEHOLDER = "Unnamed Built App"


def _panel(mode: str) -> dict:
    """The Upload's row as the panel draws it in one mode."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"mode": mode}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout.strip().splitlines()[-1])
    rows = {row["name"]: row["subtitle"] for row in report["rows"]}
    assert "support_tickets.csv" in rows, f"the Upload has no row in {mode}: {rows}"
    return rows


def test_chat_names_no_app():
    """The bug. Chat has no app on screen, so the subtitle has no subject to name."""
    said = _panel("chat")["support_tickets.csv"]

    assert PLACEHOLDER not in said, f"Chat named an app nobody has built: {said!r}"
    assert said == "Only in this chat"


def test_build_still_names_the_app_it_is_missing_from():
    """The reading that already worked. Build draws the rail row this names, placeholder label and
    all, so the name is how a reader gets to the app that cannot see the file."""
    said = _panel("build")["support_tickets.csv"]

    assert said == f"Chat-only — not in {PLACEHOLDER}"
