import copy

import pytest

from sage.gateway.capabilities import evidence, resolve
from sage.gateway.protocol import Protocol
from sage.resources.provider import join_aliases


def record(name="Opus-4.8"):
    return copy.deepcopy(next(row for row in evidence() if row["name"] == name))


def test_discovery_preserves_exact_route_identity_and_uses_the_verified_choices():
    proof = record()
    aliases = join_aliases({proof["name"]}, [proof], gateway_root=proof["gateway"] + "/v1/")
    alias = aliases[0]
    capability = resolve(proof["gateway"], proof, evidence())
    assert alias.provider_id == proof["provider_id"]
    assert alias.provider_model == "claude-opus-4-8"
    assert alias.route_capability == capability
    assert alias.reasoning_efforts == list(capability.efforts)
    assert alias.reasoning_efforts_with_tools == list(capability.efforts_with_tools)
    assert capability.protocol is Protocol.MESSAGES and capability.native


@pytest.mark.parametrize("field,value", [("id", "new-id"), ("name", "opus-4.8"),
    ("provider_id", "new-provider"), ("provider_type", "openai"),
    ("provider_model", "different-model"), ("updated_at", "tomorrow")])
def test_repoint_or_metadata_refresh_invalidates_old_native_evidence(field, value):
    row = record()
    row[field] = value
    capability = resolve(row["gateway"], row, evidence())
    assert not capability.native and not capability.efforts
    assert capability.reason


def test_gateway_and_fallback_are_part_of_the_capability_boundary():
    row = record("gpt-5.4")
    assert not resolve("https://other-gateway.example", row, evidence()).native
    row["fallback_chain"] = ["other-alias"]
    capability = resolve(row["gateway"], row, evidence())
    assert not capability.native and not capability.efforts
    assert "fallback" in capability.reason


def test_unknown_and_metadata_poor_aliases_stay_available_without_invented_choices():
    rows = join_aliases({"Exact/ALIAS", "thin"}, [{"name": "Exact/ALIAS", "display_name": "GPT Claude GLM",
                        "inference_params": {"reasoning_effort": "high"}}], gateway_root="https://gateway.example")
    assert {row.name for row in rows} == {"Exact/ALIAS", "thin"}
    assert all(not row.reasoning_efforts_with_tools for row in rows)
    assert all(row.route_capability.protocol is Protocol.CHAT for row in rows)


def test_glm_and_gemini_keep_their_measured_compatibility_settings():
    for name, expected in (("GLM 5.3 OR", ("low", "high", "max")),
                           ("domino/gemini-3.7-flash", ("low", "medium", "high", "max"))):
        row = record(name)
        cap = resolve(row["gateway"], row, evidence())
        assert cap.protocol is Protocol.CHAT and not cap.native
        assert cap.efforts_with_tools == expected
        assert cap.settings(None, tools=True) == {}
        assert cap.settings(expected[0], tools=True) == {"reasoning_effort": expected[0]}


def test_model_default_off_and_adaptive_effort_are_distinct():
    row = record()
    cap = resolve(row["gateway"], row, evidence())
    assert cap.settings(None, tools=True) == {}
    assert cap.settings("none", tools=True) == {"thinking": {"type": "disabled"}}
    assert cap.settings("high", tools=True) == {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}
    with pytest.raises(ValueError, match="unavailable"):
        cap.settings("invented", tools=True)


def test_metadata_refresh_rechecks_the_route_used_for_validation_and_dispatch(monkeypatch):
    from sage.resources.provider import DominoResourceProvider
    row = record()
    provider = DominoResourceProvider(row['gateway'], lambda: 'unused')
    now = [10.0]
    monkeypatch.setattr('sage.resources.provider.time.monotonic', lambda: now[0])
    monkeypatch.setattr(provider, '_get', lambda path: {'data': [{'id': row['name']}]} if path == '/v1/models' else [row])
    first = provider.reasoning_capability(row['name'])
    assert first.native and 'high' in first.efforts_with_tools
    row['provider_model'] = 'repointed'
    now[0] += 6
    current = provider.reasoning_capability(row['name'])
    assert not current.native and not current.efforts_with_tools
    assert provider.list_llm_aliases()[0].route_capability == current
