"""A file under `.sage/threads/` whose bytes are not UTF-8 degrades, it does not 500 the rail (#326).

Six readers here wrapped `Path.read_text()` in `except (json.JSONDecodeError, OSError)`.
`UnicodeDecodeError` is neither: it is a SIBLING of `JSONDecodeError` under `ValueError`, so a file
that is not UTF-8 at all escaped the catch. `_read_meta` and `read_handoffs` are both reached by
`list_threads`, so one such file took out the whole Threads list — every Conversation in the
Project, not just the one that owns the file.

Same class as #303, which swept `workspace/manager.py` and stopped at the module boundary because
these sites do not stop a Project OPENING. They do not: `_sweep_deleted_conversations` shields the
open path with a blanket `except Exception`. That is why this was a separate ticket and not a
missed site.

The bytes here are real, not a mocked exception. A mock proves the handler runs; it does not prove
`read_text()` raises what the handler is written for, which is the half that was wrong.
"""
from __future__ import annotations

import ast
import collections
import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

# A UTF-16 JSON document: it opens with the byte-order mark `\xff\xfe`, and `\xff` is not a legal
# UTF-8 start byte in any position. Well-formed JSON to anything that decodes it correctly — the
# fault is the ENCODING, which is why every reader here reached for a JSON error and missed.
NOT_UTF8 = json.dumps({"items": [{"id": "x"}]}).encode("utf-16")


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Sage app\n")
    (t / ".gitignore").write_text("node_modules\n")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider([], []),
    )


def _needs_utf8_locale(tmp_path: Path) -> None:
    """Skip unless `read_text()` really refuses these bytes. `read_text()` with no argument decodes
    in the LOCALE's encoding and nothing in this repo pins one; under a latin-1-ish locale `\xff`
    decodes fine and the defect cannot arise at all."""
    probe = tmp_path / ".utf8probe"
    probe.write_bytes(NOT_UTF8)
    try:
        probe.read_text()
    except UnicodeDecodeError:
        return
    finally:
        probe.unlink()
    pytest.skip("this locale decodes 0xff, so read_text() never raises UnicodeDecodeError here")


@pytest.fixture()
def store(tmp_path: Path) -> ThreadStore:
    project = _orch(tmp_path).project(start_preview=False)
    return ThreadStore(project.record.path)


def _thread(store: ThreadStore) -> str:
    row = store.create("A conversation")
    return str(row["id"])


def test_read_text_really_does_raise_unicodedecodeerror_and_it_is_not_a_json_error(tmp_path: Path):
    """The premise the six catches were written against, checked rather than assumed."""
    _needs_utf8_locale(tmp_path)
    p = tmp_path / "meta.json"
    p.write_bytes(NOT_UTF8)
    with pytest.raises(UnicodeDecodeError) as caught:
        p.read_text()
    assert not isinstance(caught.value, json.JSONDecodeError)
    assert not isinstance(caught.value, OSError)
    assert isinstance(caught.value, ValueError)


def test_a_non_utf8_meta_does_not_stop_the_threads_list(tmp_path: Path, store: ThreadStore):
    """The headline. `list()` reads every Thread's record, so one bad file hid them all."""
    _needs_utf8_locale(tmp_path)
    keep = _thread(store)
    broken = _thread(store)
    store.meta_path(broken).write_bytes(NOT_UTF8)

    rows = store.list()
    assert [r["id"] for r in rows] == [keep], "the readable Thread must survive its neighbour"


def test_a_non_utf8_handoff_does_not_stop_the_threads_list(tmp_path: Path):
    """`list_threads` reads every Thread's handoffs to resolve `boundAppId` — a second reader on
    the same request, so guarding `_read_meta` alone left the rail broken.

    Through `list_threads`, not `read_handoffs` alone: the first version of this asserted on the
    store while its name promised the rail, which is a green that proves its assertion and not its
    reason. The `boundAppId` assertion is the half that needs the caller — a Thread whose handoffs
    will not read binds no app, and only the rail says so.
    """
    _needs_utf8_locale(tmp_path)
    orch = _orch(tmp_path)
    store = ThreadStore(orch.project(start_preview=False).record.path)
    tid = _thread(store)
    # A LIVE app, not a made-up id. `list_threads` drops an entry naming an app that no longer
    # exists, so binding to "app-1" would report `boundAppId: None` whether the file read or not —
    # an assertion that passes for the wrong reason and could never have failed.
    app_id = orch._wm.app_ids()[0]
    store.mark_handoff_bound(tid, app_id=app_id)
    assert [r["boundAppId"] for r in orch.list_threads()] == [app_id]   # it resolves while readable

    (store.thread_dir(tid) / "handoff.json").write_bytes(NOT_UTF8)
    assert store.read_handoffs(tid) == []
    assert [(r["id"], r["boundAppId"]) for r in orch.list_threads()] == [(tid, None)]


