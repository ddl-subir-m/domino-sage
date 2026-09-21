"""A non-UTF-8 generated file is repaired, not refused (#341).

`_write_generated` read the file it was about to (maybe) rewrite with a bare `read_text()`, no
`try` at all, purely to decide whether its own new content differs from what is already on disk —
an optimisation that keeps a rewrite with identical bytes from showing up as a dirty file in the
turn's tree comparison and in the person's git history. `UnicodeDecodeError` is a `ValueError`, not
an `OSError`, so a generated file bombed with non-UTF-8 bytes escaped that comparison and took the
whole call down with it. `_write_app_model`, `_write_app_model_api`, `share_sample_rows` and
`clear_sample_rows` all reach it, so picking a perfectly valid Binding could brick the change on a
file the person never chose to edit.

ADR-0051 rule five decides the shape, and it is the opposite of #325's `AGENTS.md` guard: that file
is the person's to hand-edit, so the guard there leaves unreadable bytes alone. `src/appLlm.config.ts`
and `.sage/samples.json` are files Sage generated from state it already holds in full — the Binding
list, the sampled rows — so an unreadable copy on disk is repaired by overwriting it with the freshly
rendered text, never a reason to refuse the act that produced that text.

Two conditions, two plants: `_write_app_model` (through a Binding change) and `share_sample_rows`.
`clear_sample_rows` is not a third — its `kept` list comes from `_shared`, which reads the very same
`SAMPLES_PATH` through the already-guarded `_read_json` (#341's own file, but a different reader), so
when that file is unreadable `kept` is always `[]` and the call takes the `unlink` branch, never
`_write_generated`'s write. It is covered below anyway, to record that it survives rather than to
plant a second condition it cannot reach.

The bytes here are real, not a mocked exception, for the reason #303's file gives: a mock proves the
handler runs, not that `read_text()` raises what the handler is written for.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.app_helpers import TEMPLATE
from sage.resources.bindings import Binding
from sage.resources.bound_schema import SAMPLES_PATH, parse_samples
from sage.resources.pinned_model import render_config
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog

CONFIG_PATH = TEMPLATE.llm_config_path
HELPER_PATH = TEMPLATE.llm_path

# UTF-16 bytes, same as #303's and #325's files and for the same reason: it opens with `\xff\xfe`,
# and `\xff` is not a legal UTF-8 start byte in any position. This is what a `git merge` gone binary,
# or an editor that guessed the wrong encoding, leaves behind — and both files bombed below are
# committed to the person's own app repo.
NOT_UTF8 = "// stub".encode("utf-16")

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-mimo", "mimo-v2.5", "MiMo 2.5", None, ["chat"], {}),
]
CATALOG = ModelCatalog(
    sovereign_plan="sonnet", sovereign_implement="sonnet", sovereign_ask="sonnet",
    plan="sonnet", implement="sonnet", ask="sonnet",
)
GATEWAY_BASE = "https://apps.example.com/apps/llm_gateway/v1"


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


# ---- plant one: _write_app_model, reached from a Binding change ---------------------------------


def _orch(tmp_path: Path) -> Orchestrator:
    t = tmp_path / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / HELPER_PATH).write_text("// stub helper\n")
    (t / CONFIG_PATH).write_text(render_config([], None, None))
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Template rules\n")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=t,
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES)),
        browser_gateway_base=GATEWAY_BASE,
    )
    orch.project(start_preview=False)
    return orch


def test_a_non_utf8_llm_config_does_not_brick_a_binding_change(tmp_path: Path):
    """The measured reproduction from the ticket: bomb the generated file, then change a Binding."""
    _needs_utf8_locale(tmp_path)
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    config = orch.project().workspace.path / CONFIG_PATH
    config.write_bytes(NOT_UTF8)

    # Neither call may raise. Before the fix, `UnicodeDecodeError` escaped both.
    orch.unbind("llm_alias", "id-sonnet")
    orch.bind_llm_alias("id-mimo")

    body = config.read_text()
    assert "mimo-v2.5" in body
    assert "sonnet" not in body


def test_the_repaired_config_is_exactly_what_would_have_been_written(tmp_path: Path):
    """Repair means overwrite with the freshly rendered text, not a best-effort patch of the old
    bytes — there is nothing in the old bytes worth keeping; Sage re-derives all of it from the
    Binding list on every call."""
    _needs_utf8_locale(tmp_path)
    orch = _orch(tmp_path)
    config = orch.project().workspace.path / CONFIG_PATH
    config.write_bytes(NOT_UTF8)

    orch.bind_llm_alias("id-mimo")

    expected = render_config(
        [Binding("llm_alias", "id-mimo", "mimo-v2.5", "MiMo 2.5")], GATEWAY_BASE, "Sage")
    assert config.read_text() == expected


# ---- plant two: share_sample_rows, reached from the sample-rows picker --------------------------


def _orch_with_data_source(tmp_path: Path) -> Orchestrator:
    """A real workspace, seeded with the real `serve.py` — the same reason `test_bound_schema.py`'s
    `orchestrator()` fixture copies it: `bind_data_source` and the samples writers it feeds are
    exercised end to end, not through a stub."""
    react_template = Path(__file__).resolve().parents[2] / "template" / "react-vite"
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    shutil.copy2(react_template / "serve.py", template / "serve.py")
    shutil.copy2(react_template / "sage_queries.py", template / "sage_queries.py")
    shutil.copy2(react_template / "src" / "appQuery.ts", template / "src" / "appQuery.ts")
    shutil.copy2(react_template / "src" / "appBase.ts", template / "src" / "appBase.ts")

    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=template,
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    return orch


def test_a_non_utf8_samples_file_does_not_brick_sharing_rows(tmp_path: Path):
    """`kept` here comes from `_shared`, and `fresh` from the resource provider directly — neither
    reads the bombed file — so `share_sample_rows` reaches `_write_generated` with real content to
    write and a target it cannot read back. Before the fix, that raised."""
    _needs_utf8_locale(tmp_path)
    orch = _orch_with_data_source(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    samples = orch.project().workspace.path / SAMPLES_PATH
    samples.parent.mkdir(parents=True, exist_ok=True)
    samples.write_bytes(NOT_UTF8)

    result = orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"])

    assert result["shared"] == ["FCT_USAGE_DAILY"]
    shared = parse_samples(json.loads(samples.read_text()))
    assert [s.rows.table for s in shared] == ["FCT_USAGE_DAILY"]


def test_a_non_utf8_samples_file_does_not_brick_clearing_rows(tmp_path: Path):
    """Coverage, not a second plant (see the module docstring): `clear_sample_rows` takes the
    `unlink` branch here, because `_shared` already reads the bombed file as no rows at all — it
    cannot hand back a `kept` list that would route through `_write_generated`."""
    _needs_utf8_locale(tmp_path)
    orch = _orch_with_data_source(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"])
    samples = orch.project().workspace.path / SAMPLES_PATH
    samples.write_bytes(NOT_UTF8)

    result = orch.clear_sample_rows("ds-dwh")

    assert result == {"shared": [], "rows": 0}
    assert not samples.exists()
