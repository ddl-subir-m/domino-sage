"""ONE-APP-PLAN.md §2.3's spike addendum: the `orchestrator` name in `orchestrator/app.py` is now a
proxy over a `ContextVar`, not a concrete `Orchestrator`. This file pins the exact property that
made that safe: ~267 existing test files do `monkeypatch.setattr(app_module.orchestrator, "_x", fake)`
and rely on it reaching the SAME object every time, with no dispatch context active. If the proxy
ever regresses to a bare `__getattr__`-only forward, this file is what catches it — a plain
`__getattr__` proxy would let `setattr` shadow its own `__dict__` instead of reaching the real
object, and every one of these tests would start passing for the wrong reason (reading back the
value it just believed it set, off the proxy's own shadow) rather than fail loudly.
"""
from __future__ import annotations

from sage.orchestrator import app as app_module
from sage.orchestrator.app import _CURRENT_ORCHESTRATOR, current_orchestrator


def test_the_proxy_reads_forward_to_the_default_orchestrator():
    assert app_module.orchestrator._project is app_module._DEFAULT_ORCHESTRATOR._project


def test_a_monkeypatched_attribute_reaches_the_real_default_object(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(app_module.orchestrator, "_diag_depth", sentinel)
    assert app_module._DEFAULT_ORCHESTRATOR._diag_depth is sentinel
    # And reading it back through the proxy sees the SAME value — not a shadow on the proxy itself.
    assert app_module.orchestrator._diag_depth is sentinel


def test_monkeypatch_teardown_restores_the_real_objects_original_value():
    before = app_module._DEFAULT_ORCHESTRATOR._diag_depth

    def _patch_and_check(monkeypatch):
        monkeypatch.setattr(app_module.orchestrator, "_diag_depth", "temporary")
        assert app_module._DEFAULT_ORCHESTRATOR._diag_depth == "temporary"

    import _pytest.monkeypatch

    mp = _pytest.monkeypatch.MonkeyPatch()
    try:
        _patch_and_check(mp)
    finally:
        mp.undo()
    assert app_module._DEFAULT_ORCHESTRATOR._diag_depth == before


def test_a_bare_getattr_only_proxy_would_have_broken_this(monkeypatch):
    """Guards the REASON, not just the behavior: `setattr` on the proxy must not create an attribute
    on the proxy instance itself."""
    monkeypatch.setattr(app_module.orchestrator, "_diag_depth", "patched")
    assert "_diag_depth" not in vars(app_module.orchestrator)


def test_current_orchestrator_falls_back_to_default_with_no_active_dispatch():
    assert _CURRENT_ORCHESTRATOR.get() is None
    assert current_orchestrator() is app_module._DEFAULT_ORCHESTRATOR


class _Fake:
    tag = "fake"


def test_current_orchestrator_prefers_a_set_contextvar():
    fake = _Fake()
    token = _CURRENT_ORCHESTRATOR.set(fake)
    try:
        assert current_orchestrator() is fake
        # The proxy itself, not just the bare function, follows the ContextVar override.
        assert app_module.orchestrator.tag == "fake"
    finally:
        _CURRENT_ORCHESTRATOR.reset(token)
    assert current_orchestrator() is app_module._DEFAULT_ORCHESTRATOR
