"""A build that ran the app's queries and watched every one of them fail says so (#203).

THE LIVE FAILURE. Cloud-dogfood, 2026-09-07. A dashboard was built whose six queries all failed
with `Object 'GONG' does not exist or not authorized`. Sage ran those queries itself — that is what
`preview/queries.py` is for — logged all six failures, and finished the turn with "Typecheck passed.
Done — build is clean". An app whose entire data path was broken, reported as finished and clean.

WHY THE FAILURES REACHED NOBODY. `CachingExecutor.__call__` wrote one `log.warning` per failure and
re-raised. No caller read that line. The end-of-turn check that DOES run, `catalog_problems`, is
purely static: it checks that `.sage/queries.json` parses and that declared params match the `:name`
placeholders, and it never executes SQL — so a query against a table that does not exist is
invisible to it.

WHAT THIS IS NOT. Not a gate, for the same reason `data-source-unasked` beside it is not one
(ADR-0010): a store that was down for ten seconds must not cost a creator the code the turn wrote.
So the work is still committed and the app is still marked built — the turn's OUTCOME is what stops
lying, and the agent meets the reason on its next turn.

WHY "COULD NOT CHECK" IS KEPT APART FROM "NOTHING FAILED". A preview that never started ran no
query, so it saw no failure, and reporting that as a clean data path would be the same lie in a
quieter voice. `catalog_problems` already models this with `checked is None`; `failures()` answers
None for it.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import _PERSISTED_EVENTS, Orchestrator
from sage.preview.queries import CachingExecutor, PreviewQueries
from sage.resources.builtapp import serve_module
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"

# What the store actually said in the run this ticket came from, redacted the way `_readable` would
# leave it. Carried as one constant because three tests assert the creator, the agent and the
# executor are all shown the SAME sentence.
REFUSED = "DatasourceError: Object 'GONG' does not exist or not authorized"


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class FakeQueries:
    """The preview's query server, standing in for one that ran and saw what it saw.

    `answer` is what `failures()` gives back: a dict of query name to reason, or None for a preview
    that could not run queries at all.
    """

    def __init__(self, answer) -> None:
        self.answer = answer

    def failures(self):
        return self.answer


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building an app\n")
    return t


def _orch(tmp: Path, turns: list[Turn]) -> Orchestrator:
    oc = FakeOpenCode(tmp / "mnt" / "code", turns)
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
                        resources=FakeResourceProvider())
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def _catalog(orch: Orchestrator, entries: list[dict]) -> None:
    """Write `.sage/queries.json`, so the app is one that asks its store something."""
    path = orch.project(start_preview=False).workspace.path / ".sage" / "queries.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


def _ready(orch: Orchestrator, answer) -> None:
    """Bind a store, write a catalog against it, and hand the turn a preview that saw `answer`."""
    orch.bind_data_source("ds-dwh")
    _catalog(orch, [{"name": "usage", "binding": "ds-dwh", "sql": "SELECT 1"}])
    orch.project(start_preview=False).queries = FakeQueries(answer)


def _write_catalog(workspace: Path, statements: dict[str, str]) -> None:
    (workspace / ".sage").mkdir(parents=True, exist_ok=True)
    (workspace / ".sage" / "queries.json").write_text(
        json.dumps([{"name": name, "binding": "ds-dwh", "sql": sql}
                    for name, sql in statements.items()]), encoding="utf-8")


def _preview_with_catalog(tmp: Path, statements: dict[str, str]) -> PreviewQueries:
    """A preview standing where the live one stood: the real `serve.py`, a real catalog on disk, and
    a real executor holding whatever it last failed with. Everything `refresh` reads and nothing it
    does not — the loopback server and its thread play no part in re-reading a file.

    The two rewrites below each change the catalog's SIZE, so the stamp moves without this having to
    wait out an mtime tick.
    """
    workspace = tmp / "app"
    _write_catalog(workspace, statements)
    previews = PreviewQueries(workspace, TEMPLATE)
    previews._module = serve_module(TEMPLATE)
    previews._server = SimpleNamespace(sage_queries=previews._module.load_queries(workspace))
    previews.executor = CachingExecutor(
        lambda query, params: {"columns": [], "rows": [], "truncated": False})
    previews._stamp = previews._catalog_stamp()
    return previews


def _rewrite_catalog(tmp: Path, statements: dict[str, str]) -> None:
    """What the agent does mid-turn, which trips no HMR reload: `.sage/` is not under `src/`."""
    _write_catalog(tmp / "app", statements)


# ---- the turn stops reporting a clean build ---------------------------------------------------


def test_a_build_whose_queries_all_fail_is_not_clean(tmp_path: Path):
    """The whole ticket in one assertion. Typecheck passed and the code was written, so every
    signal the turn had said "clean" — and the one thing that had actually been tried against the
    real store had failed six times out of six."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, {"usage": REFUSED})

    events = list(orch.build_stream("build it"))

    done = _of(events, "done")[0]
    assert done["ok"] is False
    assert done["decision"] != "typecheck clean"


