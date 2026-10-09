"""uvicorn's `--reload`, held while a Build turn is writing the app (#752).

The fastapi-antd preview restarts on every `.py` write, and a Build writes `app.py` many times, so
the preview restarted under the person on each one. While a hold is on, a change the reloader sees
restarts nothing and is kept back; when the hold lifts, it restarts the server once. A server
started while the hold is on — the end-of-turn check's own restart — starts held, and has nothing
kept back, so lifting the hold then restarts nothing.

The hold is a file: present means held. Its path is in `SAGE_PREVIEW_RELOAD_HOLD`.

The preview runs this file as `python -c <its source>` rather than `-m`. That keeps the app's own
directory first on the server's import path, as `python -m uvicorn` does, and asks nothing of the
interpreter but uvicorn.
"""
from __future__ import annotations

import os
from collections.abc import Callable

HOLD_ENV = "SAGE_PREVIEW_RELOAD_HOLD"


def gate(should_restart: Callable, held: Callable[[], bool]) -> Callable:
    """`should_restart`, with the changes it reports while `held()` kept back until it is not."""
    kept: list = []

    def gated(reloader):
        changes = should_restart(reloader)
        if held():
            if changes:
                kept[:] = changes
            return None
        changes = changes or list(kept)
        kept.clear()
        return changes or None

    return gated


if __name__ == "__main__":
    import sys

    from uvicorn.main import main
    from uvicorn.supervisors import ChangeReload

    hold = os.environ[HOLD_ENV]
    ChangeReload.should_restart = gate(ChangeReload.should_restart, lambda: os.path.exists(hold))
    sys.argv[0] = "uvicorn"
    main()
