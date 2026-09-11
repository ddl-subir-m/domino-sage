"""A minted id never carries a shape Domino's `Block PII` guardrail refuses.

`new_id` writes epoch-ms and a random half in hex, and hex digits 0-9 ARE decimal digits. A run of
them fenced by two hex letters is a bare decimal run, which is the one thing that rule refuses. The
id is not payload, so this is not a leak — it is worse. An id IS the session directory
(`apps/<appId>/`), it rides every tool-call path, and every record that references the app copies it
(`history.jsonl`'s `app`, the Thread's `touched[].appId` and `items[].id`, a plan doc's `appId`). So
one unlucky id refuses every request that names it, in Chat and in Build, in every conversation,
for the life of the Project — and nothing a person can reach is wrong.

Live on 2026-09-11: `app_1a0908182187e6abddb1f` carries `0908182187`, fenced by `a` and `e`.
Measured over 400k ids, 2.76% of what `new_id` mints carries a 10-11 digit run.

The refused lengths below are NOT derived from this code. They were measured against the live
gateway and are recorded in `sage.shim.refusal_scan`: exactly 10-11 digits is a phone number and
exactly 16 is a card, while 9, 12-15 and 17+ all pass. The invariant asserted here is stricter and
deliberately so — no run may reach 10 at all. Leaving a run at 12-15 "because those pass" would
stake the fix on a gap in somebody else's rule, and that rule is the gateway's to change.
"""
from __future__ import annotations

import random
import re

import pytest

from sage.workspace import threads
from sage.workspace.threads import new_id, safe_id

# Measured live, not computed here — see the module note and `scripts/guardrail-probe.py`.
REFUSED_RUN_LENGTHS = {10, 11, 16}

# The captured id, split the way `new_id` builds it: 11 hex chars of epoch-ms, then token_hex(5).
LIVE_MS = int("1a090818218", 16)
LIVE_RANDOM = "7e6abddb1f"


def digit_runs(value: str) -> list[str]:
    r"""Every maximal run of decimal digits. `\d+` is already maximal, so a run here is exactly what
    the guardrail's own `(?<!\d)\d{10,11}(?!\d)` would see."""
    return re.findall(r"\d+", value)


@pytest.fixture
def minted_at(monkeypatch):
    """Mint an id at a chosen millisecond with a chosen random half.

    Both halves are pinned because both carry digits and the bug lives across their seam: the live
    failure was nine digits off the end of the timestamp plus one off the front of the random half.
    A test that pinned only one of them could not have produced it.
    """
    def mint(prefix: str, ms: int, random_half: str) -> str:
        monkeypatch.setattr(threads, "_last_id_ms", ms - 1)
        monkeypatch.setattr(threads.time, "time", lambda: 0.0)
        monkeypatch.setattr(threads.secrets, "token_hex", lambda n: random_half)
        return new_id(prefix)
    return mint


def test_the_id_that_refused_a_whole_project_is_not_minted_again(minted_at):
    """The captured failure, reproduced exactly and then refused.

    Pinned to the real millisecond and the real random half, so this is the id that actually
    happened rather than one shaped like it."""
    minted = minted_at("app", LIVE_MS, LIVE_RANDOM)

    assert "0908182187" not in minted, (
        f"{minted} still carries the exact run the gateway refused on 2026-09-11")
    assert not [r for r in digit_runs(minted) if len(r) in REFUSED_RUN_LENGTHS], (
        f"{minted} carries a refused run: {digit_runs(minted)}")
    assert safe_id(minted, "app") == minted


def test_no_millisecond_and_no_random_half_can_mint_a_refused_id(minted_at):
    """The guarantee the captured case is one instance of, swept over 400 days of timestamps.

    Seeded, so a failure here is a real one somebody can re-run rather than a flake. The same sweep
    over the old `f"{ms:011x}{token}"` shape refuses 546 of these 20,000 ids, which is what makes
    this able to go red; against the segmented shape it is not a sampling question at all, because
    no segment is long enough to hold a run of ten.
    """
    rng = random.Random(20260911)
    for _ in range(20_000):
        ms = LIVE_MS + rng.randrange(0, 400 * 86_400_000)
        random_half = "".join(rng.choice("0123456789abcdef") for _ in range(10))
        minted = minted_at("thr", ms, random_half)
        refused = [r for r in digit_runs(minted) if len(r) in REFUSED_RUN_LENGTHS]
        assert not refused, f"{minted} carries a refused run: {refused}"


def test_name_order_is_still_age_order(minted_at):
    """`manager.list_app_ids` sorts by name and calls the answer age, so the separator must not
    reorder anything. Within the new shape this is the timestamp doing the work, unchanged."""
    ids = [minted_at("app", LIVE_MS + i * 1000, f"{i:010x}") for i in range(50)]
    assert ids == sorted(ids)


# An id minted before this change, in both shapes the eighth epoch character can take. Which one it
# is decides the comparison, because that is the column the separator lands in.
@pytest.mark.parametrize("older", [
    f"app_{int('1a090818218', 16):011x}7e6abddb1f",   # eighth char is a hex DIGIT
    f"app_{int('1a09081a218', 16):011x}7e6abddb1f",   # eighth char is a hex LETTER
])
def test_an_id_minted_before_this_change_still_sorts_as_the_older_one(minted_at, older):
    """The upgrade boundary, and the reason the separator is `z`.

    Only ids sharing all seven leading epoch characters — about a 65-second window — can have the
    separator decide their order, and across that window one id is old-shaped and the other new.
    `z` (0x7A) sorts after every hex character, so the new id, which is always the younger, sorts
    later. `-` (0x2D) sorts before all of them and fails both rows here; `_` (0x5F) sorts after the
    hex digits but before the hex letters, and fails the second. Neither is a safe swap.
    """
    older_ms = int(older[len("app_"):len("app_") + 11], 16)
    younger = minted_at("app", older_ms + 1000, "0000000000")

    assert older[:len("app_") + 7] == younger[:len("app_") + 7], "not the window under test"
    assert older < younger, f"{older} (older) must sort before {younger} (younger)"
