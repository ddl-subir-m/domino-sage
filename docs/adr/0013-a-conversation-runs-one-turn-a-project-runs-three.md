---
status: accepted
---

# A Conversation runs one turn, a Project runs three

Sage refuses a second turn anywhere in a Project while one is running. #79 asked whether lifting
that limit — parallel Chat — is the product, and put the question as concurrency or nothing. We
decided the limit comes off as **two independent dials**, and that both are wanted:

- **N** — a Conversation accepts turns while one is running and runs them **in order**.
- **K** — a Project runs up to **three** Conversations' turns **at once**.

Today N is 0 and K is 1. The Conversation is the unit of both.

The reason to write this down is that #79's four blockers gate **K only**. Nothing on that list
touches N. Read as one question, the whole feature waits behind days of control-layer work. Read as
two dials, the half that fixes the incident that started this ships without any of it.

## The question #79 could not ask

#79 was written after a live failure on 2026-08-27: a Chat question about a Dataset would not
finish, Chat had no Stop, the second question came back as *"A build is already running"*, and New
conversation landed on the same sentence. `f19c1e9` fixed the control gap — Chat has a Stop, the
composer disables with a bar that says why. It did not lift the limit.

#79 then went looking for what lifting it would cost, found four control-layer blockers, and closed
with *"is parallel Chat something we want, or is one-turn-at-a-time the product?"* — arguing that
Sage is one container on one project volume, so two turns are two agents competing for CPU with the
preview and the shim, and a person who asks three questions gets three slower answers.

That argument is sound and it answers a question nobody had. **The 2026-08-27 incident was not
someone wanting two answers at once.** It was someone with a second question in their head and
nowhere to put it. Wall-clock was never the complaint; the dead composer was.

So there are two needs, and they are not the same need:

| Need | Dial | Cost |
|---|---|---|
| "I have a second question while this one is composing." | N | Days. No blockers. |
| "I want to work in another Conversation while this one runs." | K | All six blockers. |

Both are real. Only the second is what #79 priced.

## The Conversation is the unit

The unit of concurrency is the **Conversation**: one running turn each, its own queue of pending
turns behind it. `K` is how many Conversations have a turn in flight.

The glossary forbids the obvious word. `CONTEXT.md` says to avoid *session* for a Conversation,
because OpenCode already owns that noun for the harness object. That is not pedantry here — under
K > 1 there are several OpenCode sessions live at once, and conflating the two words makes the
control layer impossible to talk about. A Conversation **has** an OpenCode session. It is not one.

The Conversation is the right unit because `thread_id` already carries everything a turn reads or
writes — the OpenCode session, the transcript (`store.append_history`), the context chips
(`store.read_context`), the Artifacts under `examples/<threadId>/`, and the write allowlist
(`chat_path_allowed`). #79 established that the data layer is already per-Conversation and needs no
work. Choosing any other unit would throw that away.

The rejected alternative is Conversation × mode — a Chat turn and a Build turn running together
inside one Conversation. That means two agents editing one Built App at once, which is a race
somebody would have to invent a reason to want. Turns are serial **inside** a Conversation and
parallel **across** them. This makes ADR-0009 and #50 load-bearing rather than adjacent: if Build
is a view of a Conversation, the unit is coherent; if it is a separate place, it is not.

**The Conversation orders. It does not exclude.** A Conversation never owns a Built App —
`CONTEXT.md` says two Conversations may drive one — so "two Conversations, two Build turns" does not
mean two independent builds. They may be building the same app, and nothing in the blocker list
stops them: both turns are *legitimately* allowed to write that app's files, so they would interleave
edits and each typecheck over the other's half-written tree.

So there are two rules, not one:

- **A Conversation runs one turn at a time.** Ordering.
- **A Built App is built by one turn at a time.** Exclusion.