def test_the_person_is_told_which_query_failed_and_why(tmp_path: Path):
    """"Does not report success" is half the acceptance. The other half is that the person reading
    the transcript learns the app cannot read its data AND what the store said about it — the
    sentence that was going to `log.warning` and nowhere else."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, {"usage": REFUSED})

    events = list(orch.build_stream("build it"))

    message = _of(events, "data-source-failed")[0]["message"]
    assert "usage" in message
    assert "GONG" in message


def test_the_notice_comes_before_the_turn_ends(tmp_path: Path):
    """It belongs to the turn it describes. After `done` it would replay in the wrong order on
    reload, the way the unasked notice beside it would."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, {"usage": REFUSED})

    kinds = [e.get("type") for e in orch.build_stream("build it")]

    assert kinds.index("data-source-failed") < kinds.index("done")


def test_the_notice_survives_a_reload(tmp_path: Path):
    """The transcript is rebuilt from persisted events, so an event missing from the allow-list is
    one the creator sees once and never again."""
    assert "data-source-failed" in _PERSISTED_EVENTS


def test_the_ending_does_not_buy_a_gateway_listing(tmp_path: Path):
    """`endedBadly` fetches a model listing after a failed turn, because a turn that just failed is
    the one moment that answer is worth paying for (ADR-0027). Nothing was asked of the LLM Gateway
    here — the store refused a query — so this decision has to be in the exemption list, or every
    broken table buys a listing that cannot say anything about it.

    Asserted against the source because the branch IS the behaviour, and the repo already pins
    `data-source-unasked`'s own reducer branch the same way."""
    store = (Path(__file__).resolve().parents[1]
             / "sage" / "workbench" / "js" / "store.js").read_text()

    fault = store[store.index("const NO_PLATFORM_FAULT"):]
    assert "'queries failed'" in fault[:fault.index("\n")]


# ---- and it stays best-effort -------------------------------------------------------------------


def test_the_work_is_still_saved(tmp_path: Path):
    """A store that was down for ten seconds must not cost the creator the code this turn wrote.
    The turn's OUTCOME is what stops lying; the commit and the built flag are unchanged, which is
    also what keeps this from being the gate ADR-0010 rules out."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, {"usage": REFUSED})

    list(orch.build_stream("build it"))

    assert orch.project(start_preview=False).workspace.built_at


def test_a_build_whose_queries_worked_is_unchanged(tmp_path: Path):
    """The no-false-alarm test. A preview that ran every query and saw nothing fail answers `{}`,
    which is an answer and not an absence."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, {})

    events = list(orch.build_stream("build it"))

    assert _of(events, "data-source-failed") == []
    assert _of(events, "done")[0]["ok"] is True
    assert _of(events, "done")[0]["decision"] == "typecheck clean"


def test_a_build_with_no_queries_is_unchanged(tmp_path: Path):
    """Nothing bound, nothing asked, nothing to fail."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.project(start_preview=False).queries = FakeQueries({})

    events = list(orch.build_stream("build it"))

    assert _of(events, "data-source-failed") == []
    assert _of(events, "done")[0]["ok"] is True


def test_a_preview_that_could_not_run_queries_is_not_a_failure(tmp_path: Path):
    """`checked is None`, the third answer. A preview that never came up ran no query, so it saw no
    failure — and saying "no failures" for it would be this ticket's bug again, upside down."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, None)

    events = list(orch.build_stream("build it"))

    assert _of(events, "data-source-failed") == []
    assert _of(events, "done")[0]["ok"] is True


def test_a_turn_that_failed_carries_no_second_sentence(tmp_path: Path):
    """A build cut off part-way has its own message, and this one under it would read as part of
    that fault rather than as a fact about the app."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"}, broken_write=True)])
    _ready(orch, {"usage": REFUSED})

    events = list(orch.build_stream("build it"))

    assert _of(events, "done")[0]["ok"] is False
    assert _of(events, "data-source-failed") == []


def test_a_failing_preview_never_crashes_the_turn(tmp_path: Path):
    """Best-effort throughout. Whatever `failures()` does, it is not allowed to be the thing that
    ends a build that otherwise worked."""
    class Exploding:
        def failures(self):
            raise RuntimeError("no")

    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.bind_data_source("ds-dwh")
    orch.project(start_preview=False).queries = Exploding()

    events = list(orch.build_stream("build it"))

    assert _of(events, "done")[0]["ok"] is True


# ---- the agent meets it on its next turn --------------------------------------------------------


def test_the_agent_is_told_what_the_store_refused(tmp_path: Path):
    """`_recheck_app_data` puts the app's own sentence in front of the agent while the creator is
    still in the conversation that produced it. This extends that from static faults to real ones:
    the agent's next turn opens on the query name and the store's own words."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, {"usage": REFUSED})

    list(orch.build_stream("build it"))
    agents = (orch.project(start_preview=False).workspace.path / "AGENTS.md").read_text()

    assert "usage" in agents
    assert "GONG" in agents


