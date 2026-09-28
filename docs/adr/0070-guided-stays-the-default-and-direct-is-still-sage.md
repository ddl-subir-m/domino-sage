---
status: accepted
extends: ADR-0052 (the LLM Gateway is the trusted enforcement point), ADR-0041 (a live read
  reaches the person without reaching the model), ADR-0058 (a Chat answer computes, and Python
  is a door the person opens)
---

# Guided stays the default, and Direct is still Sage

A person can choose how Sage works. **Guided** is today's path and the one they get until they
choose otherwise. **Direct** skips the automatic cards and the plan gate, and runs one short agent
on the existing instruction-profile cut. The two words are a way of working. They are not a mode:
Build stays Auto, Ask, Plan, and Implement, and Direct is orthogonal to those four.

## The decision

**One preference, `howSageWorks`, with two values.** `guided` is the fallback. The only accepted
value of Direct is the string `direct`. Anything else, including a missing field, is Guided.

It follows the person across Projects, in the same per-person localStorage record as the other
Work-bench preferences, and it never goes in git. The server does not read that record. The choice
is sent with the turn, on the Chat stream and the Build stream, and it does not change mid-turn. A
flip during a running turn applies to the next send.

Account settings is where it is set. The composer does not offer the choice.

**Direct changes Chat, Auto, and Implement.** It does not call `set_mode`. Ask stays read-only and
never builds. Plan, an explicit `wants_plan`, and an architecture request still plan. An approval
already skips the gate. Reset and incoming stay; they are about the working tree. A turn with no
bound source still asks which one.

**Guided's only behavior change is the approve path.** An approved unphased build reuses the
planning session. Phased builds stay one session per phase. Direct Auto never reaches that path,
because it does not gate.

## What Direct still keeps

These three are why a terminal session pointed at the provider will still be a bit faster, and why
Direct is still Sage:

- Every model call still goes through the enforcement shim.
- Sage-owned files stay untouchable (`sage_serve.py`, `static/sage/`, `.sage/` bookkeeping, and
  Chat's write fence under `examples/<threadId>/`).
- Raw rows stay off the transcript. The model receives a sample only when `.sage/samples.json`
  already says the creator shared that table ([ADR-0041](0041-a-live-read-reaches-the-person-without-reaching-the-model.md)).
  There is no second door.

A forged `direct` is still only a way of working. It keeps the gateway, the file fence, and the
row rule. It is not a grant.

## A volume cap offers Keep going (#585)

"One short agent" describes one turn, not the work. Three caps end a turn that is still working
rather than one that is stuck: Chat's ceiling, Build's shell cap, and Build's progress budget. In
Direct, each still ends the turn exactly as in Guided: the session is interrupted, the lock goes
back, and Build still checks what was written. The turn then draws a **Keep going** card. Its
button sends one fixed sentence, "Keep going from where you stopped.", as an ordinary Direct turn
into the same OpenCode session. The model keeps everything it read and did, so no state rides on
the sentence.

In Chat, Keep going replaces the findings Continue card. It does not appear beside it.

Keep going never appears on a quiet window or a repeat brake. Continuing a hang or a loop must not
be one click. Nor does it appear on the pre-edit limit: by the time that limit stops a turn, it has
already replaced the session once, and it has made no edit at all.

Rejected: holding the turn open until the person clicks. That keeps the project's turn lock for as
long as nobody answers. Also rejected: replaying the original prompt, which throws the session's
context away, and simply raising the caps, which removes the backstop for a turn nobody is watching
(#400).

Chat stays an answer. Direct explains the result. It is not a terminal echo.

## What was already true, and is not rebuilt

An open investigation already keeps the shell. A shared sample already reaches the model through
`grant.values_allowed`. Guided already drops the design essay and the platform table on some
implement turns, via `choose_instruction_sections`. Direct is a stricter cut of that same function.
The on-disk `AGENTS.md` files stay the full marked file. The short text is a profile and two agent
prompts, not a rewrite of the files a Built App was seeded with.
