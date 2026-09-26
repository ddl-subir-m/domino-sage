"""A new session carries a title where OpenCode READS it, so `ensureTitle` never runs (#549).

#496 put a `title` in the body of v2's `POST /api/session` and the call it meant to remove kept
running: v2's create payload is `{id?, agent?, model?, location?}` and drops everything else, so
every session still came back `New session - <ISO>` — the exact shape `isDefaultTitle` matches.
The unit test that covered #496 asserted the key Sage SENT, which is true and says nothing about
what OpenCode stored, so the defect was invisible from inside the suite. This asks the binary.

No model is called here and no gateway is stood up. Both assertions are about a session row: what
OpenCode stores when only the create body names a title, and what it stores after
`create_session` runs. The first is the reason the second needs a second request — if a later
OpenCode starts honouring the create body, that assertion goes red and the `PATCH` in
`create_session` can go with it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest

from sage.driver.opencode import OpenCodeClient

from .opencode_server import BINARY, _opencode_server

REPO = Path(__file__).resolve().parents[2]

# OpenCode 1.18.4's own `isDefaultTitle`, read out of the shipped bundle. It is the whole test
# `SessionPrompt.ensureTitle` applies before spending a Build-model call on a thread name.
DEFAULT_TITLE = re.compile(
    r"^(New session - |Child session - )\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _isolated(tmp_path: Path) -> tuple[Path, dict]:
    """A runtime that shares no config, data or state with the machine's own OpenCode."""
    config = json.loads((REPO / "opencode.json").read_text())
    config["plugin"] = []
    config["mcp"] = {}
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "opencode.json").write_text(json.dumps(config))
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(runtime / "opencode.json"),
               OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"),
               XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"),
               XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))
    return runtime, env


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_created_session_is_titled_where_opencode_will_read_it(tmp_path):
    runtime, env = _isolated(tmp_path)
    with _opencode_server(runtime, env) as url:
        directory = str(runtime)

        # What #496 shipped, sent straight at the route: a title in v2's create body.
        dropped = httpx.post(url + "/api/session", timeout=30,
                             json={"location": {"directory": directory}, "title": "app_7f3c"})
        dropped.raise_for_status()
        payload = dropped.json()
        ignored = (payload.get("data") or payload)["title"]
        assert DEFAULT_TITLE.match(ignored), (
            f"v2 kept {ignored!r}, so the create body is enough and the PATCH can go")

        # And what Sage does now. The read is v1's, because v1 is the API the prompt path runs on
        # and therefore the one `ensureTitle` reads the title from.
        sid = OpenCodeClient(url).create_session(directory)
        stored = httpx.get(f"{url}/session/{sid}", params={"directory": directory}, timeout=30)
        stored.raise_for_status()
        title = stored.json()["title"]

        assert title == Path(directory).name
        assert not DEFAULT_TITLE.match(title), title
