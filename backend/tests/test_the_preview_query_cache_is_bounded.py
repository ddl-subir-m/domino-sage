"""The preview's query cache does not grow for the whole session (#739).

It checked its TTL only on read and never deleted, and its key carries the bound parameters, so
every control value a creator tried added an entry of up to 5,000 rows that lived as long as the
app's view. Now an entry past its TTL is dropped on the next write, and the rows held in total are
capped, oldest entry first.
"""
from __future__ import annotations

from sage.preview.queries import CachingExecutor


class _Rows:
    def __init__(self, n: int) -> None:
        self.n = n
        self.calls = 0

    def __call__(self, query, params):
        self.calls += 1
        return {"columns": ["x"], "rows": [[i] for i in range(self.n)], "truncated": False}


class Q:
    name, sql = "usage", "SELECT x"


def test_an_expired_entry_is_dropped_on_the_next_write():
    cache = CachingExecutor(_Rows(1), ttl_s=0.0)
    cache(Q(), {"since": "a"})
    cache(Q(), {"since": "b"})
    assert len(cache._entries) == 1


def test_the_rows_held_are_capped_and_the_oldest_entry_goes_first():
    inner = _Rows(4)
    cache = CachingExecutor(inner, ttl_s=30.0, max_rows=10)
    for value in ("a", "b", "c"):
        cache(Q(), {"since": value})
    assert len(cache._entries) == 2

    cache(Q(), {"since": "c"})
    assert inner.calls == 3, "the newest entry must still be served"
    cache(Q(), {"since": "a"})
    assert inner.calls == 4, "the oldest entry must be the one evicted"


def test_a_result_larger_than_the_cap_is_answered_and_not_kept():
    inner = _Rows(11)
    cache = CachingExecutor(inner, ttl_s=30.0, max_rows=10)
    assert len(cache(Q(), {})["rows"]) == 11
    assert cache._entries == {}
