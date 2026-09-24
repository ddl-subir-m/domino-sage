"""Typed limits for Build turns.

This module is the only declaration site for Build limit defaults and environment keys.

===============================  ================================================  =========
Field                            Environment key                                   Default
===============================  ================================================  =========
poll_failure_limit               SAGE_BUILD_POLL_FAILURE_LIMIT                     4
quiet_timeout_seconds            SAGE_BUILD_QUIET_TIMEOUT_SECONDS                  120
open_tool_quiet_timeout_seconds  SAGE_BUILD_OPEN_TOOL_QUIET_TIMEOUT_SECONDS        600
stop_grace_seconds               SAGE_BUILD_STOP_GRACE_SECONDS                     30
runtime_error_wait_seconds       SAGE_BUILD_RUNTIME_ERROR_WAIT_SECONDS             4
poll_message_limit               SAGE_BUILD_POLL_MESSAGE_LIMIT                     40
live_read_limit                  SAGE_BUILD_LIVE_READ_LIMIT                        25
exact_repeat_limit               SAGE_BUILD_EXACT_REPEAT_LIMIT                     3
bash_call_limit                  SAGE_BUILD_BASH_CALL_LIMIT                        40
failed_write_limit               SAGE_BUILD_FAILED_WRITE_LIMIT                     10
no_edit_nudge_limit              SAGE_BUILD_NO_EDIT_NUDGE_LIMIT                    3 [1]
runtime_repair_limit             SAGE_BUILD_RUNTIME_REPAIR_LIMIT                   3
leak_repair_limit                SAGE_BUILD_LEAK_REPAIR_LIMIT                      2
gateway_repair_limit             SAGE_BUILD_GATEWAY_REPAIR_LIMIT                   2
phased_max_seconds               SAGE_BUILD_PHASED_MAX_SECONDS                     1800 [2]
tool_result_max_bytes            SAGE_BUILD_TOOL_RESULT_MAX_BYTES                  16384
tool_result_aggregate_max_bytes  SAGE_BUILD_TOOL_RESULT_AGGREGATE_MAX_BYTES        131072
tool_result_head_fraction        SAGE_BUILD_TOOL_RESULT_HEAD_FRACTION              0.75
pre_edit_model_call_limit        SAGE_BUILD_PRE_EDIT_MODEL_CALL_LIMIT              12
pre_edit_request_non_media_max_bytes
                                 SAGE_BUILD_PRE_EDIT_REQUEST_NON_MEDIA_MAX_BYTES   524288
pre_edit_original_tool_result_max_bytes
                                 SAGE_BUILD_PRE_EDIT_ORIGINAL_TOOL_RESULT_MAX_BYTES 196608
pre_edit_clean_recovery_limit    SAGE_BUILD_PRE_EDIT_CLEAN_RECOVERY_LIMIT          1
build_context_non_media_max_bytes
                                 SAGE_BUILD_CONTEXT_NON_MEDIA_MAX_BYTES            786432
build_context_automatic_rollover_limit
                                 SAGE_BUILD_CONTEXT_AUTOMATIC_ROLLOVER_LIMIT       1
build_context_continuation_reference_max_count
                                 SAGE_BUILD_CONTEXT_CONTINUATION_REFERENCE_MAX_COUNT 100
plan_reasoning_effort            SAGE_BUILD_PLAN_REASONING_EFFORT                    high
implement_reasoning_effort       SAGE_BUILD_IMPLEMENT_REASONING_EFFORT               low
===============================  ================================================  =========

[1] ``SAGE_MAX_NUDGES`` remains an alias.
[2] ``SAGE_PHASED_MAX_SECONDS`` remains an alias.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields

log = logging.getLogger("sage.build_policy")


@dataclass(frozen=True, slots=True)
class BuildPolicy:
    """All active and approved Build limits.

    Direct construction is the test injection seam. Production values come from
    :func:`load_build_policy`, which validates environment overrides.
    """

    poll_failure_limit: int = 4
    quiet_timeout_seconds: float = 120.0
    open_tool_quiet_timeout_seconds: float = 600.0
    stop_grace_seconds: float = 30.0
    runtime_error_wait_seconds: float = 4.0
    poll_message_limit: int = 40
    live_read_limit: int = 25
    exact_repeat_limit: int = 3
    bash_call_limit: int = 40
    failed_write_limit: int = 10
    no_edit_nudge_limit: int = 3
    runtime_repair_limit: int = 3
    leak_repair_limit: int = 2
    gateway_repair_limit: int = 2
    phased_max_seconds: float = 1_800.0
    tool_result_max_bytes: int = 16_384
    tool_result_aggregate_max_bytes: int = 131_072
    tool_result_head_fraction: float = 0.75
    pre_edit_model_call_limit: int = 12
    pre_edit_request_non_media_max_bytes: int = 524_288
    pre_edit_original_tool_result_max_bytes: int = 196_608
    pre_edit_clean_recovery_limit: int = 1
    build_context_non_media_max_bytes: int = 786_432
    build_context_automatic_rollover_limit: int = 1
    build_context_continuation_reference_max_count: int = 100
    plan_reasoning_effort: str = "high"
    implement_reasoning_effort: str = "low"


@dataclass(frozen=True, slots=True)
class _Setting:
    field: str
    key: str
    kind: str = "count"
    alias: str | None = None


_SETTINGS = (
    _Setting("poll_failure_limit", "SAGE_BUILD_POLL_FAILURE_LIMIT"),
    _Setting("quiet_timeout_seconds", "SAGE_BUILD_QUIET_TIMEOUT_SECONDS", "duration"),
    _Setting("open_tool_quiet_timeout_seconds",
             "SAGE_BUILD_OPEN_TOOL_QUIET_TIMEOUT_SECONDS", "duration"),
    _Setting("stop_grace_seconds", "SAGE_BUILD_STOP_GRACE_SECONDS", "duration"),
    _Setting("runtime_error_wait_seconds", "SAGE_BUILD_RUNTIME_ERROR_WAIT_SECONDS", "duration"),
    _Setting("poll_message_limit", "SAGE_BUILD_POLL_MESSAGE_LIMIT"),
    _Setting("live_read_limit", "SAGE_BUILD_LIVE_READ_LIMIT"),
    _Setting("exact_repeat_limit", "SAGE_BUILD_EXACT_REPEAT_LIMIT"),
    _Setting("bash_call_limit", "SAGE_BUILD_BASH_CALL_LIMIT"),
    _Setting("failed_write_limit", "SAGE_BUILD_FAILED_WRITE_LIMIT"),
    _Setting("no_edit_nudge_limit", "SAGE_BUILD_NO_EDIT_NUDGE_LIMIT", alias="SAGE_MAX_NUDGES"),
    _Setting("runtime_repair_limit", "SAGE_BUILD_RUNTIME_REPAIR_LIMIT"),
    _Setting("leak_repair_limit", "SAGE_BUILD_LEAK_REPAIR_LIMIT"),
    _Setting("gateway_repair_limit", "SAGE_BUILD_GATEWAY_REPAIR_LIMIT"),
    _Setting("phased_max_seconds", "SAGE_BUILD_PHASED_MAX_SECONDS", "duration",
             alias="SAGE_PHASED_MAX_SECONDS"),
    _Setting("tool_result_max_bytes", "SAGE_BUILD_TOOL_RESULT_MAX_BYTES"),
    _Setting("tool_result_aggregate_max_bytes", "SAGE_BUILD_TOOL_RESULT_AGGREGATE_MAX_BYTES"),
    _Setting("tool_result_head_fraction", "SAGE_BUILD_TOOL_RESULT_HEAD_FRACTION", "fraction"),
    _Setting("pre_edit_model_call_limit", "SAGE_BUILD_PRE_EDIT_MODEL_CALL_LIMIT"),
    _Setting("pre_edit_request_non_media_max_bytes",
             "SAGE_BUILD_PRE_EDIT_REQUEST_NON_MEDIA_MAX_BYTES"),
    _Setting("pre_edit_original_tool_result_max_bytes",
             "SAGE_BUILD_PRE_EDIT_ORIGINAL_TOOL_RESULT_MAX_BYTES"),
    _Setting("pre_edit_clean_recovery_limit", "SAGE_BUILD_PRE_EDIT_CLEAN_RECOVERY_LIMIT"),
    _Setting("build_context_non_media_max_bytes",
             "SAGE_BUILD_CONTEXT_NON_MEDIA_MAX_BYTES"),
    _Setting("build_context_automatic_rollover_limit",
             "SAGE_BUILD_CONTEXT_AUTOMATIC_ROLLOVER_LIMIT"),
    _Setting("build_context_continuation_reference_max_count",
             "SAGE_BUILD_CONTEXT_CONTINUATION_REFERENCE_MAX_COUNT"),
    _Setting("plan_reasoning_effort", "SAGE_BUILD_PLAN_REASONING_EFFORT", "effort"),
    _Setting("implement_reasoning_effort", "SAGE_BUILD_IMPLEMENT_REASONING_EFFORT", "effort"),
)

_POSITIVE_INTEGER = re.compile(r"[0-9]+\Z")
_POSITIVE_NUMBER = re.compile(
    r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "max", "xhigh"})


def _value(setting: _Setting, key: str, raw: str) -> int | float | str:
    try:
        if isinstance(raw, bool):
            raise TypeError
        if setting.kind == "effort":
            if raw not in _REASONING_EFFORTS:
                raise ValueError
            return raw
        if setting.kind == "count":
            if not _POSITIVE_INTEGER.fullmatch(raw):
                raise ValueError
            value: int | float = int(raw, 10)
        else:
            if not _POSITIVE_NUMBER.fullmatch(raw):
                raise ValueError
            value = float(raw)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid setting {key}") from error
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"Invalid setting {key}")
    if setting.kind == "fraction" and value >= 1:
        raise ValueError(f"Invalid setting {key}")
    return value


def load_build_policy(environ: Mapping[str, str] | None = None) -> BuildPolicy:
    """Load and validate one Build policy from ``environ``.

    New names win over aliases. Warnings and errors name setting keys only; they never include the
    setting value or any other environment content.
    """

    source = os.environ if environ is None else environ
    defaults = BuildPolicy()
    values = {field.name: getattr(defaults, field.name) for field in fields(defaults)}
    for setting in _SETTINGS:
        new_present = setting.key in source
        alias_present = setting.alias is not None and setting.alias in source
        if new_present and alias_present:
            log.warning("Both %s and %s are set; %s takes precedence.",
                        setting.key, setting.alias, setting.key)
        selected = setting.key if new_present else setting.alias if alias_present else None
        if selected is not None:
            values[setting.field] = _value(setting, selected, source[selected])
    return BuildPolicy(**values)
