"""Health — the seven Problems a creator is owed, composed into one payload (ADR-0027).

A [[Problem]] is a condition Sage already knows will make the person's next act fail, or make it
silently do something other than what it says. Sage knew all of these before this module
existed and told only the log and `/api/diag`. Nothing here probes anything: every input is
something a boot Preflight, a turn or a diagnostics read already paid for, so composing them costs
one dict lookup each.

Pure functions on purpose, exactly as `preflight.py` is: each takes an already-fetched input and
returns sentences, so the whole set is decidable in a test with no gateway, no sidecar and no
`domino_data`. The one caller owns its own I/O and its own failure handling — and owes every read a
`try`, because ADR-0027's rule for this route is that it always answers.

Two rules from ADR-0027 are implemented here rather than described:

**The line on silence.** Failing to reach a DEPENDENCY is a Problem, because there the failure to
check *is* the fault. Failing a sub-listing behind a dependency that answered is not, and neither is
a listing that arrived empty. That line is drawn at the call sites `preflight_slots` already has, and
reaches this module as `reached`; nothing here re-decides it.

**Survival.** A Problem must appear in two consecutive Preflights before it is said, so the workspace
that reports itself running a second before its proxy serves does not light the chip. `survivors`
is that rule, and it is a pure function over the previous Preflight's ids.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator import brand

# Who owns the remedy. Not a severity: every Problem here lands on the creator's build, and this
# says which of the two of them can do something about it. A Problem with both owners sorts as
# `you`, because one the reader can act on belongs in the reader's own group.
OWNER_YOU = "you"
OWNER_ADMIN = "admin"

# The one Problem that is not keyed on something the platform named, so its id is written here as a
# literal. Constant on purpose and load-bearing: the toast fires once per id per session and
# survival is counted per id, so an id carrying the commit count or the branch head would mint a
# new Problem on every commit — re-toasting, resetting the count to zero, and never surviving two
# consecutive Preflights. The Problem would then never report at all (ADR-0064).
UNSENT_WORK = "workspace-unsent-work"

# The agents `opencode.json` defines, one per mode plus the architect the plan path hands to. Held
# here as a tuple rather than read back off the file, because the failure being reported is exactly
# "the file was not loaded": a check that read the same file the check is about would agree with a
# deployment that never opened it.
SAGE_AGENTS: tuple[str, ...] = (
    "sage-chat", "sage-ask", "sage-plan", "sage-architect", "sage-implement",
)

# The direct vendor keys OpenCode auto-detects and can reach a model with WITHOUT going through the
# shim's provider. Held here for the same reason `SAGE_AGENTS` is: the caller reads the environment
# and this module stays pure, and the failure being reported is one where the shim never ran, so the
# names cannot be sourced from the wiring that was skipped. `service.py` holds its own copy for the
# turn-summary it computes; that copy goes when the event does (#474).
VENDOR_KEYS: tuple[str, ...] = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")


@dataclass(frozen=True)
class Problem:
    """One condition, one sentence, one remedy, one owner.

    `id` is stable across Preflights because two things key on it: the toast fires once per Problem
    per session, and survival is counted per Problem rather than per Preflight.

    `body` is the platform's own words, quoted and never rewritten — the half a person forwards to
    whoever owns the remedy. It is absent rather than empty when the platform said nothing, so a
    reader is never shown a quotation with nothing in it.
    """

    id: str
    message: str
    fix: str
    owner: str
    body: str | None = None

    def to_dict(self) -> dict:
        out = {"id": self.id, "message": self.message, "fix": self.fix, "owner": self.owner}
        if self.body:
            out["body"] = self.body
        return out


def slot_problems(slots: dict) -> list[Problem]:
    """The model slots that will not answer, from the boot Preflight's own verdict.

    The sentences are `preflight.py`'s, carried through rather than rewritten: the same fault
    already reads one way in the log and would read another here, and two wordings for one fault is
    how a person ends up believing they are two faults. The halves come apart because the drawer
    renders the remedy on its own line; `message` in the Preflight payload is still the two joined,
    which is what the log line and the Rail read.

    Owner is the creator even though an administrator owns half the remedy, and the message keeps
    that half. A different model is a control the reader has in front of them.
    """
    return [
        Problem(
            id=f"slot:{s['slot']}",
            message=s["fault"],
            fix=s["fix"],
            owner=OWNER_YOU,
        )
        for s in (slots.get("slots") or [])
        if s.get("slot") and s.get("fault")
    ]


def gateway_problem(slots: dict) -> Problem | None:
    """The gateway itself would not answer, so no model call can be made at all.

    Keyed on `reached`, not on `state`: since #21 `unreachable` is also the state when the slots
    resolved and only the endpoint listing behind them failed, which is a sub-listing behind a
    dependency that answered and stays silent. `reached` is recorded at the one call site that can
    tell those apart. Absent — a Preflight that has not run yet, or one skipped because this
    deployment has no gateway to resolve against — is not evidence of anything, so it says nothing.
    """
    if slots.get("reached") is not False:
        return None
    return Problem(
        id="gateway",
        message=brand.text(
            "{assistantName} can't reach the {llmGateway}, so nothing will run until that's "
            "fixed."),
        fix=brand.text("Ask an administrator to check the {llmGateway}."),
        owner=OWNER_ADMIN,
        body=slots.get("error") or None,
    )


def binding_problems(bindings: dict) -> list[Problem]:
    """The Resources this app records that will not serve it, from the Binding Preflight.

    Keyed on kind and id together, because one Resource can be recorded under two kinds and the
    toast must not treat them as one Problem.
    """
    return [
        Problem(
            id=f"binding:{b['kind']}:{b['id']}",
            message=b["fault"],
            fix=b["fix"],
            owner=OWNER_YOU,
        )
        for b in (bindings.get("bindings") or [])
        if b.get("kind") and b.get("id") and b.get("fault")
    ]


def port_problem(ports: dict) -> Problem | None:
    """The build agent dials a port this process does not serve, so every call skips the shim.

    A STANDING misconfiguration, read from two config values and never from a turn (#475). The turn
    that records zero model calls is evidence of this condition, not the condition: the next turn
    dials the same wrong port, which is what ADR-0064's test for a Problem asks and what an event
    ("a turn bypassed the shim") fails. So this says nothing about any turn, and is decidable at
    Preflight with no turn having run.

    What it costs is not that calls fail — on a workspace with direct vendor keys they succeed, and
    that is the whole difficulty: OpenCode reaches the vendor itself, answers arrive, the turn looks
    normal, and routing, sovereignty and cost tagging were all silently skipped. The message says
    what is actually lost rather than predicting a failure the reader may never see.

    Silent when the configured port could not be read at all. `match` is false in that case too —
    `None` never equals a port number — and reporting it would turn "we could not check" into "this
    is broken", which is the one thing a Preflight may not do.

    The id is the constant `ports` and carries no port number in it. Load-bearing: the toast fires
    once per id per session and `survivors` counts survival per id, so an id built from the ports
    would mint a new Problem the moment either port changed — re-toasting, resetting survival to
    zero, and never being reported at all (ADR-0064).
    """
    control, configured = ports.get("control_port"), ports.get("base_port")
    if configured is None or control is None or configured == control:
        return None
    # Their presence is the likely reason inference still worked, and naming them is what turns "a
    # port is wrong" into "and here is why nothing looked broken". They travel in `body` as the bare
    # names the deployment set, un-rewritten, because the administrator who owns this remedy is the
    # one who set them and is the person this half gets forwarded to.
    keys = ports.get("vendor_keys") or []
    return Problem(
        id="ports",
        message=brand.text(
            "{assistantName} is on port {control}, but the build agent calls {configured}, so its "
            "model calls never reach the {llmGateway}: nothing routes them, and {platformName} "
            "doesn't record them."
            + (" Answers may still arrive, because this workspace has direct vendor keys set."
               if keys else ""),
            control=control, configured=configured),
        fix=brand.text("Ask your administrator to check this deployment's port configuration."),
        owner=OWNER_ADMIN,
        body=", ".join(keys) or None,
    )


def agent_problem(agents: list[dict] | None) -> Problem | None:
    """The agent definitions did not load, so every mode is running one that denies nothing.

    The worst of them, and the reason ADR-0027's test has a second clause: this one does not fail
    loudly. A question can change files, a build looks like it ran, and nothing says the rules it
    was meant to follow were never applied.

    `None` means the agent runner is not up yet or the query failed — not checked, so nothing is
    said. Reporting `None` as missing would light the chip on every boot.
    """
    if agents is None:
        return None
    # Every short scalar in the row, not `name`. `agent_summaries` deliberately refuses to pin an
    # identifier key — whatever OpenCode calls it survives as one of the values, and a response
    # keyed by agent name arrives as `key` rather than `name`. Picking one key would report five
    # missing agents on a deployment where all five loaded, which is the worst sentence in the set.
    named = {str(v) for a in agents for v in a.values()}
    if all(agent in named for agent in SAGE_AGENTS):
        return None
    return Problem(
        id="agents",
        message=brand.text(
            "{assistantName}'s agent rules didn't load, so modes may not behave as expected."),
        fix=brand.text("Ask your administrator to check this deployment's agent configuration."),
        owner=OWNER_ADMIN,
    )


def data_library_problem(detail: str) -> Problem | None:
    """This interpreter cannot read inside a Data Source, so an app that reads one will not run.

    `detail` is `data_library_ready()`'s answer: empty when the import works, and otherwise the
    import error itself, which is what travels in `body`.
    """
    if not detail:
        return None
    return Problem(
        id="data-library",
        message=brand.text(
            "This workspace can't query {dataSourcePlural}, so apps that need one will fail."),
        fix=brand.text(
            "Ask an administrator to check this workspace's data library."),
        owner=OWNER_ADMIN,
        body=detail,
    )


def unsent_problem(unsent: dict) -> Problem | None:
    """This workspace holds commits the {project}'s git remote has never been given (ADR-0064).

    A standing state read from git, never the event that produced it. "Your last save failed" is
    past tense — the next turn works, nothing downstream misbehaves — and the glossary rules that
    out by name. What stands is the work existing in one place only: a remote that refused once
    refuses the next turn too, so every turn from here silently does something other than what Chat
    says it does.

    One Problem for the whole workspace and no Conversation named, because a save commits the whole
    tree: naming one sends a person to re-type one thing while the rest is equally unsent.

    `unsent` is the reader's answer already taken — `{"unsent": bool, "detail": str | None}` — and
    an answer that is ABSENT says nothing. An empty dict is the route's fallback when the read
    itself failed, and "we could not check" may never be reported as "this is broken".

    `detail` is git's own refusal, quoted and never rewritten (ADR-0014). It is best-effort and
    absent after a restart: the condition is git's and survives, the words are the last attempt's
    and do not. `body` being optional is what makes that honest rather than lossy.
    """
    if not unsent.get("unsent"):
        return None
    return Problem(
        id=UNSENT_WORK,
        message=brand.text(
            "{assistantName} has committed work in this {project} that its git repository has "
            "never received, so that work exists in this workspace only."),
        fix=brand.text(
            "Every save tries again, so sending another message or leaving {chat} will retry it. "
            "If it keeps failing, check this workspace's git credentials and its access to the "
            "repository."),
        owner=OWNER_YOU,
        body=unsent.get("detail") or None,
    )


def problems(*, slots: dict, bindings: dict, ports: dict,
             agents: list[dict] | None, data_library: str, unsent: dict) -> list[Problem]:
    """Every Problem this deployment has, the creator's own first.

    Ordered rather than grouped: the drawer groups by owner, and a payload that arrives in the order
    it will be read spares the client from deciding what "first" means — which is the same reason
    the sentences are composed here (ADR-0014: a client that composed them would put un-branded
    nouns on an OEM screen).
    """
    found = slot_problems(slots) + binding_problems(bindings)
    for one in (unsent_problem(unsent), gateway_problem(slots), port_problem(ports),
                agent_problem(agents), data_library_problem(data_library)):
        if one is not None:
            found.append(one)
    return found


def survivors(previous: set[str], current: list[Problem]) -> list[Problem]:
    """The Problems this Preflight found that the previous one found too.

    Domino reports a workspace running before its proxy serves, and a permission cache blips, so a
    single sighting is not enough to say anything about. Two consecutive ones are. A Problem that
    clears in between starts its count over, which is what "consecutive" buys over a tally: a fault
    that flickers once an hour never accumulates.
    """
    return [p for p in current if p.id in previous]
