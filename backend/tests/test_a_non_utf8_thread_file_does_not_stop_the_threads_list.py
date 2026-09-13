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

    `service.py:9321`'s `_shared_samples` does carry the narrow pair over `SAMPLES_PATH`, but that
    definition is unreachable: a SECOND `_shared_samples` further down the same class shadows it,
    so widening its catch would have been theatre. `_shared` is what reads that file, through
    `_read_json`, which already takes `ValueError` — so this asserts the behaviour rather than
    claiming the site was fixed. The shadowing itself is a live defect and is filed separately.
    """
    _needs_utf8_locale(tmp_path)
    from sage.orchestrator.service import SAMPLES_PATH

    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    samples = project.workspace.path / SAMPLES_PATH
    samples.parent.mkdir(parents=True, exist_ok=True)
    samples.write_bytes(NOT_UTF8)

    assert orch._shared(project) == []


def test_the_dead_shared_samples_is_still_shadowed(tmp_path: Path):
    """Pins the reason the site above was not edited. The day this reddens, `service.py:9321` is
    reachable again and its narrow catch is a real gap — fix it then, not before."""
    import inspect

    from sage.orchestrator.service import Orchestrator

    _, line = inspect.getsourcelines(Orchestrator._shared_samples)
    # On the resolved LINE, not on the docstring: prose is reworded by people who have not changed
    # anything, and a real un-shadowing that happened to keep the same opening sentence would slip
    # through. The number is the fact — the dead definition sits above it, near 9321.
    assert line > 10000, (
        f"_shared_samples now resolves to line {line}; if that is the definition near 9321, its "
        "narrow catch over SAMPLES_PATH is reachable again and is a real gap (#330)")
