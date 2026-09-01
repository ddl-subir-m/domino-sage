"""Vite's error overlay knows whose error it is.

The overlay is right and still misleading. During a build the agent writes the app one file at a
time, so `App.tsx` lands holding imports for components that are several writes away, and Vite
raises `[plugin:vite:import-analysis] Failed to resolve import "./components/RowDetail"` over an app
that is merely mid-write — screenshotted live on 2026-08-31, once per failing write. The same
contradiction `test_crash_card.py` describes, on the surface React's boundary cannot reach: a
transform error takes the whole module graph down, so `main.tsx` never evaluates and nothing
imported from it is left watching. Hence a plugin in `vite.config.ts` injecting an inline script,
rather than a fifth file under `src/`.

The tests here guard the three ways that goes wrong: hiding an error with no build behind it,
staying hidden after the build has stopped, and shipping any of it to a published App.

Source assertions, in the style of test_crash_card.py — there is no browser in this suite. The
behaviour itself was verified live on 2026-09-01 against a rigged dev server: overlay hidden with
`running: true`, revealed within one poll of `running: false`, and revealed outright when the
build-state endpoint was unreachable.
"""
from __future__ import annotations

from pathlib import Path

CONFIG = (Path(__file__).resolve().parents[2] / "template" / "react-vite" / "vite.config.ts").read_text()


def test_the_gate_asks_the_builder_before_it_hides_anything():
    assert "project/build/state" in CONFIG, "the gate no longer asks whether a build is running"


def test_every_failure_path_reveals_the_overlay():
    """The one that matters most, and the direction the crash card is wrong in too.

    Hiding an error the creator has to act on is the damaging failure; showing one Sage is already
    fixing is only noise. So a refused fetch, a non-200, and a `running: false` all end at `reveal`.
    """
    assert 'catch(function () { return false; })' in CONFIG, "a failed check no longer answers no"
    assert "if (!running) { reveal(); return; }" in CONFIG


def test_the_overlay_comes_back_when_the_build_ends():
    """Sage gives up after a bounded number of fixes, so a build can end with the error standing.
    Without the poll the creator would be left with a hidden error and nothing coming to fix it."""
    assert "setInterval" in CONFIG
    assert "if (!still) reveal();" in CONFIG


def test_the_gate_never_reaches_a_published_app():
    """A published Domino App has no builder to ask, so the check would fail on every error and the
    card would be dead weight in the bundle. `apply: "serve"` is what keeps it out of `vite build`."""
    assert 'apply: "serve" as const' in CONFIG


def test_the_gate_is_registered_and_loads_outside_the_app_graph():
    """Both halves of why this lives here. Registered, or none of the above runs at all; injected
    into the document head, or it goes down with the very transform error it exists to explain."""
    assert "plugins: [react(), buildAwareOverlay(base)]" in CONFIG
    assert 'injectTo: "head-prepend" as const' in CONFIG
