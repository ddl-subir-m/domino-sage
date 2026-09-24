from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from sage.build_policy import BuildPolicy, load_build_policy
from sage.orchestrator import service
from sage.orchestrator.service import Orchestrator, _RepeatBrake

EXPECTED = {
    "poll_failure_limit": 4,
    "quiet_timeout_seconds": 120.0,
    "open_tool_quiet_timeout_seconds": 600.0,
    "stop_grace_seconds": 30.0,
    "runtime_error_wait_seconds": 4.0,
    "poll_message_limit": 40,
    "live_read_limit": 25,
    "exact_repeat_limit": 3,
    "bash_call_limit": 40,
    "failed_write_limit": 10,
    "no_edit_nudge_limit": 3,
    "runtime_repair_limit": 3,
    "leak_repair_limit": 2,
    "gateway_repair_limit": 2,
    "phased_max_seconds": 1_800.0,
    "tool_result_max_bytes": 16_384,
    "tool_result_aggregate_max_bytes": 131_072,
    "tool_result_head_fraction": 0.75,
    "pre_edit_model_call_limit": 12,
    "pre_edit_request_non_media_max_bytes": 524_288,
    "pre_edit_original_tool_result_max_bytes": 196_608,
    "pre_edit_clean_recovery_limit": 1,
    "build_context_non_media_max_bytes": 786_432,
    "build_context_automatic_rollover_limit": 1,
    "build_context_continuation_reference_max_count": 100,
}

ENVIRONMENT = {
    "poll_failure_limit": "SAGE_BUILD_POLL_FAILURE_LIMIT",
    "quiet_timeout_seconds": "SAGE_BUILD_QUIET_TIMEOUT_SECONDS",
    "open_tool_quiet_timeout_seconds": "SAGE_BUILD_OPEN_TOOL_QUIET_TIMEOUT_SECONDS",
    "stop_grace_seconds": "SAGE_BUILD_STOP_GRACE_SECONDS",
    "runtime_error_wait_seconds": "SAGE_BUILD_RUNTIME_ERROR_WAIT_SECONDS",
    "poll_message_limit": "SAGE_BUILD_POLL_MESSAGE_LIMIT",
    "live_read_limit": "SAGE_BUILD_LIVE_READ_LIMIT",
    "exact_repeat_limit": "SAGE_BUILD_EXACT_REPEAT_LIMIT",
    "bash_call_limit": "SAGE_BUILD_BASH_CALL_LIMIT",
    "failed_write_limit": "SAGE_BUILD_FAILED_WRITE_LIMIT",
    "no_edit_nudge_limit": "SAGE_BUILD_NO_EDIT_NUDGE_LIMIT",
    "runtime_repair_limit": "SAGE_BUILD_RUNTIME_REPAIR_LIMIT",
    "leak_repair_limit": "SAGE_BUILD_LEAK_REPAIR_LIMIT",
    "gateway_repair_limit": "SAGE_BUILD_GATEWAY_REPAIR_LIMIT",
    "phased_max_seconds": "SAGE_BUILD_PHASED_MAX_SECONDS",
    "tool_result_max_bytes": "SAGE_BUILD_TOOL_RESULT_MAX_BYTES",
    "tool_result_aggregate_max_bytes": "SAGE_BUILD_TOOL_RESULT_AGGREGATE_MAX_BYTES",
    "tool_result_head_fraction": "SAGE_BUILD_TOOL_RESULT_HEAD_FRACTION",
    "pre_edit_model_call_limit": "SAGE_BUILD_PRE_EDIT_MODEL_CALL_LIMIT",
    "pre_edit_request_non_media_max_bytes": "SAGE_BUILD_PRE_EDIT_REQUEST_NON_MEDIA_MAX_BYTES",
    "pre_edit_original_tool_result_max_bytes":
        "SAGE_BUILD_PRE_EDIT_ORIGINAL_TOOL_RESULT_MAX_BYTES",
    "pre_edit_clean_recovery_limit": "SAGE_BUILD_PRE_EDIT_CLEAN_RECOVERY_LIMIT",
    "build_context_non_media_max_bytes": "SAGE_BUILD_CONTEXT_NON_MEDIA_MAX_BYTES",
    "build_context_automatic_rollover_limit":
        "SAGE_BUILD_CONTEXT_AUTOMATIC_ROLLOVER_LIMIT",
    "build_context_continuation_reference_max_count":
        "SAGE_BUILD_CONTEXT_CONTINUATION_REFERENCE_MAX_COUNT",
}


def test_defaults_are_the_production_build_limits_and_the_policy_is_immutable():
    policy = load_build_policy({})

    assert {field.name: getattr(policy, field.name) for field in fields(policy)} == EXPECTED
    with pytest.raises(FrozenInstanceError):
        policy.poll_failure_limit = 5  # type: ignore[misc]


