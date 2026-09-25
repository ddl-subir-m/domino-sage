import collections
import subprocess
import sys
import time
import weakref
from dataclasses import replace

import pytest

from sage import timing
from sage.build_policy import BuildPolicy
from sage.driver.server import OpenCodeServer
from sage.orchestrator import service
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


@pytest.fixture(autouse=True)
def _a_fake_template_is_a_react_vite_one(monkeypatch):
    """Pin the stack a bare `ensure()` seeds to react-vite for the suite (#490).

    Every fake template a test builds — `package.json`, `src/App.tsx`, a `node_modules/.bin/vite`
    sentinel — is a react-vite one, and ~40 files build one and call `ensure()` without naming a
    stack. The deployment's default is the no-build stack, so without this pin each of those would
    seed `template/fastapi-antd` into a workspace whose fixture then looks for `src/App.tsx`.

    The pin is a shared state, so one test removes it on purpose:
    `test_a_bare_ensure_seeds_the_no_build_stack_by_default` proves the real default on the real
    templates. A test about the OTHER stack names it (`create_app(stack=...)`) or removes the pin.
    """
    monkeypatch.setenv("SAGE_DEFAULT_STACK", "react-vite")


@pytest.fixture(autouse=True)
def _no_wait_for_a_preview_that_never_reports(monkeypatch):
    """A build turn that wrote code polls the preview for a runtime error for 4s before it is
    done. No test runs a preview, so nothing ever reports, and every build turn that reached the
    poll paid the whole 4s — 58 files stubbed the method by hand to skip it and the rest paid.
    One check with no wait keeps the branch live: an error a test recorded before the poll is
    still found, and none arrives during it."""
    policy = replace(BuildPolicy(), runtime_error_wait_seconds=0.0, page_ack_wait_seconds=0.0)
    monkeypatch.setattr(service, "load_build_policy", lambda: policy)
    # A test that hands the Orchestrator a policy of its own steps around the line above, and since
    # #557 the page-ack wait is a second poll on the same preview nobody runs: an explicit
    # `BuildPolicy(no_edit_nudge_limit=0)` paid the default ten seconds per build turn and
    # restarted a real dev server to spend them. Zero it there too — unless the test set it, which
    # is how the tests OF the wait opt in. IN PLACE, not through `replace`: the service keeps the
    # very object it was handed, and `test_build_policy` asserts that identity.
    default_ack = BuildPolicy().page_ack_wait_seconds
    original_init = service.Orchestrator.__init__

    def init(self, *args, build_policy=None, **kwargs):
        if build_policy is not None and build_policy.page_ack_wait_seconds == default_ack:
            object.__setattr__(build_policy, "page_ack_wait_seconds", 0.0)  # frozen dataclass
        original_init(self, *args, build_policy=build_policy, **kwargs)

    monkeypatch.setattr(service.Orchestrator, "__init__", init)


# Spread through the collection, slowest file first, so a `-n auto` run does not end on one worker
# alone with a test that was dealt last. Measured 2026-09-20 with `--durations=0`: the suite's
# worker-seconds over 14 workers is ~190s and the run took 244s, because these files' tests began
# after the 94% mark. `test_wedged_turn.py` waits out real deadlines on purpose (49s, 30s, 20s,
# 20s); the real-OpenCode files run strictly in series behind the lock in `opencode_server.py`.
#
# Spread, not moved to the front: xdist hands each worker a CONTIGUOUS slice of the collection,
# so sorting these files first put every one of them on one worker, which then ran alone for the
# last 230s of a 408s run. Re-derive the list from `--durations=20` on a full run, not from memory.
_SPREAD_FIRST = (
    "test_wedged_turn.py",
    "test_a_repeated_call_stops_the_turn.py",
    "test_csv_calculation_opencode.py",
    "test_csv_text_analysis_data_used.py",
    "test_one_lock_serializes_every_opencode_boot.py",
)