def test_the_whole_rail_survives_a_non_utf8_meta(tmp_path: Path):
    """Through `list_threads`, not just the store: the route is what a person actually hits, and a
    reader that shrugs under a unit test can still 500 behind a caller that reads it again."""
    _needs_utf8_locale(tmp_path)
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    keep = _thread(store)
    broken = _thread(store)
    store.meta_path(broken).write_bytes(NOT_UTF8)

    assert [r["id"] for r in orch.list_threads()] == [keep]


def test_a_non_utf8_session_reads_as_no_session(tmp_path: Path, store: ThreadStore):
    _needs_utf8_locale(tmp_path)
    tid = _thread(store)
    store.write_session_id(tid, "sess-1")
    (store.thread_dir(tid) / "session.json").write_bytes(NOT_UTF8)

    assert store.read_session(tid) is None
    assert store.read_session_id(tid) is None


def test_a_non_utf8_context_reads_as_no_chips(tmp_path: Path, store: ThreadStore):
    _needs_utf8_locale(tmp_path)
    tid = _thread(store)
    store.write_context(tid, {"items": [{"path": "public/data/x.json"}]})
    (store.thread_dir(tid) / "context.json").write_bytes(NOT_UTF8)

    assert store.read_context(tid) == {"items": []}


def test_a_non_utf8_artifacts_file_reads_as_no_artifacts(tmp_path: Path, store: ThreadStore):
    _needs_utf8_locale(tmp_path)
    tid = _thread(store)
    store.record_artifact(tid, path="examples/x/chart.png")
    (store.thread_dir(tid) / "artifacts.json").write_bytes(NOT_UTF8)

    assert store.read_artifacts(tid) == []


def test_a_non_utf8_legacy_index_is_left_alone_rather_than_read_as_empty(
        tmp_path: Path, store: ThreadStore):
    """`_migrate_index_rows` is the one site whose shrug must NOT be "there are no Threads": reading
    an unreadable index as empty would tombstone every directory in it and then unlink the only
    evidence. The guard widens what it survives; it does not change what it does."""
    _needs_utf8_locale(tmp_path)
    tid = _thread(store)
    index = store._legacy_index_path
    index.write_bytes(NOT_UTF8)

    assert [r["id"] for r in store.list()] == [tid]
    assert index.exists(), "an index that would not read was deleted anyway"
    assert not json.loads(store.meta_path(tid).read_text()).get("deleted")


def test_a_non_utf8_samples_file_reads_as_nothing_shared(tmp_path: Path):
    """The site #326 sent this pass to visit, aimed at the reader that actually runs.

    #326 found `_shared_samples` carrying the narrow pair over `SAMPLES_PATH` and left it, because a
    second `_shared_samples` shadowed it and widening dead code is theatre. #330 removed the
    shadowing — the name now resolves to that definition — so the gap the canary below was watching
    for opened, and this asserts BOTH readers of that one file rather than only the one that ran.

    `_shared_samples` is on the Live read path. An escaping `UnicodeDecodeError` there takes out the
    turn, where the same bytes cost `_shared` one answer, so the two readers are not equally
    forgiving and both are checked.
    """
    _needs_utf8_locale(tmp_path)
    from sage.orchestrator.service import SAMPLES_PATH

    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    samples = project.workspace.path / SAMPLES_PATH
    samples.parent.mkdir(parents=True, exist_ok=True)

    # A positive control first. Every assertion below is the FAIL-CLOSED value, which is also what
    # both readers answer if `SAMPLES_PATH` moved, if these bytes landed in the wrong directory, or if
    # they stopped reading the file at all. So the readers are shown a file they must react to before
    # they are shown one they must survive.
    samples.write_text(json.dumps({"tables": [{"name": "DWH.MARTS.ORDERS", "columns": ["ID"],
                                               "rows": [[1]]}]}))
    assert [s.rows.table for s in orch._shared(project)] == ["DWH.MARTS.ORDERS"]
    assert [t for _, t in orch._shared_samples(project)] == ["DWH.MARTS.ORDERS"]

    samples.write_bytes(NOT_UTF8)

    assert orch._shared(project) == []
    assert orch._shared_samples(project) == ()


