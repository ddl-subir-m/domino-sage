"""The one LLM Alias a Built App calls, pinned into the app's own source (#7).

Two things can go wrong here and neither shows up as an exception. The app can ship pointing at a
model nobody chose — or at a model somebody un-chose — because the file on disk drifted from the
Binding record. And the file can be *written* correctly but be unbuildable, because the config
landed without the helper that imports it. So the tests are about what ends up on disk after a
Binding change, and about the two orderings that decide whether the result compiles.

The rendering is pure and tested directly. The writing runs through a real Orchestrator over a real
workspace, because the manifest is a thing under test rather than something to fake.
"""
from __future__ import annotations

from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import Binding
from sage.resources.pinned_model import CONFIG_PATH, HELPER_PATH, agents_block, pinned_alias, render_config
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog

BASE = "https://apps.example.com/apps/llm_gateway/v1"

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {}),
]

CATALOG = ModelCatalog(
    sovereign_plan="qwen-2-5", sovereign_implement="qwen-2-5", sovereign_ask="qwen-2-5",
    plan="sonnet", implement="sonnet", ask="sonnet",
)

REPO_TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"


def _binding(rid: str, name: str, display: str = "", kind: str = "llm_alias") -> Binding:
    return Binding(kind, rid, name, display or name)


def _template(tmp: Path) -> Path:
    """A template carrying the helper, like the shipped one. Stub contents, so a test can tell a
    copy from a coincidence."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / HELPER_PATH).write_text("// stub helper\n")
    (t / CONFIG_PATH).write_text(render_config([], None, None))
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Template rules\n")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES)),
        browser_gateway_base=BASE,
        cost_project_label="my-app",
    )
    orch.project(start_preview=False)  # nothing under test needs the dev server
    return orch


def _config(orch: Orchestrator) -> str:
    return (orch.project().workspace.path / CONFIG_PATH).read_text()


def _agents(orch: Orchestrator) -> str:
    return (orch.project().workspace.path / "AGENTS.md").read_text()


# ---- which Alias gets pinned ---------------------------------------------------------------------


def test_nothing_is_pinned_when_the_app_depends_on_nothing():
    assert pinned_alias([]) is None


def test_the_first_alias_in_the_manifest_is_the_one_pinned():
    # First, not last: manifest order is the order they were chosen, so anchoring on the first means
    # adding a second Binding cannot silently change what an already-built app answers with.
    aliases = [_binding("id-sonnet", "sonnet"), _binding("id-qwen", "qwen-2-5")]
    assert pinned_alias(aliases).name == "sonnet"


def test_a_binding_of_another_kind_is_skipped_rather_than_pinned():
    # Bindings are a mixed list of recorded dependencies (#6). Only an LLM Alias can be called.
    entries = [_binding("ds-1", "sales_db", kind="data_source"), _binding("id-qwen", "qwen-2-5")]
    assert pinned_alias(entries).name == "qwen-2-5"


# ---- the generated config ------------------------------------------------------------------------


def test_the_config_pins_the_alias_name_not_its_id():
    # `name` is what a request's `model` field carries. Pinning the gateway's internal id would
    # compile fine and 404 on the viewer's first question.
    text = render_config([_binding("id-sonnet", "sonnet", "Claude Sonnet 4.6")], BASE, "my-app")
    assert 'alias: "sonnet"' in text
    assert 'displayName: "Claude Sonnet 4.6"' in text
    assert f'base: "{BASE}"' in text
    assert 'project: "my-app"' in text


def test_no_alias_nulls_the_base_and_the_project_too():
    # A base with no alias would let the helper build a URL for a model that was never chosen.
    text = render_config([], BASE, "my-app")
    for field in ("alias", "displayName", "base", "project"):
        assert f"{field}: null" in text
    assert BASE not in text and "my-app" not in text


def test_an_alias_named_with_a_quote_cannot_end_the_literal_early():
    # These strings come from a gateway's registration records, not from us.
    alias = _binding('weird"id', 'say "hi"', "back\\slash")
    text = render_config([alias], BASE, "my-app")
    assert r'alias: "say \"hi\""' in text
    assert r'displayName: "back\\slash"' in text


def test_the_no_model_config_is_byte_identical_to_the_one_shipped_in_the_template():
    # The template's copy is what a fresh app is born with, and this function is what every later
    # write produces. If they differ, seeding a project and then binding-and-unbinding an Alias
    # leaves a diff in the user's repo that nobody asked for.
    assert render_config([], None, None) == (REPO_TEMPLATE / CONFIG_PATH).read_text()


# ---- what the agent is told -----------------------------------------------------------------------


def test_the_agent_is_told_nothing_when_no_model_is_pinned():
    # An agent told about a model that is not there writes a call that cannot run.
    assert agents_block([], []) == ""


def test_the_agent_is_given_the_import_the_display_name_and_the_load_check():
    block = agents_block([_binding("id-sonnet", "sonnet", "Claude Sonnet 4.6")], [])
    assert "Claude Sonnet 4.6" in block
    assert 'from "./sageLlm"' in block
    assert "checkModel" in block          # the check whose absence only breaks for OTHER people
    assert "src/sageLlm.config.ts" in block  # ... and the two files it must not rewrite
    assert "src/sageLlm.ts" in block


def test_the_agent_is_told_what_a_raw_gateway_call_costs_not_just_that_it_is_forbidden():
    # AGENTS.md forbids EDITING the helper, never going around it, and the line about "no key to
    # add, no server to write, no CORS to configure" reads a little like an invitation (#94). Every
    # rule in this block states a consequence, because a bare prohibition is the one an agent that
    # can see its own `fetch` working talks itself out of.
    block = agents_block([_binding("id-sonnet", "sonnet", "Claude Sonnet 4.6")], [])
    assert "askModel" in block
    assert "X-LLM-Tag-sage-*" in block   # how this app's spend is attributable to Sage at all
    assert "for the viewer" in block     # the error messages a raw call replaces with nothing
    assert "expired" in block            # session expiry, which a raw call cannot tell from a 500
    assert "streaming" in block


def test_the_agent_is_told_the_preview_proxy_is_not_there_once_the_app_ships():
    # The one variant no test the agent can run will catch: `/api/llm` is Sage's own proxy, answers
    # correctly through the whole build, and is gone the moment the app is published.
    block = agents_block([_binding("id-sonnet", "sonnet", "Claude Sonnet 4.6")], [])
    assert "/api/llm" in block
    assert "published" in block


# ---- through the Orchestrator, which is what a Binding change calls ------------------------------


def test_a_fresh_project_starts_with_no_model_pinned(tmp_path):
    orch = _orch(tmp_path)
    assert _config(orch) == render_config([], None, None)
    assert "sage:app-model" not in _agents(orch)


def test_binding_an_alias_writes_it_into_the_apps_own_source(tmp_path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    text = _config(orch)
    assert 'alias: "sonnet"' in text and f'base: "{BASE}"' in text
    assert "sage:app-model:begin" in _agents(orch)
    assert "Claude Sonnet 4.6" in _agents(orch)


def test_unbinding_puts_the_app_back_to_having_no_model(tmp_path):
    # Both halves matter: a config still naming the Alias would keep calling it, and an AGENTS.md
    # still describing it would keep the agent writing calls to it.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.unbind("llm_alias", "id-sonnet")
    assert _config(orch) == render_config([], None, None)
    assert "sage:app-model" not in _agents(orch)


def test_the_agents_block_is_replaced_not_repeated(tmp_path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.unbind("llm_alias", "id-sonnet")
    orch.bind_llm_alias("id-qwen")
    assert _agents(orch).count("sage:app-model:begin") == 1
    assert "Claude Sonnet 4.6" not in _agents(orch)


def test_a_second_alias_becomes_callable_without_moving_the_default(tmp_path):
    """Both halves of #34. The app gains the second model — the whole point of binding it — while
    every call already written, which names no model, goes on reaching the one it always did."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.bind_llm_alias("id-qwen")
    text = _config(orch)
    assert 'alias: "sonnet"' in text                       # the default did not move
    assert '{ alias: "sonnet", displayName: "Claude Sonnet 4.6" }' in text
    assert '{ alias: "qwen-2-5", displayName: "Qwen 2.5 (Domino-hosted)" }' in text
    block = _agents(orch)
    assert 'alias: "qwen-2-5"' in block                    # the exact string a call takes
    assert "the default, used by any call that names no model" in block


