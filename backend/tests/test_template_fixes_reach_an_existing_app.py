"""A fix to a Sage-owned template file reaches the apps that already exist (#40).

`WorkspaceManager.ensure()` seeds the template only when `package.json` is absent, so an app keeps
whatever copies of Sage's own files it was born with. Every later fix to them ships in the image,
rebuilds cleanly, passes its tests — and is invisible in every project that already exists.
`vite.config.ts` and `src/sageLlm.ts` were each pulled out of that trap one at a time, after each had
already cost a debugging session. These are the four that were still in it.

The trap is silent by construction, so a test that only reads the template proves nothing: it passes
against the bug. Each test here reaches the state that actually failed — a workspace holding an OLD
copy — and asserts the template's copy is what the app ends up with.

The other half is the rule, which had to land before the enforcement did: a refresh overwrites, and
that is only safe for a file the agent was told to leave alone. `AGENTS.md` said nothing about
`ErrorBoundary.tsx` or `reportRuntimeError.ts`, and said nothing about the other two unless the
matching Binding happened to exist. `test_agents_md_forbids_editing_every_file_that_gets_refreshed`
is the one that keeps those two facts attached to each other.
"""
from __future__ import annotations

from pathlib import Path

from sage.workspace.manager import _OWNED_SOURCES, WorkspaceManager

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"


def _template(tmp: Path) -> Path:
    """A stub template carrying a copy of every Sage-owned source, as the real one does."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "vite.config.ts").write_text("// template v2\n")
    for rel in _OWNED_SOURCES:
        (t / rel).write_text(f"// template {Path(rel).name} v2\n")
    return t


def _orch(tmp: Path):
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    catalog = ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                           plan="p", implement="i", ask="a")
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp), gateway=object(),
                        catalog=catalog, project_id="Sage")


def test_attach_brings_every_sage_owned_source_back_in_line(tmp_path: Path):
    """The one that fails against the bug, for all four at once.

    Attach rather than publish, for the reason `refresh_preview_config` gives: what these files do
    is hold the preview together — the crash card, the runtime-error report the autofix loop reads,
    the two Resource helpers the app calls. By publish time the damage they prevent is already done.
    """
    orch = _orch(tmp_path)
    app = orch.project(start_preview=False).workspace.path
    for rel in _OWNED_SOURCES:
        (app / rel).write_text("// an older Sage wrote this\n")

    orch._project = None                      # the next call re-attaches, as a restart would
    orch.project(start_preview=False)

    for rel in _OWNED_SOURCES:
        assert (app / rel).read_text() == f"// template {Path(rel).name} v2\n", rel


def test_an_unchanged_source_is_not_rewritten(tmp_path: Path):
    # These are committed to the app's repo, so an identical rewrite would still show up as a dirty
    # file in the turn's tree comparison and in the creator's git history, for no change at all.
    orch = _orch(tmp_path)
    app = orch.project(start_preview=False).workspace.path
    before = {rel: (app / rel).stat().st_mtime_ns for rel in _OWNED_SOURCES}

    orch._project = None
    orch.project(start_preview=False)

    assert {rel: (app / rel).stat().st_mtime_ns for rel in _OWNED_SOURCES} == before


def test_attach_never_puts_in_a_helper_the_app_did_not_have(tmp_path: Path):
    """Refresh, not restore — the one thing attach must not do.

    `src/sageModelApi.ts` imports `./sageModelApi.config`, and `src/sageQuery.ts` imports
    `./sageBase`. Both siblings are written by the Binding path, which knows whether the app has a
    Model API or a Data Source; attach does not. So an app seeded before either helper existed would
    get a module importing a file that isn't there, and stop compiling — Sage breaking an app that
    was working, to deliver a helper it never asked for. Putting them there stays with
    `ensure_model_api_helper` / `ensure_query_helper`, which write the config in the same breath.
    """
    orch = _orch(tmp_path)
    app = orch.project(start_preview=False).workspace.path
    for rel in _OWNED_SOURCES:
        (app / rel).unlink()

    orch._project = None
    orch.project(start_preview=False)

    for rel in _OWNED_SOURCES:
        assert not (app / rel).exists(), rel


def test_the_binding_writers_replace_a_stale_helper_rather_than_keep_it(tmp_path: Path):
    """The other door onto the same two files, and it was missing-only too.

    Bind is where a helper first lands, so it is also where a stale one is most easily left standing:
    the file exists, so the old missing-only check returned without looking at what was in it.
    """
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_template(tmp_path))
    ws = mgr.ensure("proj1")
    (ws.path / "src" / "sageModelApi.ts").write_text("// an older Sage wrote this\n")
    (ws.path / "src" / "sageQuery.ts").write_text("// an older Sage wrote this\n")

    assert mgr.ensure_model_api_helper() is True
    assert mgr.ensure_query_helper() is True
    assert (ws.path / "src" / "sageModelApi.ts").read_text() == "// template sageModelApi.ts v2\n"
    assert (ws.path / "src" / "sageQuery.ts").read_text() == "// template sageQuery.ts v2\n"
    assert mgr.ensure_model_api_helper() is False   # already current — nothing to commit
    assert mgr.ensure_query_helper() is False


def test_agents_md_forbids_editing_every_file_that_gets_refreshed():
    """The rule has to exist before the enforcement does, and unconditionally.

    A refresh overwrites on every attach, which is safe for a file nobody else may edit and
    destructive for one somebody might. The two Resource helpers were already named in AGENTS.md —
    but only in a block SPLICED IN when the matching Binding exists, while all four are on disk from
    seed time regardless. So in a project with no Model API bound, `sageModelApi.ts` sat there with
    the agent never having been told to leave it alone. This asserts the STATIC file, which is the
    only copy every project is guaranteed to have.
    """
    agents = (TEMPLATE / "AGENTS.md").read_text()
    for rel in _OWNED_SOURCES:
        assert f"`{Path(rel).as_posix()}`" in agents, f"{rel} is refreshed but never forbidden"


def test_the_reporter_is_refreshed_before_the_boundary_that_imports_it():
    # Ordered for the reason _DEPLOY_FILES is: ErrorBoundary.tsx imports reportRuntimeError.ts, so
    # a refresh that dies partway must leave the older boundary against the newer reporter, never a
    # newer boundary importing exports the older reporter does not have.
    order = [Path(rel).name for rel in _OWNED_SOURCES]
    assert order.index("reportRuntimeError.ts") < order.index("ErrorBoundary.tsx")
    assert 'from "./reportRuntimeError"' in (TEMPLATE / "src" / "ErrorBoundary.tsx").read_text()
