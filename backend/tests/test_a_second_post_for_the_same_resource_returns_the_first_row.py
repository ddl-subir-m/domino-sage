"""A chip posted twice during one wait is one chip.

Picking `@MIXPANEL__EVENT` drew no chip until the attach POST answered, and for a table that
POST read the table's columns from the warehouse first — long enough to pick the same row again.
The composer's only guard reads its own chip list, which is empty until the first POST returns,
so the second pick was a second POST, and the store appended every POST it got. Two chips.

The check that decides now lives in `ThreadStore.add_context`, under a lock: a `resourceId`
already on the Thread answers the row that is there and writes nothing. `add_thread_context` reads
the same fact once, up front, so the duplicate never fetches bytes, lists the store, or joins the
project a second time.

Plant, one per condition:
- drop the `held` early return in `add_context` → `test_the_store_itself_answers_one_row_for_two_adds`
  reds on the item count. The route test stays green under that plant — the route's own early
  return covers for it — which is why the store is also asked directly.
- drop the early return in `add_thread_context` → `test_a_duplicate_post_does_no_second_work`
  reds on the listing count.
"""

from __future__ import annotations

from pathlib import Path

from sage.workspace.threads import ThreadStore

from .test_a_chat_build_request_is_asked_which_table import _gong_warehouse, _orch
from .test_a_table_confirmed_in_chat_is_carried_into_build import _client

CHIP = {
    "kind": "data_source",
    "name": "GONG__CALLS",
    "resourceId": "table:ds-dwh:DWH.MARTS.GONG__CALLS",
    "bindingKey": ["data_source", "ds-dwh"],
    "scope": {"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"},
}


def _items(orch, tid: str) -> list[dict]:
    return ThreadStore(orch._chat_project().record.path).read_context(tid).get("items") or []


def test_the_route_answers_one_row_for_two_posts(tmp_path: Path, monkeypatch):
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = orch.create_thread()["id"]

    first = client.post(f"/api/threads/{tid}/context", json=CHIP)
    second = client.post(f"/api/threads/{tid}/context", json=CHIP)
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert second.json()["id"] == first.json()["id"], "the second POST minted its own row"
    assert len(_items(orch, tid)) == 1, _items(orch, tid)


def test_the_store_itself_answers_one_row_for_two_adds(tmp_path: Path):
    """The route's early return hides the store's own check from the test above — measured: with
    the store's return removed and the route's left in, that test stays green. So the store is
    asked directly. It is the check that decides, because it runs under the lock; the route's is
    a read that only skips work."""
    orch, _ = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)

    a = store.add_context(tid, dict(CHIP))
    b = store.add_context(tid, dict(CHIP))
    assert b["id"] == a["id"]
    assert len(store.read_context(tid)["items"]) == 1


def test_a_duplicate_post_does_no_second_work(tmp_path: Path, monkeypatch):
    """The store listing is the cheapest of the three (bytes, listing, join) and the one every
    table chip pays, so its count is the witness."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    listed: list[str] = []
    real = orch._resources.list_data_sources

    def counted():
        listed.append("x")
        return real()

    monkeypatch.setattr(orch._resources, "list_data_sources", counted)
    tid = orch.create_thread()["id"]

    orch.add_thread_context(tid, dict(CHIP))
    before = len(listed)
    assert before >= 1, "positive control: the first add did not list the store"
    orch.add_thread_context(tid, dict(CHIP))
    assert len(listed) == before, "the duplicate listed the store again"


def test_a_different_resource_is_still_its_own_row(tmp_path: Path, monkeypatch):
    """The dedupe is on `resourceId`, not on kind or source: two tables off one store are two
    chips, as they were."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = orch.create_thread()["id"]

    orch.add_thread_context(tid, dict(CHIP))
    orch.add_thread_context(tid, {
        **CHIP,
        "name": "DIM_ACCOUNT",
        "resourceId": "table:ds-dwh:DWH.MARTS.DIM_ACCOUNT",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    assert len(_items(orch, tid)) == 2


def test_a_row_with_no_resource_id_is_not_deduped(tmp_path: Path):
    """Rows the server mints itself (a pinned candidate, an Upload) may carry no `resourceId`;
    absence must not read as "the same as every other absence"."""
    orch, _ = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)

    a = store.add_context(tid, {"kind": "file", "name": "a.csv", "path": "a.csv"})
    b = store.add_context(tid, {"kind": "file", "name": "b.csv", "path": "b.csv"})
    assert a["id"] != b["id"]
    assert len(store.read_context(tid)["items"]) == 2
