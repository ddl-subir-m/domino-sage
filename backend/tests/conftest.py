import sys
import time
import weakref

import pytest

from sage.orchestrator.service import Orchestrator

# For `test_a_leaked_turn_lock_fails_the_test_that_took_it`, which runs pytest inside pytest to
# watch the autouse check below actually fire. A fixture nobody ever sees fail is indistinguishable
# from one that cannot.
pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _isolate_brand_override(monkeypatch, tmp_path):
    """Keep every test off the developer's own Appearance choice (ADR-0044).

    `brand.load()` reads a writable override, and its default path is under `~/.config`. Running
    the Workbench once on this machine writes that file — so without this, a theme somebody picked
    while trying the feature out becomes a pack every test in the suite silently resolves against,
    and the failure lands in whichever test happens to assert on a colour.

    Autouse and suite-wide rather than in `test_brand.py`, because the file is read by `load()` and
    `load()` is reached from the routes, the entry pages and `apply_voice` — the brand tests are the
    ones that would notice, not the only ones affected.
    """
    monkeypatch.setenv("SAGE_BRAND_OVERRIDE", str(tmp_path / "no-brand-override.json"))


_ORCHESTRATORS: "weakref.WeakSet[Orchestrator]" = weakref.WeakSet()
# Strong references to the orchestrators THIS test made, cleared at the start of each one. Without
# them the check is decided by the garbage collector: the commonest leak of all is a local `orch`
# whose lock is never released, and that orchestrator dies with the test frame, leaves the WeakSet
# and is never looked at. The same leak would then be red or green depending on whether some
# background thread happened to still be holding a reference at teardown.
_MADE_BY_THIS_TEST: list = []


def _remember(born):
    def __init__(self, *args, **kwargs) -> None:
        born(self, *args, **kwargs)
        _ORCHESTRATORS.add(self)
        _MADE_BY_THIS_TEST.append(self)
    __init__._remembers = True
    return __init__


# Wrapped once, here, because there is no other way to ask "which orchestrators exist" cheaply
# enough to ask it after all 5000-odd tests. Sweeping `gc.get_objects()` per test is the same
# answer at a hundred times the price, and putting the registry in `service.py` would be test
# scaffolding living in the thing under test.
#
# Guarded, because this module is reachable under two names (`tests.conftest` and, from a test
# package import, `.conftest`). A second import would otherwise wrap the wrapper against a second
# registry, and the check below would then watch a set that no orchestrator is ever added to —
# silently stopping, which is worse than never having been written.
if not getattr(Orchestrator.__init__, "_remembers", False):
    Orchestrator.__init__ = _remember(Orchestrator.__init__)


def held_turn_locks() -> list[Orchestrator]:
    """Every live orchestrator whose turn lock is held.

    A wedged workspace is not one of them. Holding the lock for the life of the process is what a
    wedge IS (#39) — the turn was given up on, the session may still be writing, and every later
    turn is refused on purpose until the workspace is restarted. The tests that assert on that
    hold it deliberately and for good.
    """
    return [orch for orch in list(_ORCHESTRATORS)
            if orch._turn_lock.locked() and not orch._turn_wedged]


def _shared_orchestrator():
    """The module-level orchestrator, if the routes have been imported. Read out of `sys.modules`
    so that asking the question never causes the import."""
    appmod = sys.modules.get("sage.orchestrator.app")
    return getattr(appmod, "orchestrator", None) if appmod is not None else None


@pytest.fixture(autouse=True)
def _turn_lock_is_handed_back(request):
    """Fail the test that leaves a turn lock held, rather than whoever is next in line for it.

    `-n auto` is xdist's `--dist load`, which hands out individual TESTS, not files. So adding one
    test file re-deals the whole suite across workers, and a lock left held does not redden the
    test that took it — it reddens whatever shares that worker afterwards. Which file that is
    changes with every diff, so the bill goes to the next person to add a test file, as a red in a
    file they never opened, pointing at their own change (#265).

    Which is why it asks only about orchestrators THIS test made, plus the one shared thing. A
    previous test's post-turn commit can take ITS orchestrator's lock while this test is running
    and hold it for the length of a push — that is the design (`_after_chat_turn`), not a leak, and
    billing it here would be the misattribution this fixture exists to stop, moved one test along.

    The one exception is the module-level orchestrator in `sage.orchestrator.app`. It is built at
    import, before any test, and shared by every test that goes through a route — so it is the only
    one a leak can carry across tests, and the only pre-existing one worth asking about. It is read
    out of `sys.modules` rather than imported, so this never drags the route module into a test
    that had no use for it.

    The wait is a grace, not a poll for trouble. A Chat turn hands its commit to a background
    thread on purpose, and that thread holds the turn lock for as long as a commit and a push take,
    which is routinely past the end of the test that started it — 102 of them, measured. A lock
    still held after the grace is one nobody is coming back for.

    Nothing is released. The thread that took the lock may still own it, and handing back a lock
    somebody is holding either blows up in their `finally` or frees a lock a LATER test is
    legitimately holding — cross-test corruption, which is the disease and not the cure. Scoping
    is what stops the cascade instead: one leak is one red, on the test that caused it.
    """
    shared = _shared_orchestrator()
    shared_was_free = shared is not None and not shared._turn_lock.locked()
    _MADE_BY_THIS_TEST.clear()
    yield
    mine = list(_MADE_BY_THIS_TEST)
    if shared_was_free:
        mine.append(shared)
    deadline = time.monotonic() + 5.0
    while True:
        held = {id(orch) for orch in held_turn_locks()}
        leaked = [orch for orch in mine if id(orch) in held]
        if not leaked:
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    pytest.fail(f"{request.node.nodeid} left {len(leaked)} turn lock(s) held. A turn lock is "
                f"released in a `finally`, or handed back with `_release_turn()`.")