def pytest_collection_modifyitems(items):
    slow = sorted((i for i in items if i.path.name in _SPREAD_FIRST),
                  key=lambda i: _SPREAD_FIRST.index(i.path.name))
    if not slow:
        return
    rest = [i for i in items if i.path.name not in _SPREAD_FIRST]
    step = max(1, len(rest) // len(slow))
    spread = []
    for k, item in enumerate(slow):
        spread.append(item)
        spread.extend(rest[k * step:(k + 1) * step])
    spread.extend(rest[len(slow) * step:])
    items[:] = spread


@pytest.fixture
def ledger(monkeypatch):
    """A turn ledger this test alone writes to and reads back.

    `sage.timing` keeps ONE record and ONE ring for the whole process, so a test that asks it what
    just ran is asking a question about the process (#339). Without this, `last_finished()` after
    the first ledger test in a worker never returns `None` again — it returns whatever the previous
    test left, so a build that stopped recording reads as a stranger's turn rather than as nothing,
    and the assertion that was written to say so cannot fire.

    Restored rather than only cleared, for the same reason as the turn-lock check below: a record
    this test leaves open must not travel to whoever shares the worker next.

    One residual, and it is the reverse direction. A turn left OPEN by an earlier test, with a
    background thread still on it, is blanked here — so that thread's `finish_turn` finds nothing
    and never rings it, and the restore hands the still-open record back afterwards, to be rung
    `abandoned` by the next `start_turn`. A turn that finished is then reported as one that died.
    Diagnostics only: no test reads `_history` without this fixture. A test that does would need
    the leak named where it happens, the way the turn-lock check below names it.
    """
    monkeypatch.setattr(timing, "_history", collections.deque(maxlen=timing._HISTORY))
    monkeypatch.setattr(timing, "_current", None)


@pytest.fixture
def ledger_required(ledger):
    """`ledger`, and the flag has to be on for the test to mean anything.

    Read here rather than at import: `timing.enabled()` is a per-call environment read everywhere
    else in the module, and a `skipif` evaluated at collection would disagree with it the moment
    anything set the variable afterwards.
    """
    if not timing.enabled():
        pytest.skip("SAGE_TIMING is off: the ledger records nothing, so there is no turn to "
                    "assert on. The no-op is covered by "
                    "test_the_turn_ledger_says_which_turn_you_get.py.")


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


# The OpenCode servers THIS test started, cleared at the start of each one (#536).
_SERVERS_STARTED_BY_THIS_TEST: list = []
# Taken at import: the driver tests replace `subprocess.Popen` itself with a fake while they run.
_POPEN = subprocess.Popen


def _remember_start(start):
    def wrapped(self, *args, **kwargs):
        _SERVERS_STARTED_BY_THIS_TEST.append(self)
        return start(self, *args, **kwargs)
    wrapped._remembers = True
    return wrapped


# Guarded for the same reason as `_remember` above: this module is reachable under two names.
if not getattr(OpenCodeServer.start, "_remembers", False):
    OpenCodeServer.start = _remember_start(OpenCodeServer.start)


@pytest.fixture(autouse=True)
def _opencode_server_is_stopped(request):
    """Stop a real `opencode serve` the test left running, and fail THAT test (#536).

    `OpenCodeServer.start` gives OpenCode a session of its own, so the server does not die with the
    test process: only `stop()` ends it. A test whose `Orchestrator` has no fake OpenCode reaches a
    real one on its first turn and, with no `shutdown()`, leaves it running under `launchd` for
    good — three of them ran for two days from a draft test on 2026-09-22.

    Unlike a turn lock, stopping it here is safe: the process belongs to this test and nobody
    else can be holding it. Only a real `subprocess.Popen` counts; the driver tests' fakes stand
    in for one without being a process.
    """
    _SERVERS_STARTED_BY_THIS_TEST.clear()
    yield
    left = [s for s in _SERVERS_STARTED_BY_THIS_TEST
            if isinstance(s._proc, _POPEN) and s._proc.poll() is None]
    _SERVERS_STARTED_BY_THIS_TEST.clear()
    for server in left:
        server.stop()
    if left:
        pytest.fail(f"{request.node.nodeid} left {len(left)} OpenCode server(s) running. A test "
                    f"that starts one calls `stop()` (or `Orchestrator.shutdown()`), or injects "
                    f"`opencode_client=` so no real server starts.")