def test_one_alias_is_described_without_the_selector(tmp_path):
    # An app with one model gains nothing from a paragraph about choosing between models, and pays
    # for it on every turn — this block is re-read whole each time.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    block = _agents(orch)
    assert "## The app's language model\n" in block
    assert "Use only the Alias names listed above" not in block


def test_re_binding_the_alias_already_in_use_is_a_no_op(tmp_path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.bind_llm_alias("id-qwen")
    orch.bind_llm_alias("id-sonnet")   # the one already pinned, recorded again
    assert 'alias: "sonnet"' in _config(orch)


def test_removing_the_pinned_alias_promotes_the_next_one(tmp_path):
    # Removal is how a creator switches model, so the row below has to take over.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.bind_llm_alias("id-qwen")
    orch.unbind("llm_alias", "id-sonnet")
    assert 'alias: "qwen-2-5"' in _config(orch)


def test_an_unchanged_config_is_not_rewritten(tmp_path):
    # This file is committed to the user's repo. A rewrite with identical content still shows up as
    # a dirty file in the turn's tree comparison and in their git history.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    config = orch.project().workspace.path / CONFIG_PATH
    before = config.stat().st_mtime_ns
    orch.bind_llm_alias("id-sonnet")
    assert config.stat().st_mtime_ns == before


def test_a_project_seeded_before_this_feature_gets_the_helper_it_lacks(tmp_path):
    # The config imports the helper, so writing one into a pre-#7 repo without the other leaves an
    # app that cannot build at all — a worse outcome than the missing feature.
    orch = _orch(tmp_path)
    helper = orch.project().workspace.path / HELPER_PATH
    helper.unlink()
    orch.bind_llm_alias("id-sonnet")
    assert helper.read_text() == "// stub helper\n"


def test_a_stale_helper_is_replaced_with_the_template_s(tmp_path):
    """This was missing-only, and the reasoning has been reversed on purpose.

    The old rule was that replacing a copy the app's code imports is riskier than leaving a stale
    one. What it actually bought was a fix to this file reaching new projects and never existing
    ones: an app that could not call its model in the preview (#7) stayed broken for the whole
    session that reported it, and the only ways out were Reset app — which throws the app away — or
    starting a new project.

    The risk it was guarding against is bounded by what the file is. Sage owns it, AGENTS.md forbids
    the agent to edit or re-create it, and the surface apps import (`askModel`, `checkModel`, the
    types) is stable; only the internals move. So it is refreshed, and an edit to it does not
    survive — which is what "Sage owns this file" has to mean to be worth writing down.
    """
    orch = _orch(tmp_path)
    helper = orch.project().workspace.path / HELPER_PATH
    helper.write_text("// edited by someone\n")
    orch.bind_llm_alias("id-sonnet")
    assert helper.read_text() == "// stub helper\n"


def test_the_helper_is_refreshed_when_the_project_is_opened(tmp_path):
    # Not only when a Resource changes. Binding is the one thing a creator with a working app has no
    # reason to do, so a helper fix that waited for it would never arrive.
    orch = _orch(tmp_path)
    helper = orch.project().workspace.path / HELPER_PATH
    helper.write_text("// an older Sage wrote this\n")

    orch._project = None                      # the next call re-attaches, as a restart would
    orch.project(start_preview=False)

    assert helper.read_text() == "// stub helper\n"


def test_an_unchanged_helper_is_not_rewritten(tmp_path):
    # This file is committed to the app's repo. Rewriting identical content would still show up as a
    # dirty file in the turn's tree comparison and in git history, for no change at all.
    orch = _orch(tmp_path)
    helper = orch.project().workspace.path / HELPER_PATH
    before = helper.stat().st_mtime_ns

    orch.bind_llm_alias("id-sonnet")

    assert helper.stat().st_mtime_ns == before
