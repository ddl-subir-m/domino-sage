"""Each stack ships one working example of a screen with a search, a select and a paged query (#699).

The agent copies shapes it can see. The example puts the query rules in one runnable place: the
typed text waits for a pause before it queries, a select applies at once, a filter change starts
again at page one, the total comes from a query that counted every matching row, and the region
choices come from their own distinct-values query rather than from a page of rows.

It runs on a declared fixture catalog beside it (`orders.queries.json`), never on a query name that
pretends to be a production connection. The same catalog's SQL is run here against an in-memory
SQLite table larger than the response cap, so the result-shape rules are checked on real rows, not
only stated. Every Node process here is `subprocess.run`, which waits for it to exit.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from sage.resources.app_helpers import FASTAPI
from sage.resources.app_helpers import TEMPLATE as TEMPLATE_NAMES
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.bound_schema import BoundSource, agents_block

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).parent / "js" / "app_query_records_harness.mjs"
TEMPLATES = ["fastapi-antd", "react-vite"]
EXAMPLES = {
    "fastapi-antd": ("static/examples", ["OrdersScreen.js"]),
    "react-vite": ("src/examples", ["OrdersScreen.tsx", "useOrdersView.ts"]),
}

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def _catalog(template: str) -> list[dict]:
    folder, _ = EXAMPLES[template]
    return json.loads((REPO / "template" / template / folder / "orders.queries.json").read_text())


def _declared(template: str) -> dict[str, set[str]]:
    return {q["name"]: {p["name"] for p in q["params"]} for q in _catalog(template)}


# ---- the declared fixture catalog ----------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_fixture_catalog_names_say_they_are_examples_and_declare_every_placeholder(template):
    for query in _catalog(template):
        assert query["name"].startswith("example_"), query["name"]
        placeholders = set(re.findall(r"(?<!:):([A-Za-z_]\w*)", query["sql"]))
        assert placeholders == {p["name"] for p in query["params"]}, query["name"]


def test_both_stacks_declare_the_same_fixture_catalog():
    assert _catalog("fastapi-antd") == _catalog("react-vite")


REGIONS = ["East", "North", "South", "West"]
CUSTOMERS = ["Acme Corp", "Bolt Co", "Cobalt Labs", "Dune Foods", "Ember Inc"]


def _orders(n: int = 6000) -> list[tuple]:
    """Synthetic rows, more than the 5000-row response cap, with many orders sharing a date so a
    page boundary falls inside a tie."""
    rng = random.Random(699)
    return [(i, f"2026-0{1 + i % 9}-{1 + i % 28:02d}", rng.choice(CUSTOMERS), rng.choice(REGIONS),
             round(rng.uniform(5, 500), 2)) for i in range(1, n + 1)]


def _run(sql_of: dict, name: str, params: dict) -> list[tuple]:
    # Stored out of id order, so rows tied on date come back in no useful order unless the query
    # breaks the tie itself.
    rows = _orders()
    random.Random(1).shuffle(rows)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE orders (order_id INTEGER, placed_on TEXT, customer TEXT, "
                   "region TEXT, amount REAL)")
        db.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", rows)
        return db.execute(sql_of[name], params).fetchall()


SQL = {q["name"]: q["sql"] for q in _catalog("fastapi-antd")}


def test_the_summary_counts_every_matching_row_not_a_capped_page():
    got = _run(SQL, "example_orders_summary", {"region": "West", "search": "co"})
    truth = [r for r in _orders() if r[3] == "West" and "co" in r[2].lower()]
    assert len(truth) > 50
    assert got[0][0] == len(truth)
    assert got[0][1] == pytest.approx(sum(r[4] for r in truth))


def test_all_is_the_sentinel_and_counts_every_row():
    got = _run(SQL, "example_orders_summary", {"region": "__all__", "search": ""})
    assert got[0][0] == 6000                       # more than the response cap


def test_two_detail_pages_are_ordered_and_never_repeat_a_row():
    params = {"region": "__all__", "search": ""}
    first = _run(SQL, "example_orders_page", {**params, "offset": 0})
    second = _run(SQL, "example_orders_page", {**params, "offset": 50})
    assert len(first) == len(second) == 50
    assert not {r[0] for r in first} & {r[0] for r in second}
    expected = sorted(_orders(), key=lambda r: (r[1], -r[0]), reverse=True)[:100]
    assert [r[0] for r in first + second] == [r[0] for r in expected]


def test_the_region_choices_are_the_distinct_values_of_the_whole_table():
    assert [r[0] for r in _run(SQL, "example_order_regions", {})] == REGIONS


# ---- the example screen's state, driven through the stack's own helpers ---------------------------

ANSWERS = {
    # A region no row on this page is in: the choices are the store's, not the page's.
    "example_order_regions": {"body": {"columns": ["REGION"], "rows": [["East"], ["North"], ["West"]]}},
    "example_orders_summary": {"body": {"columns": ["ORDER_COUNT", "TOTAL_AMOUNT"],
                                        "rows": [[6000, 123456.5]]}},
    "example_orders_page": {"body": {"columns": ["ORDER_ID", "PLACED_ON", "CUSTOMER", "REGION", "AMOUNT"],
                                     "rows": [[7, "2026-03-01", "Acme Corp", "East", 10.5],
                                              [3, "2026-03-01", "Bolt Co", "West", 99.0]]}},
}


def _drive(template: str, steps: list, *, hook="example", by_query=None) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"template": template, "steps": steps, "hook": hook,
                          "byQuery": ANSWERS if by_query is None else by_query}),
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(out.stdout)


def _sent(requests: list, name: str) -> list[dict]:
    return [r["params"] for r in requests if r["query"] == name]


@node
@pytest.mark.parametrize("template", TEMPLATES)
def test_the_example_asks_only_declared_queries_with_every_declared_parameter(template):
    got = _drive(template, [{"mount": []}, "settle"])
    declared = _declared(template)
    assert {r["query"] for r in got["requests"]} == set(declared)
    for request in got["requests"]:
        assert set(request["params"]) == declared[request["query"]], request
    assert _sent(got["requests"], "example_orders_page") == [
        {"region": "__all__", "search": "", "offset": 0}]


@node
@pytest.mark.parametrize("template", TEMPLATES)
def test_rapid_typing_sends_one_search_after_the_pause(template):
    steps = [{"mount": []}, "settle"]
    for text in ("a", "ac", "acm"):
        steps += [{"call": ["setDraft", text]}, {"advance": 100}]
    steps += [{"advance": 300}, "settle"]
    got = _drive(template, steps)
    assert got["snapshots"][2]["draft"] == "a"        # the box shows each key at once
    assert _sent(got["requests"], "example_orders_page") == [
        {"region": "__all__", "search": "", "offset": 0},
        {"region": "__all__", "search": "acm", "offset": 0}]
    assert len(_sent(got["requests"], "example_orders_summary")) == 2


@node
@pytest.mark.parametrize("template", TEMPLATES)
def test_a_select_applies_at_once_and_a_filter_change_starts_again_at_page_one(template):
    got = _drive(template, [{"mount": []}, "settle", {"call": ["setPage", 2]}, "settle",
                            {"call": ["setRegion", "West"]}, "settle"])
    assert _sent(got["requests"], "example_orders_page") == [
        {"region": "__all__", "search": "", "offset": 0},
        {"region": "__all__", "search": "", "offset": 100},
        {"region": "West", "search": "", "offset": 0}]
    assert got["snapshots"][-1]["page"] == 0


@node
@pytest.mark.parametrize("template", TEMPLATES)
def test_the_total_is_the_counted_one_and_the_choices_are_the_distinct_ones(template):
    view = _drive(template, [{"mount": []}, "settle"])["snapshots"][-1]
    assert view["total"] == 6000                      # not the 2 rows on this page
    assert view["regionChoices"] == ["East", "North", "West"]
    assert [r["order_id"] for r in view["rows"]] == [7, 3]


@node
@pytest.mark.parametrize("template", TEMPLATES)
def test_a_failed_page_is_an_error_not_an_empty_table(template):
    failing = {**ANSWERS, "example_orders_page": {"status": 403, "body": {"error": "No access."}}}
    view = _drive(template, [{"mount": []}, "settle"], by_query=failing)["snapshots"][-1]
    assert view["detail"]["status"] == "error"
    assert view["rows"] == []
    assert view["total"] == 6000


def _find(tree, kind: str) -> list[dict]:
    found = []
    if isinstance(tree, dict) and tree.get("type") == kind:
        found.append(tree)
    for value in (tree.values() if isinstance(tree, dict) else tree if isinstance(tree, list) else ()):
        found += _find(value, kind)
    return found


@node
def test_the_fastapi_screen_renders_the_page_rows_and_the_counted_total():
    tree = _drive("fastapi-antd", [{"mount": []}, "settle"], hook="exampleScreen")["snapshots"][-1]
    (table,) = _find(tree, "antd.Table")
    assert [r["order_id"] for r in table["props"]["dataSource"]] == [7, 3]
    assert 6000 in [s["props"].get("value") for s in _find(tree, "antd.Statistic")]
    (pages,) = _find(tree, "antd.Pagination")
    assert (pages["props"]["total"], pages["props"]["pageSize"]) == (6000, 50)


# ---- where the example lives ---------------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_example_never_names_the_raw_query_call(template):
    """An unbound app is judged to be reaching for a store when an app file names the raw query
    call (`Orchestrator._reaches_for_a_store`). The example ships to every new app, bound or not,
    so naming it would tell every unbound app it is broken."""
    folder, files = EXAMPLES[template]
    for name in files:
        assert "runQuery" not in (REPO / "template" / template / folder / name).read_text(), name


def test_the_fastapi_example_is_not_loaded_by_the_page():
    index = (REPO / "template/fastapi-antd/static/index.html").read_text()
    assert "static/examples/" not in index


@pytest.mark.parametrize("names, path", [(FASTAPI, "static/examples/OrdersScreen.js"),
                                         (TEMPLATE_NAMES, "src/examples/OrdersScreen.tsx")],
                         ids=TEMPLATES)
def test_the_query_guidance_points_at_the_example_only_if_it_exists(names, path):
    binding = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                      "DWH", "MARTS", None, "SnowflakeConfig")
    block = " ".join(agents_block([BoundSource(binding, [], [], None)], [], 5000, names=names).split())
    assert f"If `{path}` exists" in block
