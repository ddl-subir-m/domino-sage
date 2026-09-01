---
status: accepted
revises: ADR-0009 (Build's Chat half is no longer the whole Conversation)
---

# Build shows one app's Lead-in, not the whole Conversation

[ADR-0009](0009-one-conversation-build-is-a-view.md) made a Conversation one transcript and Build a
view over it, and it drew one asymmetry: Build's *build* turns are the selected app's, because Build
has a preview bound to one app, while Build's *Chat* turns are the whole Conversation, because that
half was what the ticket existed to stop losing. That second half was right for the failure it was
fixing — a Build pane with none of the analysis behind it — and wrong once a Conversation drives
more than one Built App. Open the second app and the questions that planned the first one sit above
its plan card, on a screen that cannot preview them and against work they did not produce.

We decided that **Build shows the selected app's Lead-in**: the Chat turns after the previous
handoff and before that app's first build turn. Chat is unchanged and still shows every app the
Conversation drove, folded one row per run. The asymmetry ADR-0009 introduced therefore stops being
half an asymmetry: in Build, both halves are now one app's.

## Considered options

A Chat turn carries no app id. The chat log holds `user`, `agent`, `artifacts`, `done`, `error`,
`stopped` and `handoff-suggest`, and not one of them names a Built App, so relevance has to be
derived rather than read. We rejected **tagging turns going forward**, which does nothing for
turns already on disk and needs a new idea — "which app is this question aimed at" — that the
product does not otherwise have; and we rejected **showing the plan card alone**, which is the
split view with extra steps.

The derivation left is the handoff, and it can be read in two directions. **Forward** gives a turn
to the app of the *next* handoff after it, reading Chat as the talk that plans the build that
follows. **Backward** gives it to the app of the *last* handoff before it, reading Chat as the talk
that follows the build that is live. We took forward, because it is the product's own story — you
think in Chat, then you hand off with a plan — and because `test_build_after_a_handoff_shows_the_chat_turns_above_the_plan_card`
already puts the Chat turns above the plan card, which is the forward reading written down.

Forward is not always right. If one turn refined the app you are looking at and the next planned the
app you are not, both land under the app you are not, and no rule recovers the difference because
nothing in the data holds it. The fold below is the mitigation, not a fix.

## Consequences

**Nothing hidden is hidden silently.** Every gap the cut leaves draws a fold in place, named with the
app whose Lead-in it holds — "2 turns about P&L report" — and it opens where it sits. One fold per
gap rather than one at the top, because the transcript is ordered and a single fold would put the
turns somewhere they never were. The fold is view state: it closes on an app switch and on reload,
because "show me those two turns" is a glance, not an answer a person gives once.

**A turn we cannot place is shown, never hidden.** Three cases have no Lead-in to belong to: a turn
after the last handoff, a turn written before the `at` stamp existed, and every turn when no app is
selected. All of them draw under whatever app is on screen. This is what the build half has always
done with a row that carries no app — `test_an_adopted_row_with_no_app_is_not_dropped` — and it
keeps the failure direction pointed away from the blank pane ADR-0009 was filed about.

**An app with no build turns applies no filter.** Since [#74](https://github.com/ddl-subir-m/domino-sage/issues/74)
a person can start a Built App inside a Conversation already full of talk. That app has no handoff,
so it has no Lead-in, and the strict reading would leave it with the tail alone. It shows the whole
Chat half instead, until its first build turn gives it a boundary. The transcript shrinks at that
moment; the fold is what stops the shrink being a disappearance.

**No new preference.** `unified` means this now. This is a defect in unified, not a taste, and
`split` already answers "show me this app alone". A third view would be a third render path to hold
green for the same question.

**The server read does not change.** `conversation_history` still scans every app in the Project and
still returns both halves merged; the cut is applied in the browser, in the same function that
already drops another app's build rows. Nothing migrates, and Chat's caller reaches the same code
with no app to filter on.