def test_the_agent_is_not_told_it_once_the_query_works(tmp_path: Path):
    """AGENTS.md is re-read every turn, so a section that outlives its reason is a standing lie."""
    orch = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    _ready(orch, {})

    list(orch.build_stream("build it"))
    agents = (orch.project(start_preview=False).workspace.path / "AGENTS.md").read_text()

    assert "GONG" not in agents


# ---- what the executor remembers ----------------------------------------------------------------


class _Query:
    def __init__(self, name: str, sql: str = "SELECT 1") -> None:
        self.name, self.sql = name, sql


def test_a_failing_query_is_remembered_by_the_name_that_failed(tmp_path: Path):
    """Keyed on the name, not on the call: one screen reloading over HMR fires the same broken
    query dozens of times in a turn, and the creator needs one line about it, not forty."""
    def refuse(query, params):
        raise RuntimeError("Object 'GONG' does not exist or not authorized")

    executor = CachingExecutor(refuse, redact=serve_module(TEMPLATE)._readable)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            executor(_Query("usage"), {})

    assert list(executor.failures) == ["usage"]
    assert "GONG" in executor.failures["usage"]


def test_a_query_that_starts_working_is_forgotten():
    """The agent fixes the SQL, the reload answers, and the old reason has to go with it — a build
    reported as broken over a query that now works is the same defect facing the other way."""
    answers = [RuntimeError("Object 'GONG' does not exist or not authorized"), None]

    def flaky(query, params):
        problem = answers.pop(0)
        if problem is not None:
            raise problem
        return {"columns": [], "rows": [], "truncated": False}

    executor = CachingExecutor(flaky)
    with pytest.raises(RuntimeError):
        executor(_Query("usage", "SELECT bad"), {})
    executor(_Query("usage", "SELECT good"), {})

    assert executor.failures == {}


def test_a_rewritten_statement_drops_what_the_old_one_failed_with(tmp_path: Path):
    """The fix that no reload announces. `.sage/queries.json` is not under `src/`, so rewriting it
    trips no HMR reload and nothing fires the query again — and fixing a query is the likeliest last
    act of a turn that had a broken one. Without this the turn ends reporting SQL the agent has
    already replaced, which is this ticket's defect facing the other way."""
    previews = _preview_with_catalog(tmp_path, {"usage": "SELECT bad"})
    previews.executor.failures["usage"] = "Object 'GONG' does not exist or not authorized"

    _rewrite_catalog(tmp_path, {"usage": "SELECT good"})
    previews.refresh()

    assert previews.failures() == {}


def test_an_unchanged_statement_keeps_what_it_failed_with(tmp_path: Path):
    """Only a statement that CHANGED is forgiven. A catalog rewritten around one broken query —
    a second query added beside it — must not launder the one nobody touched."""
    previews = _preview_with_catalog(tmp_path, {"usage": "SELECT bad"})
    previews.executor.failures["usage"] = "Object 'GONG' does not exist or not authorized"

    _rewrite_catalog(tmp_path, {"usage": "SELECT bad", "trend": "SELECT 1"})
    previews.refresh()

    assert "usage" in previews.failures()


# ---- and the preview stops sending a creator to a published app's log ---------------------------


def test_the_preview_does_not_send_a_creator_to_an_app_they_never_published(tmp_path: Path):
    """Seen live in the preview by someone who had published nothing: "whoever published this app
    can see the reason in the App's log". In a preview the reader IS the creator and there is no
    App and no log — so the sentence has to depend on which context is serving."""
    serve = serve_module(TEMPLATE)
    source = serve.Source("ds-dwh", "DWH", "DWH", "MARTS", "SnowflakeConfig")

    class _Closed:
        def get_datasource(self, name):
            raise RuntimeError("Object 'GONG' does not exist or not authorized")

    executor = serve.FlightExecutor({"ds-dwh": source}, 100, preview=True)
    executor._client = _Closed()
    with pytest.raises(serve.QueryProblem) as caught:
        executor(serve.Query("usage", "ds-dwh", "SELECT 1"), {})

    assert "publish" not in str(caught.value).lower()


def test_the_published_app_keeps_its_own_words(tmp_path: Path):
    """The published app's reader is a viewer who cannot fix anything and whose one route to a
    reason is the publisher. That sentence is right there and is not what changed."""
    serve = serve_module(TEMPLATE)
    source = serve.Source("ds-dwh", "DWH", "DWH", "MARTS", "SnowflakeConfig")

    class _Closed:
        def get_datasource(self, name):
            raise RuntimeError("nope")

    executor = serve.FlightExecutor({"ds-dwh": source}, 100)
    executor._client = _Closed()
    with pytest.raises(serve.QueryProblem) as caught:
        executor(serve.Query("usage", "ds-dwh", "SELECT 1"), {})

    assert "Whoever published it" in str(caught.value)