@pytest.mark.parametrize(("field", "key"), ENVIRONMENT.items())
def test_each_new_environment_key_changes_exactly_one_field(field: str, key: str):
    before = load_build_policy({})
    raw = "0.5" if field == "tool_result_head_fraction" else "7"

    after = load_build_policy({key: raw})

    changed = {item.name for item in fields(before)
               if getattr(before, item.name) != getattr(after, item.name)}
    assert changed == {field}


@pytest.mark.parametrize("alias,field", [
    ("SAGE_MAX_NUDGES", "no_edit_nudge_limit"),
    ("SAGE_PHASED_MAX_SECONDS", "phased_max_seconds"),
])
def test_old_environment_names_remain_aliases(alias: str, field: str):
    assert getattr(load_build_policy({alias: "9"}), field) == 9


def test_new_name_wins_over_alias_and_warning_contains_keys_only(caplog):
    caplog.set_level(logging.WARNING, logger="sage.build_policy")

    policy = load_build_policy({"SAGE_BUILD_NO_EDIT_NUDGE_LIMIT": "5",
                                "SAGE_MAX_NUDGES": "87654321"})

    assert policy.no_edit_nudge_limit == 5
    warning = caplog.messages[-1]
    assert "SAGE_BUILD_NO_EDIT_NUDGE_LIMIT" in warning
    assert "SAGE_MAX_NUDGES" in warning
    assert "5" not in warning
    assert "87654321" not in warning


@pytest.mark.parametrize("raw", ["", "0", "-1", "1.5", "true", "NaN", "inf", "+1", " 1"])
def test_invalid_count_fails_and_names_only_the_setting(raw: str):
    key = "SAGE_BUILD_POLL_FAILURE_LIMIT"

    with pytest.raises(ValueError, match=f"^Invalid setting {key}$"):
        load_build_policy({key: raw})


@pytest.mark.parametrize("raw", [
    "", "0", "-1", "true", "NaN", "inf", "-inf", "+1", " 1", "1_0",
])
def test_invalid_duration_fails_and_names_only_the_setting(raw: str):
    key = "SAGE_BUILD_QUIET_TIMEOUT_SECONDS"

    with pytest.raises(ValueError, match=f"^Invalid setting {key}$"):
        load_build_policy({key: raw})


@pytest.mark.parametrize("raw", ["0", "1", "-0.1", "NaN", "inf", "true"])
def test_invalid_head_fraction_fails_and_names_only_the_setting(raw: str):
    key = "SAGE_BUILD_TOOL_RESULT_HEAD_FRACTION"

    with pytest.raises(ValueError, match=f"^Invalid setting {key}$"):
        load_build_policy({key: raw})


def test_each_service_keeps_its_injected_policy_without_global_leakage(tmp_path: Path):
    first_policy = replace(BuildPolicy(), exact_repeat_limit=2)
    second_policy = replace(BuildPolicy(), exact_repeat_limit=8)
    template = tmp_path / "template"
    first = Orchestrator(tmp_path / "first", template, object(), object(),
                         build_policy=first_policy)
    second = Orchestrator(tmp_path / "second", template, object(), object(),
                          build_policy=second_policy)

    assert first._build_policy is first_policy
    assert second._build_policy is second_policy
    assert _RepeatBrake().limit == 3  # Chat keeps its existing value.


def test_an_invalid_environment_value_fails_when_the_service_starts(tmp_path: Path,
                                                                    monkeypatch):
    monkeypatch.setattr(service, "load_build_policy", load_build_policy)
    monkeypatch.setenv("SAGE_BUILD_POLL_FAILURE_LIMIT", "0")

    with pytest.raises(ValueError, match="SAGE_BUILD_POLL_FAILURE_LIMIT"):
        Orchestrator(tmp_path / "workspace", tmp_path / "template", object(), object())


def test_every_active_build_limit_is_read_from_the_policy_at_its_call_site():
    """Plant: replace one policy read below with a literal and this source contract turns red."""
    source = Path(service.__file__).read_text()
    active_fields = set(EXPECTED) - {
        "tool_result_max_bytes",
        "tool_result_aggregate_max_bytes",
        "tool_result_head_fraction",
        "pre_edit_model_call_limit",
        "pre_edit_request_non_media_max_bytes",
        "pre_edit_original_tool_result_max_bytes",
        "pre_edit_clean_recovery_limit",
        "build_context_non_media_max_bytes",
        "build_context_automatic_rollover_limit",
        "build_context_continuation_reference_max_count",
    }

    assert all(f"self._build_policy.{field}" in source for field in active_fields)
    assert "SAGE_MAX_NUDGES" not in source
    assert "SAGE_PHASED_MAX_SECONDS" not in source
