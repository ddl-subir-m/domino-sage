"""`_apply_static_platform_override` (ONE-APP-PLAN.md §2.4): how a laptop's preview `/api/domino/*`
relay gets a token with no sidecar to ask, and — just as important — how it does NOT.

The relay runs `sage_domino.py` IN THE ORCHESTRATOR PROCESS (`domino_module` execs the file there;
the preview proxy answers `/api/domino/*` itself before a request ever reaches the spawned preview
child). `sage_domino.py`'s `token()`/`platform_host()` are plain `os.environ` reads by design — the
same file runs unmodified inside a published app's own process — so the fix patches the loaded
module's OWN `token`/`platform_host` attributes directly, and never touches `os.environ`.

That last part is the point of these tests, not a footnote: an earlier version of this fix set
`SAGE_DOMINO_TOKEN` as a real environment variable, once in the preview child's own spawn env and
once considered at the orchestrator-process level. Both were reverted before landing — a process-wide
env var is inherited by every OTHER child the orchestrator spawns too, including OpenCode's own
server (`driver/server.py`'s `_env()` copies `os.environ` wholesale) and the preview child itself
(which runs the agent's own generated code). Either shape hands a real Domino credential to code this
process does not control, which is exactly what ONE-APP-PLAN.md §2.8 rules out ("the agent never
holds a Domino token"). These tests pin the module-attribute shape and guard against that regression.
"""
from __future__ import annotations

import os

from sage.orchestrator.app import _apply_static_platform_override


class _FakeModule:
    def token(self) -> str:
        return "sidecar-would-answer-this"

    def platform_host(self) -> str:
        return "http://in-cluster-only"


class _FakeTokenSource:
    def __init__(self, kind: str, host: str = "https://dogfood.domino.tech", tok: str = "laptop-pat-1"):
        self.kind = kind
        self.api_host = host
        self._tok = tok

    def bearer(self) -> str:
        return self._tok


def test_a_static_source_overrides_both_functions_on_the_module():
    module = _FakeModule()
    _apply_static_platform_override(module, _FakeTokenSource("static"))
    assert module.token() == "laptop-pat-1"
    assert module.platform_host() == "https://dogfood.domino.tech"


def test_a_sidecar_source_leaves_the_module_alone():
    """A real workspace/App already has a reachable sidecar — nothing to override."""
    module = _FakeModule()
    _apply_static_platform_override(module, _FakeTokenSource("sidecar"))
    assert module.token() == "sidecar-would-answer-this"
    assert module.platform_host() == "http://in-cluster-only"


def test_no_token_source_leaves_the_module_alone():
    module = _FakeModule()
    _apply_static_platform_override(module, None)
    assert module.token() == "sidecar-would-answer-this"


def test_a_none_module_is_a_no_op_not_a_crash():
    """`domino_module` answers None for a template with no relay — nothing to patch."""
    _apply_static_platform_override(None, _FakeTokenSource("static"))  # must not raise


def test_the_override_never_touches_os_environ():
    """The regression this whole file exists to catch: a real credential must never become a
    process-wide environment variable, which every other child this process spawns would inherit."""
    before = dict(os.environ)
    module = _FakeModule()
    _apply_static_platform_override(module, _FakeTokenSource("static"))
    assert os.environ == before
    assert "SAGE_DOMINO_TOKEN" not in os.environ
