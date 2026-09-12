"""The Build history drawer took seconds to open, and what it was waiting for was source code.

The drawer's one read names no conversation — that is the whole point of it (#88), it lists what
has been built into this app by whoever asked — so it is the app's WHOLE log by definition. And a
build log is not mostly prose. Measured on a real 432-row app log: 555KB, of which 465KB is one
field, `detail`, the arguments each tool was called with. A single `bash` row carries the entire
file the agent wrote (`cat > src/App.tsx <<EOF ...`, 27KB in one row).

The drawer draws a list of prompts. It reaches those arguments only for somebody who opens a build,
then opens a turn, then opens the card — three folds down — and nothing was compressed on the way,
because Domino's nginx gzips text/html and not application/json.

So: `detail=off` leaves the tool inputs out and says where each one lives, the card fetches its own
row at the fold that reveals it, and GZipMiddleware compresses what is left. 588KB became 7.2KB on
the wire, which is 82x.

WHAT MUST NOT CHANGE, and each has a test below: the drawer still gets every row (the grouping into
runs is the client's, and a read short of rows would be short of builds); the transcript's own
per-conversation read is untouched, because it draws those cards open; and a card that fetches has
to tell a read that failed from a tool that recorded no input, which are the same empty string.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

_HARNESS = Path(__file__).resolve().parent / "js" / "tool_card_detail_harness.mjs"
_APP_PY = Path(__file__).resolve().parents[1] / "sage" / "orchestrator" / "app.py"
_SHIM_PY = Path(__file__).resolve().parents[1] / "sage" / "shim" / "app.py"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

# A `bash` row the size the real ones are: the whole file, in one field.
_WROTE = "cat > src/App.tsx <<'EOF'\n" + ("export default function App() { return null }\n" * 40)


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=template,
        gateway=object(),  # never called: no build runs here
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
    )


def _client(tmp: Path, monkeypatch) -> TestClient:
    """Two builds of one app, in two conversations, with a big tool row in each — the shape the
    drawer reads and the shape the saving is measured on."""
    import sage.orchestrator.app as appmod

    orch = _orch(tmp)
    ws = orch._wm.app_workspace("Sage")
    for conversation, prompt in (("thr_a", "Add a margin column"), ("thr_b", "Sort by P&L")):
        ws.append_history({"type": "user", "text": prompt}, conversation)
        ws.append_history({"type": "agent", "kind": "text", "text": "Working."}, conversation)
        ws.append_history({"type": "agent", "kind": "tool", "tool": "bash", "detail": _WROTE},
                          conversation)
        ws.append_history({"type": "done", "ok": True, "decision": "built"}, conversation)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app)


def _run(payload: dict) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(payload),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---- the read the drawer makes -----------------------------------------------------------------


def test_the_drawers_read_leaves_out_what_each_tool_was_called_with(tmp_path: Path, monkeypatch):
    """Every row, every other field, and none of the tool inputs.

    The row count is half the claim: the client groups the log into runs (`SW.buildRuns`), so a
    read that dropped rows to get small would drop builds off a list whose whole job is to hold
    every build."""
    client = _client(tmp_path, monkeypatch)

    whole = client.get("/api/project/history").json()["history"]
    lean = client.get("/api/project/history?detail=off").json()["history"]

    assert [r["type"] for r in lean] == [r["type"] for r in whole]
    assert [r.get("text") for r in lean] == [r.get("text") for r in whole]
    assert all(r.get("detail", "") == "" for r in lean)
    # And it says where each one went, rather than leaving a hole nobody can fill.
    assert [r["detailRow"] for r in lean if "detailRow" in r] == [2, 6]


def test_it_leaves_the_row_where_it_was_so_the_card_can_go_back_for_it(tmp_path: Path, monkeypatch):
    """`detailRow` is a position in the log, and the route answers by it. Byte-identical to what
    the whole read carried, or the fold shows something the transcript does not."""
    client = _client(tmp_path, monkeypatch)
    whole = client.get("/api/project/history").json()["history"]

    folded = [r for r in client.get("/api/project/history?detail=off").json()["history"]
              if "detailRow" in r]
    # Which rows fold is the test above's fact. What this one needs is that any folded at all,
    # since a listing that stopped carrying `detailRow` leaves the loop below reading nothing and
    # asserting nothing (#267).
    assert folded, "the folded listing carried no detailRow"
    for row in folded:
        got = client.get(f"/api/project/history/row/{row['detailRow']}").json()["detail"]
        assert got == whole[row["detailRow"]]["detail"] == _WROTE


def test_a_row_the_log_no_longer_has_is_a_404_rather_than_an_empty_tool_call(
        tmp_path: Path, monkeypatch):
    """The stop button truncates this log, so a drawer read before a revert can ask for a row that
    has since gone. `""` is what a tool nobody recorded the input of already says, and answering a
    missing row with it would draw an empty box under a card that ran something."""
    client = _client(tmp_path, monkeypatch)

    assert client.get("/api/project/history/row/999").status_code == 404
    assert client.get("/api/project/history/row/-1").status_code == 404


def test_the_transcripts_own_read_still_carries_every_tool_input(tmp_path: Path, monkeypatch):
    """Only the drawer asks the lean way. Build's transcript is per conversation (ADR-0005), it is
    a fraction of the log, and it draws these cards where they are read — so it keeps what it
    always had, and the default is unchanged for every other caller too."""
    client = _client(tmp_path, monkeypatch)

    named = client.get("/api/project/history?conversation=thr_a").json()["history"]
    assert [r["detail"] for r in named if r.get("kind") == "tool"] == [_WROTE]
    assert not any("detailRow" in r for r in named)

    default = client.get("/api/project/history").json()["history"]
    assert [r["detail"] for r in default if r.get("kind") == "tool"] == [_WROTE, _WROTE]


def test_the_drawer_pays_a_fraction_of_what_it_used_to(tmp_path: Path, monkeypatch):
    """The two halves multiply, and the number is the point of the change — so it is asserted
    rather than described. Held as a ratio because the fixture is allowed to grow.

    Elision and compression are measured apart as well as together, because either one silently
    turning off would still leave the other looking like a win."""
    client = _client(tmp_path, monkeypatch)
    plain = {"accept-encoding": "identity"}
    size = lambda r: int(r.headers["content-length"])

    # What the drawer used to ask for, and what it asks for now.
    before = size(client.get("/api/project/history", headers=plain))
    after = size(client.get("/api/project/history?detail=off",
                            headers={"accept-encoding": "gzip"}))
    elided = size(client.get("/api/project/history?detail=off", headers=plain))

    assert elided * 4 < before, f"elision saved almost nothing: {elided} of {before}"
    assert after * 3 < elided, f"compression saved almost nothing: {after} of {elided}"
    # 15x on a fixture this small; 82x measured on a real 432-row log, because a bigger log holds
    # more of the repetition gzip lives on. The floor is what is asserted, not the headline.
    assert after * 15 < before, f"the drawer's read is only {before / after:.0f}x smaller"


# ---- and what is left goes over the wire compressed ---------------------------------------------


def test_the_json_is_compressed_on_the_wire(tmp_path: Path, monkeypatch):
    """Nothing here was compressed before: Domino's nginx gzips text/html, and every route in this
    app answers application/json. The client decompresses, so the header and the declared length
    are what can be asserted — `content` is the same either way, which is the whole idea."""
    client = _client(tmp_path, monkeypatch)

    zipped = client.get("/api/project/history", headers={"accept-encoding": "gzip"})
    plain = client.get("/api/project/history", headers={"accept-encoding": "identity"})

    assert zipped.headers["content-encoding"] == "gzip"
    assert "content-encoding" not in plain.headers
    assert int(zipped.headers["content-length"]) * 3 < int(plain.headers["content-length"])
    assert zipped.json() == plain.json()


def test_every_stream_declares_the_media_type_that_keeps_it_out_of_the_compressor():
    """The one hazard the compressor brings, held by the thing that prevents it.

    gzip holds bytes back until it has a block to write, so a compressed SSE turn would arrive in
    silence and then all at once — a build stream that looks hung. Starlette excludes
    `text/event-stream` and nothing else, which makes that declaration load-bearing on every
    streaming route rather than a formality. A new stream that forgot it would not fail; it would
    stall, on the slowest turn somebody ran."""
    from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES

    assert "text/event-stream" in DEFAULT_EXCLUDED_CONTENT_TYPES

    for source in (_APP_PY, _SHIM_PY):
        text = source.read_text()
        for match in re.finditer(r"return StreamingResponse\(", text):
            call = text[match.start():match.start() + 500]
            assert 'media_type="text/event-stream"' in call, (
                f"{source.name}: a StreamingResponse near offset {match.start()} does not declare "
                "text/event-stream, so GZipMiddleware will buffer it"
            )


def test_the_compressor_is_actually_installed():
    """A middleware that is written down but not added compresses nothing, and every assertion
    above about a header would still pass on a client that never asked for one."""
    from starlette.middleware.gzip import GZipMiddleware

    import sage.orchestrator.app as appmod

    assert any(m.cls is GZipMiddleware for m in appmod.control_app.user_middleware)


# ---- the card that goes back for its own row ----------------------------------------------------


@needs_node
def test_a_folded_card_reads_nothing():
    """The saving is the whole reason this exists. A card that fetched on render would put the
    bytes back one request at a time, and add a request per tool row for the privilege."""
    step = _run({"block": {"type": "sandbox_run", "label": "Ran bash", "detailRow": 7, "code": ""}})

    assert step["calls"] == []
    # But it still offers the chevron: a deferred row has to count as detail, or the one card worth
    # a fetch is the one card nobody can open.
    assert step["opens"] is True


@needs_node
def test_opening_a_card_reads_the_row_the_list_left_out():
    step = _run({"block": {"type": "sandbox_run", "label": "Ran bash", "detailRow": 7, "code": ""},
                 "open": True})

    assert step["calls"] == ["GET /project/history/row/7"]
    assert step["code"] == "cat > src/App.tsx <<EOF\nexport default App\nEOF"


@needs_node
def test_a_card_that_carries_its_own_input_asks_for_nothing():
    """Build's transcript reads with the detail in place, and those cards must not start making a
    request each. `detailRow` is what tells the two reads apart, and only one read sets it."""
    step = _run({"block": {"type": "sandbox_run", "label": "Ran edit", "code": "src/App.tsx"},
                 "open": True})

    assert step["calls"] == []
    assert step["code"] == "src/App.tsx"


@needs_node
def test_a_failed_row_read_says_so_and_offers_the_way_back():
    """Not an empty code box, which is what a tool with no recorded input draws — the two are the
    same empty string and the person cannot tell them apart. Said as a fact about the READ, with
    the way back beside it, which is the rule the drawer's own failed state states (#90)."""
    step = _run({"block": {"type": "sandbox_run", "label": "Ran bash", "detailRow": 7, "code": ""},
                 "open": True, "rowFails": True})

    assert "Couldn't read what this ran." in step["words"]
    assert "Try again" in step["words"]
    assert step["code"] is None


@needs_node
def test_try_again_reads_once_more_rather_than_twice():
    """The read is armed by the card holding nothing, and Try again works by putting it back to
    holding nothing — so a button that ALSO fetched would fetch twice for one click. Two calls
    here: the one that failed, and the one that worked."""
    step = _run({"block": {"type": "sandbox_run", "label": "Ran bash", "detailRow": 7, "code": ""},
                 "open": True, "rowFails": 1, "retry": True})

    assert step["calls"] == ["GET /project/history/row/7"] * 2
    assert step["code"] == "cat > src/App.tsx <<EOF\nexport default App\nEOF"
