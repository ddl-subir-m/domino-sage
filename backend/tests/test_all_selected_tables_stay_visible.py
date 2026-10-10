"""The table picker and composer agree about a multi-table Binding."""
import json
import subprocess
from pathlib import Path

from .test_a_data_source_binds_first_and_is_scoped_afterwards import AT_APP_A, _steps


def _guard(**options):
    harness = Path(__file__).parent / "js" / "table_scope_guard_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)], input=json.dumps(options), text=True,
        capture_output=True, check=True, timeout=15,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_mentioned_table_in_the_binding_does_not_warn():
    result = _guard(prompt="Chart @DIM_ACCOUNT", tables=[
        {"database": "DWH", "schema": "MARTS", "table": name}
        for name in ("FCT_USAGE_DAILY", "DIM_ACCOUNT")
    ])
    assert result["guard"]["line"] == ""
    assert result["guard"]["labels"] == []
    assert "DIM_ACCOUNT" in result["scopeShown"]
    assert "FCT_USAGE_DAILY" in result["scopeShown"]


def test_the_scope_repair_opens_its_modal():
    result = _guard(prompt="Chart @DIM_ACCOUNT")
    assert result["scopeOpened"] == ["data_source:ds-dwh"]
    assert result["dependenciesOpen"] is True


def test_the_scope_menu_stays_open_while_stepping_to_a_table():
    result = _steps([{
        **AT_APP_A, "scopeIn": "Market data EOD", "menuClose": True,
        "walk": ["DWH", "MARTS", "FCT_USAGE_DAILY"],
    }])[0]
    assert result["scoped"] == [{
        "id": "ds_1", "database": "DWH", "schema": "MARTS", "table": "FCT_USAGE_DAILY",
    }]
    assert result["now"]["open"] is False
