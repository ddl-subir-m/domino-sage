"""A Built App seeded before #119 keeps the helper names it was born with.

The template's `src/sage*.ts` helpers were de-branded because the Built App repo is a surface a
partner's own customer reads, and those names are prose that cannot be per-pack (ADR-0014). New apps
get `appLlm.ts`, `appQuery.ts` and the rest. **Existing apps get nothing done to them.** Migrating
them was rejected: the imports live in code the agent wrote, in files Sage does not own, and
rewriting a user's source to fix a cosmetic name is the worst trade available.

So every one of these tests is about an app that already has the old names, and what they assert is
that Sage goes on writing to those files rather than beside them. The failure this guards against is
silent in the worst way: Sage would write a correct `src/appLlm.config.ts` into an app whose code
imports `./sageLlm.config`, the pinned Alias would land in a file nothing reads, and the app would
go on calling whatever it called before.

The rename lives in `app_helpers`, which is the one place that answers "what is this app's helper
called". A test here that had to work the scheme out for itself would be evidence the resolver had
already failed.
"""
from __future__ import annotations

from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.app_helpers import LEGACY, TEMPLATE, helpers_for
from sage.resources.model_api_credentials import Credential, CredentialStore
from sage.resources.provider import FakeResourceProvider, LlmAlias, ModelApi
from sage.router.models import ModelCatalog

BASE = "https://apps.example.com/apps/llm_gateway/v1"
MODEL_ID = "6a8727f40ff0450030085fb3"
TOKEN = "SsQBZCygwPP79P8Q57qLPrGIfj67YAFBm3nrTT6Sm7vuPhBPBJvAL7lHm6jp36qB"
URL = f"https://example.domino.tech:443/models/{MODEL_ID}/latest/model"

ALIASES = [LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0})]
MODEL_APIS = [ModelApi(MODEL_ID, "churn-risk", "churn-risk", None, None)]
CATALOG = ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                       plan="p", implement="i", ask="a")
REPO_TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"


# ---- the resolver ---------------------------------------------------------------------------------


def test_an_app_holding_the_old_files_is_read_as_the_old_scheme(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sageBase.ts").write_text("// seeded in 2026\n")
    assert helpers_for(tmp_path) is LEGACY
    assert helpers_for(tmp_path).llm_config_path == "src/sageLlm.config.ts"


def test_an_app_with_no_helper_at_all_gets_the_neutral_names(tmp_path: Path):
    # A project seeded before ANY helper existed (pre-#7) has none of these files, so nothing in it
    # imports the old names and there is nothing to keep working. It joins the template's scheme the
    # first time Sage writes a helper into it.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("placeholder")
    assert helpers_for(tmp_path) is TEMPLATE


def test_a_freshly_seeded_app_gets_the_neutral_names(tmp_path: Path):
    # The shipped template, not a stub: the point is that what Sage seeds and what Sage resolves
    # agree, and a stub template could agree with a resolver that was wrong about both.
    assert helpers_for(REPO_TEMPLATE) is TEMPLATE
    for rel in TEMPLATE.paths:
        assert (REPO_TEMPLATE / rel).is_file(), rel
    for rel in LEGACY.paths:
        assert not (REPO_TEMPLATE / rel).exists(), rel


# ---- what Sage writes into an app that has the old names -------------------------------------------


def _template(tmp: Path) -> Path:
    """The CURRENT template — neutral names, the shipped helpers verbatim.

    Verbatim rather than stubbed: what these tests turn on is the text of the files, and a stub
    could agree with a substitution that was wrong about the real ones.
    """
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Template rules\n")
    (t / "src" / "App.tsx").write_text("placeholder")
    for rel in TEMPLATE.paths:
        (t / rel).write_text((REPO_TEMPLATE / rel).read_text())
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    """An Orchestrator over an app seeded from the OLD template and a container carrying the new one.

    That pair is the only state this ticket has to survive, and it is the one nothing else covers:
    the image always holds the current template, and the app on the volume was seeded whenever it
    was seeded. So the app is aged after seeding rather than seeded from an old template — renamed
    and localized, which is exactly the app a person has on disk today.
    """
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES), model_apis=list(MODEL_APIS)),
        browser_gateway_base=BASE,
        cost_project_label="my-app",
    )
    app = orch.project(start_preview=False).workspace.path
    for rel in TEMPLATE.paths:
        old = app / rel
        (app / LEGACY.localize(rel)).write_text(LEGACY.localize(old.read_text()))
        old.unlink()
    orch._project = None                      # the next call re-attaches, as a restart would
    orch.project(start_preview=False)
    return orch