def test_a_non_utf8_samples_file_does_not_stop_a_live_read_turn(tmp_path: Path):
    """The cost of the same bytes at the seam that decides, rather than at the reader alone.

    `_live_read_turn_for` calls `_shared_samples` while building the Turn, so a reader that lets
    `UnicodeDecodeError` out does not degrade one grant — it fails the whole turn before the person's
    read is even attempted. Driven through the Turn, because that is the caller whose failure is not
    recoverable.
    """
    _needs_utf8_locale(tmp_path)
    from sage.orchestrator.service import SAMPLES_PATH
    from sage.workspace.threads import ThreadStore

    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    samples = project.workspace.path / SAMPLES_PATH
    samples.parent.mkdir(parents=True, exist_ok=True)
    thread = str(ThreadStore(project.record.path).create("A conversation")["id"])

    # The same positive control, for the same reason: `shared == ()` is what an empty Turn carries too,
    # so the Turn is shown a readable record first. Named, not just non-empty — `!= ()` passes for an
    # empty list, or for some other table left behind in the record, neither of which shows that the
    # bytes written here are what the Turn read.
    samples.write_text(json.dumps({"tables": [{"name": "DWH.MARTS.ORDERS", "columns": ["ID"],
                                               "rows": [[1]]}]}))
    assert [t for _, t in orch._live_read_turn_for(thread).shared] == ["DWH.MARTS.ORDERS"]

    samples.write_bytes(NOT_UTF8)

    assert orch._live_read_turn_for(thread).shared == ()


def _extends_the_previous(node: ast.AST, name: str) -> bool:
    """Whether a decorator reaches THROUGH the previous binding — `@v.setter`, `@f.register`.

    An attribute access rooted at the name is the descriptor and dispatch pattern: `v.setter` asks the
    property object built by the earlier `def` for its setter, so that `def` is alive and the pair is
    deliberate.

    MENTIONING the name is not enough, which is where a looser version of this rule was wrong.
    `@functools.wraps(f)` passes the earlier function as an ARGUMENT and rebinds the name to a new
    one; the earlier body is then unreachable, which is the defect this sweep is for. So the name has
    to be the root of an attribute access, not merely somewhere in the decorator expression.

    Asking what the decorator reaches for beats an allowlist of decorator names, which leaked twice
    under review — `property`/`setter`/`deleter`, `overload`, `singledispatch`, `cached_property`, and
    whatever arrives next.
    """
    for dec in getattr(node, "decorator_list", []):
        while isinstance(dec, ast.Call):
            dec = dec.func
        if not isinstance(dec, ast.Attribute):
            continue
        root = dec
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id == name:
            return True
    return False


def _is_overload(node: ast.AST) -> bool:
    """`@overload`, whose stubs are consumed by the typing system rather than by later code.

    `typing.overload` and a bare `overload` both count.
    """
    for dec in getattr(node, "decorator_list", []):
        while isinstance(dec, ast.Call):
            dec = dec.func
        if (dec.attr if isinstance(dec, ast.Attribute) else getattr(dec, "id", "")) == "overload":
            return True
    return False


def _shadowed_in(body: list, where: str) -> list[str]:
    """Names defined more than once in ONE statement list, where a later definition kills an earlier.

    Judged over ALL of a name's definitions rather than by dropping one as it is read: a plain
    `def value` followed by `@property def value` leaves the plain one dead, and skipping the decorated
    one would hide #330's own shape behind a decorator.

    Overload STUBS are removed and the rule is applied to what is left, rather than the name being
    exempted because a stub exists. Exempting the name hides a third `def f` sitting after a legitimate
    overload group — a dead duplicate, exactly this sweep's subject, behind an exemption meant for
    something else.
    """
    defs: dict[str, list[ast.AST]] = collections.defaultdict(list)
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name].append(node)
    out = []
    for name, found in defs.items():
        real = [n for n in found if not _is_overload(n)]
        if len(real) < 2:
            continue
        # Every definition after the first has to reach through what came before it. One that does not
        # is the point where an earlier definition stopped being reachable.
        if all(_extends_the_previous(n, name) for n in real[1:]):
            continue
        out.append(f"{where}: {name} at {[n.lineno for n in real]}")
    return out


def _statement_lists(tree: ast.AST):
    """Every statement list in the file, so nesting is swept and not just the two outermost scopes.

    A duplicate inside ONE list is always a shadow. Separate lists are never compared, which is what
    keeps a legitimate conditional definition — the same name under `if` and under `else`, or under
    `try` and `except ImportError` — from reading as one. That is also this sweep's stated limit: two
    definitions in DIFFERENT lists, such as one under `if TYPE_CHECKING:` and one at module level, are
    not compared and are not found. What nesting buys is the duplicate WITHIN a nested body — inside a
    function, or inside a `TYPE_CHECKING` block — which the outermost two scopes miss entirely.
    """
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            value = getattr(node, field, None)
            if isinstance(value, list) and any(isinstance(v, ast.stmt) for v in value):
                yield value