Most of the second already exists. Apps live in `apps/<appId>/` (#67, closed), a turn pins its app
for its whole life so a rail switch mid-build cannot move it (`_pin_turn_app`, #77), and **Build's
revert is already scoped to that app** — `Project.snapshot` returns
`TurnSnapshot(self.app_for_turn().path)`. What is missing is the exclusion itself, which the
project-wide turn lock provides today by accident and would stop providing the moment it is removed.

## Why the blockers do not gate the queue

The claim that N is free had to be attacked before it could be believed. Three attacks, all
survived:

**Turn A's revert finishes before the lock is free.** `revert_denied_writes` runs at
`service.py:4431`; the `yield done` that releases the lock is at `:4454`. A queued turn cannot
start into a revert that is still walking the tree.

**Turn A's teardown cannot disarm turn B.** `chat_stream` releases the lock at `done`, so
`_chat_stream`'s `finally` — `disarm_chat`, `disarm_web`, `_after_chat_turn` — runs after a queued
turn may already have armed. Those disarms are token-guarded (`if self._chat_token is token`), so a
stale disarm from a finished turn is a no-op. The control layer already survives out-of-order
teardown; #79's blocker 2 is a statement about genuine overlap, not about this.

**The aftercare already loses safely.** `_maybe_compact_chat` and `_flush_chat_save` both
`acquire(blocking=False)` and skip when they lose. Their comments say so outright.

Two paths deserve naming because they look like exceptions and are not. On a Stop
(`service.py:~4256`) and on a timeout or stall (`:~4310`), `_chat_stream` yields `done` and returns
**without** running the revert. That is deliberate for Stop — *"Chat reverts nothing… a chart
already written under `examples/` is an answer that still reads."* It means the two abnormal exits
free the lock earlier relative to their own cleanup, which the token guards and the non-blocking
acquires already cover.

So: **turns still run one at a time under a queue.** No shared-tree revert hazard, no per-turn
control plane, no per-Conversation cwd. N is a scheduling change, not an isolation change.

## What the queue costs that the blockers do not cover

Three costs are created by N itself and appear on no blocker list.

**A wedged lock becomes a silent forever-wait.** `_turn_wedged` holds `_turn_lock` permanently by
design (#39, closed): *"a wedged workspace someone can restart beats a corrupted working tree
nobody can detect."* Today that produces an immediate refusal naming the remedy. A queue behind it
produces a spinner that never resolves. So the queue must **refuse at enqueue when wedged**, and
must **fail every pending turn loudly** if a wedge happens while they wait. This requires
`/api/project/build/state` to start reporting `wedged`: `turn_busy()` deliberately returns
`locked() and not wedged`, so the client can currently see "not running" and cannot see why.

**Aftercare starves.** Compaction and the git save skip when they lose the lock. A human typing
loses that race rarely. A queue firing at `done` wins **every time**. Compaction is per-OpenCode-
session, so this bites a deep queue in *one* Conversation: the turns in the middle run against a
session that should have been summarised and was not. It is bounded — the last turn in the queue
compacts — and it is a real reason not to encourage very deep queues.

**The transcript would lie about context.** Chips are read server-side at run time
(`store.read_context(thread_id)`); the user bubble echoes attachments client-side at send time
(`store.js:~2409`). Queue those apart and the bubble shows chips the turn did not use. See the
snapshot rule below.

## Six blockers, not four

#79 lists four things that stop K > 1. There are six. The list has a shape worth stating, because
the shape is the argument for why this is real work and not a flag:

**Everything on it is something an operating system gives Claude Code for free, and Sage does not
have.** Sage is one `Orchestrator` → one `Project` → one of everything.

| What a process gets free | What Sage has | #79 |
|---|---|---|
| Its own working directory | one `.sage/chat-work` (`threads.py:490`) | blocker 3 |
| Its own file writes | a whole-tree revert that deletes (`threads.py:380`) | blocker 1 |
| Its own credential environment | one `ModelControl` arming, last-writer-wins | blocker 2 |
| Its own signal target | **five** single fields on `Project`, not two (below) | blocker 4 |
| Its own checkout, or an accepted mess | **one `_save_to_git` on one working tree** (`:6203`) | **missing** |
| No preview to share | **one `ViteSupervisor`** on the workspace path | **missing** |

Blocker 5 — the git working tree — is not cosmetic, and it is the one most likely to be missed
because the code that protects it looks like it already handles concurrency. `_flush_chat_save`
takes the turn lock rather than testing it, with the reason written down: *"a turn that starts
partway through gets its half-written files committed."* Under K > 1 there is **always** a turn
running, so that protection evaporates at exactly the moment it starts mattering.

Blocker 4 is larger than "stop targeting". `stop_requested` (`service.py:1811`) and
`active_session_id` (`:1795`) are the two it is usually described by, but `_pin_turn_app` also
writes `turn_app`, `turn_attached` and `turn_tree_baseline` — all single fields on `Project`. Under
K > 1 two Build turns clobber each other's pin, and every later question the pin answers (*which app
do I revert, log to, repair afterwards?*) gets the other turn's answer. Five fields, all keyed by
Conversation.

Blocker 1 is narrower than it looks, in Build's favour. The whole-tree diff is the **Chat** revert
(`revert_denied_writes`). Build already reverts per app through `Project.snapshot`. So blocker 1 is
Chat-only work, and Build's existing shape is the model for what Chat's should become.

Blockers 1 and 3 also do not cover each other, which is easy to assume they do.
`_SKIP_SNAPSHOT_PARTS` (`threads.py:466`) excludes `chat-work` from the snapshot, so two agents
colliding in the shared work directory are invisible to the revert that is supposed to be the
safety net.

## The rules

**Queue depth (N).** Per Conversation, uncapped. It lives in memory on the server and dies with the
process. A pending turn is an intention, not a commitment — the UI says so when it accepts one.
Persisting to disk was rejected: it buys little under a memory-only failure model, and it collides
with the one repair procedure Sage has, since a workspace restart is the remedy for a wedge and a
persisted queue would replay the turns that were stuck behind it — possibly the ones that caused it.

**Concurrency (K).** Capped, configurable, default 3. Unbounded was argued for on the grounds that
the box can be sized, and the cap serves that argument rather than fighting it: the cap is the thing
you raise. The default is low because the failure modes are asymmetric. A wait is recoverable; an
OOM takes the workspace and, under memory-only queues, every pending turn in it.

**Drain order is FIFO by enqueue time, across the whole Project.** The queues are per Conversation,
but the order they drain in is not: the oldest pending turn anywhere in the Project takes the next
free slot. At K = 1 that is the whole schedule; at K > 1 it is which pending turn fills a slot as it
frees.

A pending turn is eligible when **its Conversation is free and its target Built App is free**. The
second predicate is the exclusion rule doing its work: a second build asked for on an app that is
already building waits rather than being refused. Refusing was the alternative and is rejected for
consistency — a Workbench that queues Chat and refuses Build is a rule people have to learn instead
of guess.

Per-Conversation round-robin is the alternative and is rejected, for a reason that only holds in
Sage. Round-robin exists to stop one tenant starving another. **There are no tenants here** — a Sage
Builder belongs to one viewer (`CONTEXT.md`, *Sage Builder*: two viewers in the same Project each
have their own). Every pending turn in the Project was asked by the same person, so fairness between
Conversations is not a thing to protect; it is a thing that would reorder one person's own questions
against each other for no benefit they can perceive. "In the order you asked" is the only rule that
does not need explaining on screen.

The consequence is accepted rather than mitigated: a deep queue in one Conversation delays a single
question asked later in another. That is what asking in order means. If it becomes a complaint the
answer is a higher K, not a cleverer scheduler — reordering would buy latency for one question by
making the schedule unpredictable for all of them.

**Context: snapshot at enqueue, validate at run.** A pending turn records the chips it was written
against, **and, for a build, the Built App it was written for**. Resolving the target at run time
instead would pick up wherever the rail had drifted to — the exact failure #77 fixed for a running
turn, arriving through the queue instead. Before it runs, that snapshot is compared to the live Session context. If anything moved,
**the turn does not run** — it is surfaced as *"your context changed since you asked this"* with the
text returned to the composer. The two obvious alternatives both produce a turn that quietly did
something other than what was on screen: running against the snapshot resurrects a chip the person
deliberately removed, and running against live context makes the transcript bubble a lie. Sage's
posture is that the transcript is the receipt. A refused pending turn is worse latency and a better
product.

**Stop stops one turn. Failure advances. A wedge fails the queue.** `stopChat()` currently calls
`stopBuild()` because *"one project runs one turn, so there is one thing to interrupt"* — a sentence
that is false the moment N > 0. Stop targets the running turn of one Conversation and the queue
advances: you stopped that answer, not your other questions. A timeout or an error also advances,
because those are answers too. A wedge is the exception and fails everything pending immediately,
with the restart sentence.

**Cancel is a separate control from Stop.** A pending turn can be dropped without touching what is
running.

**The preview is unchanged.** One `ViteSupervisor`, following the rail selection. A background
build's result appears when you select that app. Per-app previews are now *possible* (#67 closed,
Built Apps have their own directories) and are deliberately not in scope: building one grows this
from "run turns at once" into "run apps at once". If it becomes a complaint it is its own issue.

## Sequencing, which is a constraint and not a preference

Two orderings are load-bearing.

**N does not wait for K.** The queue ships against the existing lock, needing only the `wedged` flag
on `build/state`. The work is not thrown away when K rises: a queue draining into K slots is the
same code with a different number.

**The wedge must not be narrowed before the tree is.** Under K > 1, one stuck Conversation killing
five healthy ones is bad, so per-Conversation wedging is obviously desirable. But #39's reasoning
holds *only while the working tree is shared* — a wedged Conversation may still be writing. Letting
others carry on around it before blocker 1 (scoped revert) and blocker 3 (per-Conversation working
directory) are done re-opens #39 as data loss instead of as a stall. A project-wide wedge is correct
behaviour until the tree is split, not a bug to be fixed early.

Blocker 5 pairs with blocker 1 and should be done with it. Fixing the revert properly means
computing *the set of paths this turn wrote*; feeding that same set to the commit is most of the
commit-lock fix, at close to no extra cost. Doing them apart means computing that set twice.

## Consequences

**One code comment is already false and gets worse.** `service.py:2101` claims *"The UI already
queues composer messages behind a live turn."* It does not — `store.js:2059` refuses
(`if (!text.trim() || state.buildRunning) return null`). The comment describes this ADR's N, written
before it existed. Anyone reasoning about the turn lock from that docstring has been reasoning from
a queue that was never built.

**`chatRunning` stops being one flag.** `store.js:87` is deliberately project-wide today, and its
comment explains why: *"a Chat turn, a Build turn and a turn another tab started are all the same
fact here."* Under N it becomes "can I send" — which is always true — and under K it becomes
per-Conversation. The single flag is not a bug being fixed; it is a correct model of K = 1 being
replaced.

**One-turn-at-a-time is recorded as a stage, not as doctrine.** Every rule here that reads like a
restriction — Chat refusing to queue behind a Build, the project-wide wedge, the single preview —
belongs to a value of K and should be re-read when K changes. That is the thing most likely to be
misread later as a decision against concurrency.