def _bind_model_api(orch: Orchestrator) -> None:
    """Bind one, without the paste-and-verify round trip that is another ticket's subject."""
    CredentialStore(orch.project().workspace.path).put(MODEL_ID, Credential(URL, TOKEN))
    orch.bind_model_api(MODEL_ID)


def test_the_pinned_alias_lands_in_the_file_the_app_already_has(tmp_path: Path):
    """The write that would be silently lost: a config beside the one the app's code imports."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    app = orch.project().workspace.path

    assert not (app / TEMPLATE.llm_config_path).exists()
    config = (app / LEGACY.llm_config_path).read_text()
    assert 'alias: "sonnet"' in config
    # The export is the file's own name repeated, so the helper that imports it has to agree.
    assert "export const sageLlmConfig" in config
    assert "See ./sageLlm.ts" in config


def test_the_pinned_model_api_lands_in_the_file_the_app_already_has(tmp_path: Path):
    orch = _orch(tmp_path)
    _bind_model_api(orch)
    app = orch.project().workspace.path

    assert not (app / TEMPLATE.model_api_config_path).exists()
    config = (app / LEGACY.model_api_config_path).read_text()
    assert TOKEN in config
    assert "export const sageModelApiConfig" in config


def test_the_agent_is_told_the_name_the_app_actually_has(tmp_path: Path):
    """Generated instruction text, which is the half of this that has no compiler behind it.

    An import line naming a file that is not there is a build error the agent can see. A prose
    paragraph naming the wrong file is just a wrong instruction, and it survives every check.
    """
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    agents = (orch.project().workspace.path / "AGENTS.md").read_text()

    assert 'from "./sageLlm"' in agents
    assert "src/sageLlm.config.ts" in agents
    assert "appLlm" not in agents


def test_no_source_of_the_apps_is_renamed_or_left_behind(tmp_path: Path):
    """The criterion the whole ticket turns on: no existing Built App's source is rewritten."""
    orch = _orch(tmp_path)
    app = orch.project().workspace.path
    before = sorted(p.name for p in (app / "src").iterdir())

    orch.bind_llm_alias("id-sonnet")
    orch._project = None                      # the next call re-attaches, as a restart would
    orch.project(start_preview=False)

    assert sorted(p.name for p in (app / "src").iterdir()) == before
    assert not any(n.startswith("app") and n != "App.tsx" for n in before)


def test_a_refreshed_helper_still_imports_the_sibling_this_app_has(tmp_path: Path):
    """Attach refreshes Sage's own sources (#40), and the template's now import neutral names.

    Copied in verbatim, `appQuery.ts`'s `import … from "./appBase"` would point at a file this app
    does not have and the app would stop building — Sage breaking a working app to deliver a
    cosmetic rename. So the text is localized on the way in, the same substitution as the path.
    """
    orch = _orch(tmp_path)
    app = orch.project().workspace.path
    (app / LEGACY.query_path).write_text("// an older Sage wrote this\n")

    orch._project = None
    orch.project(start_preview=False)

    refreshed = (app / LEGACY.query_path).read_text()
    assert 'from "./sageBase"' in refreshed
    assert "appBase" not in refreshed
    assert refreshed == LEGACY.localize((REPO_TEMPLATE / TEMPLATE.query_path).read_text())