# One source carrying a case per CONDITION: three shadows that must be reported, and three
# deliberate patterns beside them that must not be. A probe holding only one shadow proves the
# exemptions do not over-fire and says nothing about whether one of them is too WIDE.
_DETECTOR_PROBE = """
import functools
from typing import overload

class C:
    @property
    def kept(self): return 1
    @kept.setter
    def kept(self, v): self._v = v

    @functools.singledispatchmethod
    def dispatched(self, x): return x
    @dispatched.register
    def dispatched(self, x: int): return x + 1

    @overload
    def stubbed(self, x: int) -> int: ...
    @overload
    def stubbed(self, x: str) -> str: ...
    def stubbed(self, x): return x

    def plain_pair(self): return 1
    def plain_pair(self): return 2

    def then_property(self): return 1
    @property
    def then_property(self): return 2

    @overload
    def after_overload(self, x: int) -> int: ...
    def after_overload(self, x): return x
    def after_overload(self, x): return 0
"""


def test_the_shadowing_detector_still_detects():
    """A plant per condition, kept in the file rather than run once by hand (#330).

    The sweep below asserts that a list is empty. Empty is also what the detector returns when its
    `isinstance` tuple loses a member, when the statement-list field names go stale, or when an
    exemption grows too wide — and the test then goes on being green under a name saying nothing in the
    backend is shadowed. So the detector is asked a question whose answer is known.

    Asserted as an exact set, not a count, because three of these conditions are shadows and three are
    deliberate: a count alone cannot tell a missed shadow from a newly false accusation.

    `after_overload` is the one a per-NAME overload exemption misses. Two real definitions sit after
    the stub, the second killing the first, and exempting the whole name because a stub exists hides it.
    """
    found = []
    for body in _statement_lists(ast.parse(_DETECTOR_PROBE)):
        found += _shadowed_in(body, "probe")

    assert sorted(f.split(": ")[1].split(" at ")[0] for f in found) == [
        "after_overload", "plain_pair", "then_property"], f"the detector reported {found}"


def test_no_definition_in_the_backend_is_shadowed_by_a_later_one():
    """Replaces the canary that pinned the shadowing, and widens it (#330, criterion 5).

    That canary asserted `_shared_samples` still resolved BELOW line 10000 — it pinned the defect in
    place so the dead site would not be widened for nothing, and it did its job: it reddened the
    moment the shadowing was removed. Pinning it any longer would pin the bug.

    What replaces it asks the general question, because one shadowed definition that nothing reports
    is unlikely to be the only one. `ruff --select F811` is NOT that report: measured on this very
    file, it catches a duplicate method five lines apart, a duplicate at either end of a 6,000-method
    class, and a `turn_busy` injected deliberately — and it misses a `_shared_samples` planted inside
    `class Orchestrator`, for a reason nobody has found. So the evidence comes from the AST, where a
    duplicate name in one statement list is a fact and not a heuristic.

    `tests/` is swept beside `sage/`, because a test function shadowed by a later one of the same name
    is a test that silently never runs — the same defect, and harder to notice.

    Names bound twice by an `Assign` are not flagged: rebinding a name is ordinary Python. Two `def`s
    of one name in one statement list, the later one building on nothing, is the defect class.
    """
    root = Path(__file__).resolve().parent.parent
    shadowed: list[str] = []
    scanned: dict[str, int] = {}
    for sub in ("sage", "tests"):
        for path in sorted((root / sub).rglob("*.py")):
            # Bytes, not `read_text()`. This file's whole subject is that `read_text()` decodes in the
            # LOCALE's encoding, and `ast.parse` honours a source encoding declaration itself.
            try:
                tree = ast.parse(path.read_bytes())
            except SyntaxError as e:
                # Named, or an unparseable file reds a test about encodings with no hint of which file
                # is at fault. Raised rather than skipped: nothing in here should fail to parse.
                raise AssertionError(f"{path.relative_to(root)} does not parse: {e}") from e
            scanned[sub] = scanned.get(sub, 0) + 1
            # Relative to the backend root, because basenames repeat in here — `service.py` alone is
            # two files — and a duplicate reported by basename does not say which one to open.
            for body in _statement_lists(tree):
                shadowed += _shadowed_in(body, str(path.relative_to(root)))

    # A floor PER ROOT, because `shadowed == []` over nothing at all passes, and a sweep that reached
    # no files reads exactly like a sweep that found nothing wrong. Per root rather than on the total:
    # `tests/` is four times the size of `sage/`, so a single total floor is cleared by `tests/` alone
    # and would say nothing about whether `sage/` — the tree this ticket is about — was opened.
    assert scanned.get("sage", 0) > 50 and scanned.get("tests", 0) > 200, (
        f"this swept {scanned}; it is not reaching the trees it names")
    assert shadowed == [], (
        "a definition is shadowed by a later one of the same name in the same statement list; the "
        f"second wins and the first is dead, exactly as #330's `_shared_samples` was — {shadowed}")
