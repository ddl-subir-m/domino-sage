"""The Workbench half of the Dataset card (#196, ADR-0039).

Two things break here without throwing anything, which is why this runs the real `store.js` rather
than reading it.

The transcript is a chain of `ev.type === ...` branches — two of them, one per mode — and a turn
event with no branch reaches the transcript and vanishes. A Dataset card that vanishes is a turn
that asked nothing and answered nothing.

And the click is TWO acts, deliberately: it writes the record and then sends the request the person
already made against it. Drop the second and the dashboard they asked for is never built. Drop
`skipDatasetGate` from the second and the build hands back the same card it was answering, forever.
Neither shows up in Python, because neither crosses it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "dataset_files_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

PROMPT = "build me a daily summary of calls"

FILE_ROWS = [
    {"kind": "file", "path": "calls_daily.csv", "size": 2048},
    {"kind": "file", "path": "accounts.csv", "size": 512},
]

# One turn, exactly as the orchestrator wrote it: the person's sentence, the card, and the `done`
# that says the turn stopped here without building.
HISTORY = [
    {"type": "user", "text": PROMPT},
    {"type": "dataset-files", "prompt": PROMPT,
     "message": "Sage listed what revenue_2026 holds.",
     "datasetId": "ds_revenue", "datasetName": "revenue_2026", "answered": {},
     "rows": FILE_ROWS, "allRows": FILE_ROWS, "total": 2, "matched": 1, "truncated": False},
    {"type": "done", "ok": False, "decision": "dataset files"},
]


def _run(*, mode: str = "build", history: list[dict] | None = None,
         click: dict | None = None, answered: dict | None = None) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"mode": mode, "history": history or HISTORY, "prompt": PROMPT,
                          "answered": answered or {},
                          "click": click or {"kind": "file", "path": "calls_daily.csv"}}),
        check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _chat_history() -> list[dict]:
    card = {**HISTORY[1], "threadId": "thr_1"}
    card.pop("answered")
    return [HISTORY[0], card, HISTORY[2]]


@needs_node
def test_the_card_is_drawn_from_the_turn_row_with_its_rows_intact():
    """The whole card survives the transcript. A branch chain that dropped the rows would leave a
    sentence asking somebody to pick from a Dataset it had stopped showing them."""
    cards = _run()["cards"]

    assert len(cards) == 1
    assert cards[0]["datasetId"] == "ds_revenue"
    assert cards[0]["prompt"] == PROMPT
    assert cards[0]["total"] == 2
    assert [r["path"] for r in cards[0]["rows"]] == ["calls_daily.csv", "accounts.csv"]


@needs_node
def test_a_card_read_back_off_the_server_carries_no_buttons():
    """`live` is set only on a frame that arrived over SSE this session. A card replayed on a page
    load is a record of a decision somebody already made, and its buttons would attach files to an
    app and start a build out of a message they are only scrolling back through.

    The buttons GO rather than grey out, which is what every offer beside this one does: a list of
    dead buttons under a sentence still asking somebody to pick reads as an app that has broken.
    """
    card = _run()["cards"][0]

    assert card["live"] is False
    assert card["drawn"]["pickable"] == []
    assert card["drawn"]["past"] is False


@needs_node
def test_the_click_attaches_the_file_first_and_then_replays_the_request():
    """Two calls, in that order. The attach stands whether or not the build after it succeeds, and
    the build is an ordinary turn taking the turn lock like any other.

    `skipDatasetGate` on the replay is the card being answered rather than skipped. Without it the
    same request meets the same gate and gets the same card back.
    """
    out = _run()

    assert out["record"]["url"] == "api/project/assets/ds_revenue/files/attach"
    assert out["record"]["body"] == {"path": "calls_daily.csv"}
    assert "api/project/build/stream" in out["routes"]
    assert out["routes"].index("api/project/build/stream") > 0
    assert out["replay"]["prompt"] == PROMPT
    assert out["replay"]["skipDatasetGate"] is True


@needs_node
def test_clicking_a_folder_attaches_the_folder_rather_than_its_files_one_at_a_time():
    """The folder is the unit of the act (ADR-0029). A card that answered a Dataset partitioned to
    the day with one call per file would rebuild the 200-click problem the folder act removed."""
    rows = [{"kind": "folder", "path": "raw/2026/01", "count": 31},
            {"kind": "folder", "path": "raw/2026/02", "count": 28}]
    history = [HISTORY[0],
               {**HISTORY[1], "rows": rows, "allRows": rows, "total": 2, "matched": 0},
               HISTORY[2]]

    out = _run(history=[history[0], {**history[1], "live": True}, history[2]],
               click={"kind": "folder", "path": "raw/2026/01"})

    assert out["record"]["url"] == "api/project/assets/ds_revenue/files/attach-folder"
    assert out["record"]["body"] == {"folder": "raw/2026/01"}
    assert out["replay"]["skipDatasetGate"] is True
    # And the row says what it stands for, because the path alone understates an act that carries
    # thirty-one files.
    assert out["cards"][0]["drawn"]["pickable"] == ["raw/2026/01 — 31 files",
                                                    "raw/2026/02 — 28 files"]


@needs_node
def test_a_row_the_listing_never_measured_draws_no_size_at_all():
    """A Dataset with no mount here can be named without being weighed, and the server leaves the
    key off rather than sending a zero (#197). A row that filled it back in as "0 bytes" would put
    the lie back on the card the omission exists to keep off it — and the row still has to be
    pickable, because a single-file attach works on an unmounted Dataset as a download."""
    rows = [{"kind": "file", "path": "calls_daily.csv"}, {"kind": "file", "path": "accounts.csv"}]
    history = [HISTORY[0],
               {**HISTORY[1], "live": True, "rows": rows, "allRows": rows, "total": 2},
               HISTORY[2]]

    card = _run(history=history)["cards"][0]

    assert card["drawn"]["pickable"] == ["calls_daily.csv", "accounts.csv"]


@needs_node
def test_the_replay_carries_the_gates_this_turn_had_already_answered():
    """"Start over and summarise my calls" answers the reset offer and then reaches this card. The
    reset gate is a prompt match with nothing remembered, so a replay that dropped `skipResetGate`
    would offer to throw the app away a second time — for a request already answered, with the
    destructive button back under it (#185)."""
    out = _run(answered={"skipResetGate": True, "skipSourceGate": True})

    assert out["replay"]["skipResetGate"] is True
    assert out["replay"]["skipSourceGate"] is True
    assert out["replay"]["skipDatasetGate"] is True


@needs_node
def test_answering_the_card_retires_it_rather_than_leaving_it_clickable():
    """Reloading the transcript is what retires an offer here, as it is for the offers beside it:
    the server's copy carries no `live`, so the buttons go with the reload.

    Without it the card stays answerable after its build finishes, and a second click months into a
    conversation would attach another file and start another build.
    """
    out = _run()

    assert any(r.startswith("api/project/history") for r in out["routes"])
    assert all(card["live"] is False for card in out["cardsAfter"])


@needs_node
def test_a_live_card_offers_every_row_and_a_way_past_the_question():
    """The rows are the answer and the way past is the escape hatch: an app that holds its own data
    is a real case, and a listing outage must not take somebody's build hostage (#194, story 6)."""
    live = [HISTORY[0], {**HISTORY[1], "live": True}, HISTORY[2]]

    card = _run(history=live)["cards"][0]

    assert card["drawn"]["pickable"] == ["calls_daily.csv — 2.0 KB", "accounts.csv — 512 B"]
    assert card["drawn"]["past"] is True


@needs_node
def test_the_way_past_the_card_names_the_dataset_it_is_getting_past():
    """`skipDatasetGate` answers this request; `datasetDismissed` answers the app. The gate reads
    the app's state rather than the request's words, so without the name it would ask again on the
    next sentence, whatever that sentence was about."""
    out = _run(click={"kind": "past"})

    assert out["replay"]["skipDatasetGate"] is True
    assert out["replay"]["datasetDismissed"] == "ds_revenue"
    # Nothing recorded: the point of this button is that no file was chosen.
    assert not [r for r in out["routes"] if "/files/attach" in r]


@needs_node
def test_the_chat_way_past_the_card_names_it_too():
    """The same, against the Thread — Chat's card is drawn off the Thread's own Dataset row."""
    out = _chat(click={"kind": "past"})

    assert out["replay"] == {"prompt": PROMPT, "skipTableGate": False,
                             "skipDatasetGate": True, "datasetDismissed": "ds_revenue"}


@needs_node
def test_a_long_listing_opens_short_and_keeps_the_rest_one_click_behind():
    """A bad ordering has to cost a scroll and never be a dead end. The shortlist is what mounts;
    the rest are behind a button that says how many there are."""
    every = [{"kind": "file", "path": f"part_{n:02d}.csv", "size": 10} for n in range(40)]
    history = [HISTORY[0],
               {**HISTORY[1], "live": True, "rows": every[:5], "allRows": every, "total": 40},
               HISTORY[2]]

    card = _run(history=history)["cards"][0]

    assert len(card["drawn"]["pickable"]) == 5
    assert card["drawn"]["more"] is True


# ---- the same card, in Chat ---------------------------------------------------------------------


def _chat(**kw) -> dict:
    return _run(mode="chat", history=_chat_history(), **kw)


@needs_node
def test_the_card_survives_the_chat_transcript_too():
    """`historyToMessages` is its own chain of branches, so nothing the Build tests above prove says
    anything about this one. A row whose type no branch reaches vanishes from the Thread."""
    cards = _chat()["cards"]

    assert len(cards) == 1
    assert cards[0]["threadId"] == "thr_1"
    assert cards[0]["datasetId"] == "ds_revenue"
    assert cards[0]["prompt"] == PROMPT


@needs_node
def test_the_chat_click_pins_the_file_and_then_asks_the_question_again():
    """A different record, not a different act: Chat has no Built App to attach to, so the file
    joins Session context as a `dsfile:` chip and crosses into `App.requires` at the handoff.

    Without `skipDatasetGate` the replayed question meets the same gate and gets the same card back.
    """
    out = _chat()

    assert out["record"]["url"] == "api/threads/thr_1/context/dataset/ds_revenue/file"
    assert out["record"]["body"] == {"path": "calls_daily.csv"}
    assert "api/threads/thr_1/chat/stream" in out["routes"]
    # No `datasetDismissed`: the chip is what stops this card coming back, and a Dataset somebody
    # just answered about must not also be recorded as one they wanted nothing from.
    assert out["replay"] == {"prompt": PROMPT, "skipTableGate": False,
                             "skipDatasetGate": True, "datasetDismissed": ""}


@needs_node
def test_the_chat_card_writes_no_attachment_and_no_binding():
    """The two records Chat must not write. A Binding written here would be a declaration on a
    Built App that does not exist yet, and an Attachment would be files copied into a tree the
    conversation does not have."""
    routes = _chat()["routes"]

    assert not [r for r in routes if "/files/attach" in r]
    assert not [r for r in routes if r.startswith("api/bindings/") and r.endswith("/candidate")]
