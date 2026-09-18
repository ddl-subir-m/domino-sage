"""ADR-0058's amendment: the SQL tool reaches BOTH Chat data lanes (#408, #402).

The two lanes differ by one tool and share one hole. They also reach their tool sets by two unlike
mechanisms, and that is the whole reason this file exists:

    read-only  DENYLIST  — `READ_ONLY_DENIED` strips write and shell; anything unnamed survives
    artifact   ALLOWLIST — `enforcement.py`'s explicit set; anything absent is stripped

So "add it to both" is two different edits, and only one of them is a list of names. A change that
gave `live_read_query` to the read-only path and stopped would ship a `data_artifact` turn that
still cannot compute — which is #402, filed.

The read-only half is asserted rather than assumed. It needs no edit, and "needs no edit" is a claim
about a denylist somebody could later add to; a test is the difference between that being true and
it merely having been true when this was written.
"""

from __future__ import annotations

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.router.phase_classifier import READ_ONLY_DENIED
from sage.shim.enforcement import EnforcementShim

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b", sovereign_implement="sovereign-8b", sovereign_ask="sovereign-8b",
    plan="strong-vendor", implement="cheap-vendor", ask="ask-vendor",
)

# Both spellings of one tool. OpenCode prefixes an MCP tool with its `opencode.json` key before
# offering it to the model, so a lane that admits only the bare name strips the tool exactly where
# it is served over MCP — which reads, from the model's side, like the tool not existing.
BARE = "live_read_query"
NAMESPACED = "sage-live-read_live_read_query"


def _req(*names: str) -> dict:
    return {"messages": [],
            "tools": [{"type": "function", "function": {"name": n}} for n in ("read", *names)]}


def _offered(control: ModelControl, *names: str) -> set[str]:
    shim = EnforcementShim(control, CATALOG, FakeGatewayClient())
    list(shim.handle(_req(*names), {}))
    return {t["function"]["name"] for t in shim.gateway.seen[-1][0]["tools"]}


def test_a_data_artifact_turn_is_offered_the_sql_tool_by_both_of_its_names():
    """The half #402 owns, and the half a one-line change would have missed.

    An artifact turn that cannot compute is worse off than a read-only one that cannot: it has
    already been labelled `data_artifact`, already armed `artifact_write`, and already been told by
    its own prompt to write the table with it. Then it has no number to put in one (#425).
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    chat = control.arm_chat("thr_artifact")
    artifact = control.arm_chat_artifact()
    names = _offered(control, "bash", "artifact_write", BARE, NAMESPACED)
    control.disarm_chat_artifact(artifact)
    control.disarm_chat(chat)

    assert BARE in names, "the allowlist must name the bare tool"
    assert NAMESPACED in names, "and the namespaced one, which is a SECOND entry and not a restatement"
    assert "artifact_write" in names, "the lane keeps what it already had"
    assert "bash" not in names, "and gains nothing it did not"


def test_a_read_only_turn_is_offered_the_sql_tool_because_nothing_takes_it_away():
    """The other lane, and it needed no edit — asserted, because "no edit" is the easiest kind of
    claim to be wrong about later.

    A denylist gives a new read tool to this lane for free. What would take it away is somebody
    adding it to `READ_ONLY_DENIED`, and the test below says why nobody should.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    chat = control.arm_chat("thr_read_only")
    read_only = control.arm_read_only("question")
    names = _offered(control, "bash", "write", BARE, NAMESPACED)
    control.disarm_read_only(read_only)
    control.disarm_chat(chat)

    assert BARE in names and NAMESPACED in names
    assert "bash" not in names and "write" not in names, (
        "the read-only guarantee is untouched: #400 measured 400.2s across 14 shell calls as the "
        "cost of a lane that keeps one")


def test_the_sql_tool_is_not_named_by_the_read_only_denylist():
    """Said directly, not only through a turn, because this is the line somebody would add.

    `live_read_query` runs SQL, and "runs SQL" reads like something a read-only lane should refuse.
    It is not: the statement runs under a read-only warehouse role, the agent never receives a
    shell, a token or a gateway URL, and ADR-0058 chose this tool over keeping `bash` precisely so
    that the lane's guarantee could stand. Denying it here would restore the hole this closed while
    looking like caution.
    """
    assert BARE not in READ_ONLY_DENIED
    assert not {BARE, NAMESPACED} & set(READ_ONLY_DENIED)


def test_an_ordinary_build_turn_is_not_given_the_sql_tool_by_this_change():
    """Neither lane is Build, and widening one gate must not widen its neighbour.

    A Build turn has a shell already, so this would grant nothing — but it would also put a
    `data_use`-recording, value-disclosing path on a turn none of that wiring was built for.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    names = _offered(control, "write", "artifact_write", BARE)

    assert "artifact_write" not in names, "scoped to the artifact lane, unchanged"
    assert "write" in names, "and a Build turn still builds"
