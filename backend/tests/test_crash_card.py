"""The preview's crash card knows whose crash it is.

During a build the agent rewrites the app's files repeatedly — three writes to App.tsx inside 25
seconds, live on 2026-08-24 — and the versions in between throw. React's boundary catches each one
and, until now, showed a red "The app crashed while rendering / fix the code below" over an app that
was simply mid-edit, while the chat two panes over said "app crashed at runtime — fixing". Two
surfaces contradicting each other about the same error, several times per build.

So the card asks the builder whether a turn is running and softens only then. The tests here guard
the three ways that goes wrong: softening when nothing is coming to fix it, staying soft after the
build has stopped, and quieting the report the autofix loop depends on.

Source assertions, in the style of test_builder_composer.py — there is no browser in this suite. What
they buy is that each of these is a one-line revert away and none of them can happen silently.
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite" / "src"
BOUNDARY = (TEMPLATE / "ErrorBoundary.tsx").read_text()


def _method(src: str, sig: str) -> str:
    """One method's body, ending at its own closing brace rather than at whatever comes next —
    so a test reads the same thing whether or not the methods around it exist."""
    start = src.index(sig)
    return src[start:src.index("\n  }\n", start)]
REPORTER = (TEMPLATE / "reportRuntimeError.ts").read_text()


def test_the_crash_is_still_reported_whether_or_not_a_build_is_running():
    """The load-bearing one — and the only test here that passed before this change too.

    It guards an invariant the change puts at risk rather than one it establishes: `_await_runtime_error`
    blocks on this report, and it is the only channel that catches a throw tsc cannot see. Now that the
    card asks whether a build is running, "only report while one is" is the obvious tidy-up to reach
    for — and it would disable runtime autofix entirely while leaving the card looking perfect.
    """
    body = _method(BOUNDARY, "componentDidCatch(error")
    report = body.index("reportRuntimeError(error.message")
    assert "if" not in body[:report], "the report is now behind a condition"


def test_the_card_asks_the_builder_before_it_softens():
    assert "buildIsRunning" in BOUNDARY
    # And starts from the blunt card, so a check that never answers cannot leave a reassuring one up.
    derived = _method(BOUNDARY, "getDerivedStateFromError")
    assert "building: false" in derived


def test_the_reassurance_stops_when_the_build_does():
    """Sage gives up after a bounded number of runtime fixes, so a build can end with the app still
    broken. Without the poll the card would keep promising a fix that had already stopped coming."""
    assert "window.setInterval" in BOUNDARY
    assert "if (!still) this.stopPolling();" in BOUNDARY
    # An interval that outlives the boundary keeps polling a build nobody is watching.
    assert "this.stopPolling()" in _method(BOUNDARY, "componentWillUnmount(")


def test_both_messages_survive():
    # The point is that there are two. Collapsing to one is how this regresses.
    assert "The app crashed while rendering" in BOUNDARY
    assert "Sage is still building this app" in BOUNDARY


def test_a_published_app_never_claims_sage_is_fixing_it():
    """A published Domino App has no builder to ask, and the fetch would fail — but "fails" must mean
    "no", not "unknown". Every exit in this function has to be false except the confirmed one."""
    fn = REPORTER[REPORTER.index("export async function buildIsRunning"):]
    fn = fn[:fn.index("\n}")]
    assert "if (!import.meta.env.DEV) return false;" in fn
    assert "catch {\n    return false;\n  }" in fn
    assert "if (!res.ok) return false;" in fn


def test_the_path_the_card_asks_for_is_a_route_the_builder_serves():
    """The one cross-language contract here: a typo in this string is a card that silently never
    softens, with nothing on either side to say so."""
    from sage.orchestrator import app as appmod

    assert 'fetch(API + "project/build/state")' in REPORTER
    # API is the builder's prefix with `preview/` stripped, so the route is that path under /api/.
    served = {getattr(r, "path", None) for r in appmod.control_app.routes}
    assert "/api/project/build/state" in served
