"""Snowflake opens every statement with no current database.

Measured on Snowflake-Data-Warehouse: `SELECT … FROM information_schema.tables` fails with
"This session does not have a current database. Call 'USE DATABASE', or use a qualified name."
Qualified names work, and so does `SHOW DATABASES`. `USE DATABASE` does not: each statement
opens and closes its own client, so a USE never applies to the next one.

A turn that obeyed the store's advice asked the person for table names. `dwh.marts` was already
the database and the schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sage.liveread import run
from sage.orchestrator.service import _chat_context_line
from sage.resources.provider import ResourceUnavailable

_SNOWFLAKE = (
    "Snowflake-Data-Warehouse did not answer: SQL compilation error: "
    "This session does not have a current database. Call 'USE DATABASE', "
    "or use a qualified name."
)


@dataclass
class _Source:
    connector_type: str = "SnowflakeConfig"
    name: str = "Snowflake-Data-Warehouse"


def _turn(tmp_path: Path, fail):
    return run.Turn(
        thread_id="thr_a",
        examples_dir=tmp_path / "examples" / "thr_a",
        bound={"datasource": ("Snowflake-Data-Warehouse",)},
        source_for=lambda n: _Source() if n == "Snowflake-Data-Warehouse" else None,
        run_statement=fail,
    )


def test_the_missing_database_error_names_the_next_statement(tmp_path: Path):
    def fail(source, sql, *, limit):
        raise ResourceUnavailable(_SNOWFLAKE)

    said = run.perform("live_read_query", {
        "source": "Snowflake-Data-Warehouse",
        "sql": "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES LIMIT 50",
    }, _turn(tmp_path, fail))

    assert "current database" in said
    assert "SHOW DATABASES" in said
    assert "<database>.INFORMATION_SCHEMA.TABLES" in said
    assert "does not apply to the next" in said
    assert "dwh.marts" in said
    assert "Do not ask the person to name a table" in said
    # The store's own advice is what sent the turn into USE DATABASE. It must not be the
    # instruction that survives.
    assert "Call 'USE DATABASE'" not in said


def test_a_different_store_error_is_left_alone(tmp_path: Path):
    def fail(source, sql, *, limit):
        raise ResourceUnavailable(
            "Snowflake-Data-Warehouse did not answer: SQL compilation error: "
            "invalid identifier 'NOPE'")

    said = run.perform("live_read_query", {
        "source": "Snowflake-Data-Warehouse",
        "sql": "SELECT NOPE FROM DWH.MARTS.SFDC__CASE",
    }, _turn(tmp_path, fail))

    assert "invalid identifier" in said
    assert "SHOW DATABASES" not in said


def test_the_unscoped_source_line_does_not_treat_that_error_as_a_dead_end():
    # The line the model reads, not a chat turn. A turn boots a project, a save timer and a
    # data-source lookup; this sentence does not need any of them.
    line = _chat_context_line({
        "kind": "data_source",
        "name": "Snowflake-Data-Warehouse",
        "resourceId": "data_source:ds-dwh",
        "sourceName": "Snowflake-Data-Warehouse",
    }, thread_id="thr_a")

    assert "SHOW DATABASES" in line
    assert "does not apply to the next" in line
    assert "dwh.marts" in line
    assert "Do not ask the person to name a table until that lookup has failed" not in line


def test_the_static_chat_prompt_does_not_repeat_the_warehouse_procedure():
    """The repair stays on the turn line and in the investigation skill.

    A second copy in the always-on chat prompt is what an open investigation followed instead of
    the skill's four-statement budget. Both copies, because the mirror is what the model is sent.
    """
    root = Path(__file__).resolve().parents[2]
    pack = " ".join((root / "template" / "chat" / "AGENTS.md").read_text().split())
    mirror = " ".join(json.loads(
        (root / "opencode.json").read_text())["agent"]["sage-chat"]["prompt"].split())
    skill = " ".join((root / "template" / "skills" / "investigate-weak-signals" / "SKILL.md")
                     .read_text().split())
    for name, text in (("AGENTS.md", pack), ("sage-chat", mirror)):
        assert "SHOW DATABASES" not in text, name
        assert "INFORMATION_SCHEMA" not in text, name
        assert "SUBSTR(" not in text, name
    assert "SHOW DATABASES" in skill
    assert "SHOW DATABASES" in run.SESSION_DATABASE_REPAIR
