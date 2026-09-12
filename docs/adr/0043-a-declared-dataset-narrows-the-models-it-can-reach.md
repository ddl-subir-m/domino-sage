---
status: accepted
supersedes: 0012 (the refusal, now that a classification exists)
---

# A declared Dataset narrows the models it can reach

ADR-0012 refused to gate any Data Source and LLM Alias pair at publish, and gave one load-bearing
reason: **nothing declared that a store was sensitive**, so a refusal could only be blanket, and a
blanket refusal refuses the ordinary case. It named the condition that would reopen it — "Sage grows
a per-Data-Source sensitivity declaration, or Domino exposes a classification Sage can read."

That condition has fired. A Domino Dataset carries freeform tags, and Sage already reads them. So a
Dataset tagged `sensitive` now narrows the models Sage will use while it is **in scope**, and narrows
the models the app Sage builds may be published against. The approved models are named by a
**Domino LLM Gateway alias group**, which an administrator curates and Sage only reads.

"In scope" is the load-bearing word and it is defined in *Which Datasets are in scope*, below.

The gate is off until an administrator configures it.

## What the promise says, and what it does not

This is an enforced control, not a strong default. A creator cannot click through it, it holds
across Chat and Build alike, and it fails closed. That is worth stating plainly because the guardrail
version of this feature — warn, then let them proceed — cannot be said out loud in the sentence the
platform is sold with, and a control that folds under the first person to disagree with it is not a
control.

But the promise has an exact shape, and overclaiming it would be worse than not making it.

**Sage honours a declared classification. It does not detect sensitive data.** A Domino tag is
self-service: anybody with write access to a Dataset can add or remove it. Sage cannot verify that
the tag is right, cannot notice data that nobody tagged, and does not read the rows to guess. What
Sage guarantees is that once the declaration exists, Sage acts on it.

**The gate holds up to publish, not past it.** A published app calls the gateway directly from the
viewer's browser — `template/react-vite/serve.py` says a bound Alias "names a model the browser
calls directly" — and there is no Sage hop in that path to intercept. So the promise is that *an app
which reads a sensitive Dataset cannot be published pointing at a non-approved model*. It is not
that a running app is prevented from calling one. Those are different sentences and only the first
is true.

**One of the two data surfaces can carry the declaration.** A Dataset has tags. A Data Source has no
classification field at all — none exists to read. Because the gate locks the whole turn rather than
one file, a Snowflake source bound beside a tagged Dataset is protected incidentally, and the real
gap is narrower than it first appears: a Project holding a sensitive Data Source and no Dataset gets
no gate. That is the warehouse case, and it is the more sensitive half. It is stated wherever the
promise is made rather than left for a reader to discover.

## The approved list is read, never written

The models approved for sensitive work are a **group on the gateway**. `GET /api/alias-groups`
carries `{name, description, alias_ids}` with full CRUD, and every alias on `/api/aliases` reports
its membership as `groups: ["FDE_models"]`. Sage already fetches that record, and already throws the
field away.

Sage reads it and never writes it. ADR-0012's strongest argument survives the reversal: the grant
was made upstream, by an administrator who registered the Alias and configured the credential, with
more context than Sage has. Sage creating its own approval group would make Sage the authority it is
supposed to be reading from. `SAGE_SENSITIVE_MODEL_GROUP` names the group to look for, because a
customer may already have one under their own name.

**It is a whitelist.** The deciding case is a model nobody has classified — an alias registered
tomorrow. A blacklist admits it by default, on the most sensitive data in the deployment, which is
the only moment the control matters. A whitelist refuses it until somebody says otherwise. The
default answer for an unknown model must be no.

**Hosting was rejected as the signal.** `provider_type` distinguishes `domino_platform` and `vllm`
from vendor providers, and deriving the safe set from it would need no configuration at all. But
"runs inside Domino" is not "approved for PII", an administrator cannot override a derivation, and
on the reference deployment it resolves to two aliases, one of which rides a Hosted GenAI endpoint
in a project Sage does not own. A gate whose safe set is computed cannot be corrected by the person
answerable for it.

The group is a label, not a permission — the schema has no access field, and the gateway will still
answer a call to a model outside the group. Sage enforces a classification the gateway only records.
That is the honest description, and it is enough, because the administrator's word is the thing
being honoured.

## Where the gate fires

Four points, three of which Sage owns.

**Model resolution**, in `llm_router.resolve`, above the Chat fork. One check before the branch, so
Chat and Build cannot disagree and no later harness can be added underneath it. This covers the
creator's pick and every turn, and Chat matters specifically because a Chat build request has
already been shown to reach gates that the Build path applies.

**The preview proxy.** `/api/llm/*` is intercepted server-side while an app is being built, so the
app Sage is writing is gated at the moment its own model call would carry sensitive rows.

**Publish.** `publish_guard` refuses an app that binds a non-approved Alias while a sensitive Dataset
is in scope, reusing `sensitive-rows-to-vendor-model` — the code the deleted implementation used for
exactly this refusal. Two lists meet here and they are not the same list: only a BOUND Alias is
pinned into the app's source, so the manifest names the model, while the declared half comes from
the wider scope — an app whose sensitive rows arrive as files under `public/data` ships the same
rows as one that also wrote a Binding down. No Conversation is consulted: an app is published, a
Conversation is not, and a chip pinned to a chat does not travel until a handoff writes it into the
manifest.

**Binding is deliberately not gated.** Refusing to attach an Alias would be the strongest point, and
it is the wrong one: a creator picking a model is not thinking about data, and a refusal there
teaches nothing about why. Binding is a declaration of intent. The explanation belongs at the point
of use.

**Filtering the app's own model list was rejected.** `askModel` refuses any alias absent from
`llm.config.ts`, so filtering that list would be defence in depth. But `_write_app_model` runs on
disk on every binding change and, as `pinned_model.py` records, "is handed no listing and cannot tell
a vendor-backed Alias from a Domino-hosted one" — deliberately. Handing it a network listing to
serve this gate would undo a decision taken for good reasons elsewhere.

**Compaction inherits the lock rather than being gated separately.** `compact_model` already calls
`llm_router.resolve`, so a locked session compacts through the locked alias for free. This is worth
naming because `chat_compact.py` holds `COMPACT_FALLBACK = "gpt-5.4"` — a vendor model — handed to
OpenCode as a resolution handle when the real alias is unlisted, safe only because the shim rewrites
`model` on every request. That invariant is now load-bearing for a governance promise, so it is
pinned by a test rather than by a comment. `COMPACT_FALLBACK` stays: the comment explaining it is
correct, and the fallback exists for a real reason.

**A Live read is out of scope, and stays that way.** ADR-0041 hands the assistant columns, row count
and path while the rows go to the Artifact and reach the person. Nothing sensitive enters a prompt,
so there is nothing here to gate. This is recorded because a later reader who sees "sensitive" and
"reads a Dataset" in one sentence will otherwise gate it, and gating it would break the one path
built to keep rows away from the model.

**A descriptor is NOT the same case, and the two are easy to read as one.** `describe()` promises a
file's shape and not its content, and its tabular branch breaks that promise on purpose: it emits
three verbatim rows, and the PDF branch a first-page snippet. Those reach the model — so unlike a
Live read they are inside the gate, and the turn carrying them is locked to an approved model like
any other. What they must not do is get written down; see *What the manifest may hold*, below.

The two surfaces differ here and a reader who assumes they behave alike will gate the wrong one:
Chat's chip inlines `summary` only (`_describe_context_file`), which carries no values, while
Build's `@mention` inlines `detail` (`_resolve_mentions`), which carries the rows.

## Which Datasets are in scope

**The scope is the selected app's and the open Conversation's, not the Project's.** This document
never said which, and an unstated scope is read as the widest one it mentions — `SensitivityGate` is
described as "per Project" further down, which is true of the object and its caches and says nothing
about what a turn can reach. So the scope is stated here rather than corrected: of the three doors
below, two are the selected app's — `_datasets_in_scope` reads the Binding manifest plus
`project.attached`, and `Project.attached` is the app on screen — and the third, a pinned chip, is
the Conversation's.

Narrow is also right, not merely what the code happens to do. The lock's job is to cover the read
paths the turn actually has, and the lock and the reachability travel together: a Build turn's cwd
is one app, and `ensure_chat_workdir` symlinks that same app's `public/data/` into the Chat cwd,
with `_ensure_dir_link` re-pointing it when the selection changes — while the transcript the turn is
answering in carries whatever that Conversation pinned. Widening it to every app under `apps/` would
arm the lock over rows the turn cannot read, which refuses work on grounds that are not true.
Changing the selected app does drop a `declared` lock — the sticky bit is what keeps that from being
a hole, and it is the subject of its own section below.

**The two halves are not interchangeable, and the notice is where that shows.** A chip is
conversation-scoped and writes neither a Binding nor an Attachment, and the Conversation travels
into Build through the handoff — so the mode says nothing about which door armed a lock. Hence
`lockWayOut` points at App dependencies only when EVERY declared Dataset holding the lock is on the
app's own list (`appHoldsEveryDeclaredDataset`). "Any" was the first rule and it was wrong in the
case the three doors make ordinary: an app binding declared `claims` while the Conversation pins
declared `members` satisfies "any", and the notice would then say "Remove them" of a pair only half
of which is there — the creator removes one, comes back, and the lock has not moved. Under "every",
the chip-only lock and the mixed lock are one case with one rule, and the pre-manifest Attachment
that carries no `dataset_id` falls under it too rather than needing to be remembered separately.

**The pointer crosses out of Chat, and names the surface when it does.** Withholding it there was
decided against once, on the precedent that every other "under App dependencies" sentence is
Build-only through the mention guard. The precedent does not transfer: a mention is an ACT and
ADR-0021 keeps an act on the surface that owns it, while a pointer exists so a reader can be sent
somewhere they are not standing, on ADR-0011's condition that it names the destination in the words
they will see on arrival. Withholding it would have reproduced the last clause of the failure this
whole feature answers — the lock on in Chat with its only removal control in Build — with the naming
fixed and the route still missing. Build's sentence names no mode, because naming the surface already
on screen reads as a correction.

A Dataset reaches a turn three ways, and all three put its rows in front of a model. The gate read
the Binding manifest alone, which is the door people use least — so the lock did not fire for either
of the two doors the product actually puts in front of somebody.

**Bound.** `bind_dataset` wrote a record. The only one a manifest knows about.

**Attached.** Files sit under `public/data/<slug>` and `@mention` inlines their descriptor. Attaching
writes no Binding — that is a separate act behind a separate button, and ADR-0021 keeps it separate
on purpose. The attachment entry keeps `dataset_id`, which is the same id space the Binding uses, so
the two join without a listing.

**Pinned.** A Chat `dsfile:` chip. Chat has no Built App and writes no manifest at all, so before
this the Chat lock could only fire on whatever the selected app happened to have bound — never on a
Chat act. A chip belongs to one Conversation, so this source is consulted only when there is a
Conversation to consult; the unscoped Build turn and the preview proxy have none.

`Orchestrator._datasets_in_scope` assembles the three and the gate still decides which are declared,
which is why the fail-safe covers all of them: it is keyed on the Dataset LISTING rather than on how
the Dataset arrived. The one place this is not fail-closed is an unreadable Conversation, which
narrows the scope rather than failing the turn — bounded, because the sticky lock already holds
every Conversation whose transcript actually carries declared rows.

**The crossing was repaired with it.** `binding_from_context` matched on chip kind, and a Dataset
chip is `kind: "file"` carrying `datasetId`. So a Chat that read declared rows crossed into a Build
whose manifest recorded no Dataset, and the lock did not survive the handoff either.

## What the manifest may hold

`.sage/attachments.json` is committed, and the descriptor cache lives in it. `public/data/` is
gitignored precisely so that data never enters git — and three verbatim rows of that same data were
being committed beside it, in the file whose job is to survive into the published app.

So a declared Dataset's `detail` is withheld from the cache and only from the cache. The prompt still
gets it: the turn is locked to an approved model, and an approved model reading the rows is the
feature rather than a gap in it. `_descriptor(want_detail=True)` re-reads the file for the one caller
that inlines it, which costs a read per mention rather than per file — the loops that describe every
attachment on every turn read `kind` and `summary` and are untouched, which is what keeps ADR-0029's
cache doing the job it was built for.

**The declaration reaches backwards.** A Domino tag is self-service and can be added at any time, so
the ordinary case is a file attached before the declaration existed, with its rows already committed.
`_scrub_declared_descriptors` takes them out when the lock engages. This is a different answer from
the one given to an app published before its Dataset was tagged, and deliberately: there no
interception point exists and unpublishing would be destructive, while here Sage owns the file and
rewriting it costs nothing. Two situations that look alike and are not.

## Failing closed without failing everyone

Fail-closed needs four answers, not one, and each gets its own message in the existing
`publish_guard` register — what happened, why it matters, what to do.

The group is configured but absent (`missing-model-group`). The group exists but is empty
(`empty-model-group`). The gateway could not be reached (`unchecked-alias`, lifted unchanged). And
the one that will actually happen: the group has members but this caller has permission on none of
them (`no-approved-model-access`), because `/api/aliases` lists every registered alias while
`/v1/models` lists only the ones the caller may use. The last needs copy of its own — naming the
group, naming its models, and saying who to ask — because a dead end with no next step is its own
failure.

All four refuse. The deleted implementation had the reasoning right: *"Sage could not check where
they would go" is not a reason to send them*. The blast radius stays narrow by construction, because
none of this is reached unless a Dataset has already been declared.

**This only works because the feature is off by default.** Domino dataset tags are freeform and
pre-existing; a customer may already hold Datasets tagged `sensitive` today, for their own reasons,
with no idea Sage exists. Shipping this on, with a default group name no administrator has created,
would take every such Project and lock it to an empty approved set the first time anyone opened it.
Nobody would have changed anything. So `SAGE_SENSITIVE_MODEL_GROUP` is both the group name and the
on-switch: creating the group is the act of opting in, which also makes "ask your Domino
administrator" true advice in all four messages above.

## Considered options

**Keep ADR-0012's refusal.** Rejected on its own terms. Its reason was an absence, it named the
absence, and the absence ended.

**A Sage-side list of approved models**, in configuration. Rejected: it makes Sage a second model
registry that goes stale per deployment, and it answers a question the gateway already answers.

**Derive the approved set from `provider_type`.** Rejected above — uncorrectable, and it conflates
where a model runs with whether anyone approved it.

**Sage creates the group when it is missing.** Rejected: it removes a setup step by making Sage the
authority whose word it is reading.

**Refuse the Alias binding outright.** Rejected: strongest, and surprising at a moment the creator
is not thinking about data.

**Stop or unpublish a running app when its Dataset is tagged later.** Rejected: Sage taking down a
production app on the strength of a self-service tag somebody else edited, with no interception
point that would make it enforcement rather than theatre. The state is surfaced and a human acts.

## Consequences

**A creator's picked model can change under them, and is told.** Attaching a sensitive Dataset moves
the session to an approved model and says so once; the picker then shows non-approved aliases
disabled with the reason. Switching silently would fail the confidence the promise is meant to
build, and blocking the turn outright would stop somebody mid-task to teach them something a
sentence can teach.

**A Dataset's declaration is read per turn and never written down.** The tag is a live fact and
somebody can remove it, so the verdict is held in memory and never in `.sage/bindings.json` —
`pinned_model.py` already records why a live fact in a committed file goes stale in a place nobody
re-reads. The two answers cache differently: holding "sensitive" too long over-restricts, holding
"not sensitive" too long is the leak.

**Sage may tag an upload, but only into a Dataset it made for this Project.** A Domino tag marks a
whole Dataset snapshot, not a file, and Sage's uploads land in a Dataset the creator chose from
their writable ones — which can be shared. Ticking a box on one upload would otherwise tag a
colleague's Dataset and lock their Projects, from inside a form that said nothing about them.
Marking a shared Dataset stays a deliberate act in Domino's own UI, by somebody who can see who else
uses it.

**A Dataset says it is sensitive before the lock does.** The Resource panel badges the Dataset row
and its Binding, using the chip pattern already carried by alias capabilities and Data Source
readiness. The lock then reads as a consequence the creator already knew about.

**An app published before its Dataset was tagged keeps running, and Sage says so.** There is no
interception point to stop it and no unpublish that would not be destructive. Sage shows it as out
of compliance and the next publish fixes it.

**The vocabulary is re-taken.** `CONTEXT.md` lists `sensitive` under _Avoid_ on **Sample rows**,
recording that the flag it named was gone. It is back, with a narrower meaning than the one that was
removed — a declaration on a Dataset, not a property of a conversation — and the glossary now says
so rather than warning the word away.

**The deleted implementation is a source, not a template.** Commit `685ebf3` removed a sovereign
lock, a sensitive-tag upload, a sample-row tick and a vendor-model publish refusal together. Two of
its pieces are lifted here almost unchanged — the case-insensitive tag match and the publish
problems — and the rest is not: this gate reads an administrator's group instead of deriving a
sovereign tier, and it does not touch sample rows at all.

## What running it against a real deployment changed

Verified end to end on cloud-dogfood, 2026-09-09: a Dataset tagged `sensitive`, an alias group
`sensitive-approved` holding `opus` and `haiku`, and a Project binding `gpt-5.4`. The declaration
read, the turn locked to `opus`, and the publish refused with `sensitive-rows-to-vendor-model`.

**Approved means approved AND reachable.** The group named two models and only one of them was on
this caller's `/v1/models`. The first implementation took the group's word for both, which put
`haiku` in the approved set — and because the router breaks ties by sorting, `haiku` is the one it
would have chosen, on a model the gateway then refuses. A designed refusal
(`no-approved-model-access`, which exists precisely for this) would have arrived as a dead turn with
a permission error instead.

So both sources are filtered through the permission-filtered alias listing, and the reverse source
contributes only members that listing already holds. The rule is worth stating on its own, because
it is not obvious from either source alone: `/api/aliases` lists every registration deployment-wide,
`/api/alias-groups` lists membership, and NEITHER of them knows what this caller may call. Only
`/v1/models` does, and it is what `join_aliases` has always intersected against.

This is also the answer to why `members` travels beside `names` on `ApprovedModels`. The count is
the group's declaration and the set is what survives permissions; when they differ and the set is
empty, that gap IS the `no-approved-model-access` message, and neither number can produce it alone.

**The reverse source is a hedge against a redacted field, never a second opinion on permissions.**
That distinction was implicit before and is now the reason a test exists.

**The LIVE-VERIFY narrows but does not close.** `groups` is returned and populated on `/api/aliases`
— `opus` came back as `["FDE_models", "sensitive-approved"]` — so the forward source is real rather
than theoretical. It is still only proven for a `GovernanceAdmin` identity, because no non-admin
account was available to test with. The hedge stays for that reason, and the note stays with it.

**A tag write needs a snapshot, and the fallback earns its place.** `tag_dataset_sensitive` ran
against the Project's default Dataset, which carried `snapshotIds` and no `latestSnapshotId` — so
the two-key fallback carried over from 685ebf3 is what found the snapshot to tag. Worth recording
because the Domino UI would not let the same person do this: it asks for a snapshot they had not
taken, while the API tags the one the Dataset already has. Sage doing it for them is the workflow
this ADR chose, and the reason it chose it turned out to be more concrete than the argument made.

## Amendment: which approved model, and who chose it

Two questions the first draft left standing, answered together because either alone makes the other
worse. Both are about the shape of the group an administrator curates, and neither changes what the
gate refuses.

**The administrator's ordering of the group is a preference, and it was being thrown away.**
`ApprovedModels.names` is a frozenset, so a group listed as `opus, haiku` and a group listed as
`haiku, opus` reached the router as the same thing, and the router broke the tie with `min()` —
alphabetical order standing in for somebody's decision. An ordered tuple now travels beside the set
(`ApprovedModels.order`, off `/api/alias-groups`, which is the only source that carries an order at
all), and `llm_router._nearest_approved` reads it after the sovereign slots and before `min()`.

The two fail-closed edges are unchanged and deliberately so. `min(approved)` stays as the last
resort, for a deployment whose gateway offers no group listing to order — a stable arbitrary pick
is still better than refusing a turn somebody is waiting on. The empty-set `ValueError` stays: that
set is the orchestrator's refusal to make before the turn starts, and a router that returns a model
cannot express "no".

Sovereign slots keep their place above the ordering. A sovereign slot is an assignment made for this
Sage and already preflighted; the group ordering is a preference expressed about a list of models.
The narrower statement wins, and the ordering only decides where the narrower one is silent.

**And now that the pick is somebody's, it is said out loud.** `util.lockedLabel` named a model only
when exactly one was approved, and otherwise wrote "Approved model" — correct, because a second copy
of `_nearest_approved` in JavaScript would be a confident label that is wrong on the turn where it
matters. The answer moved instead of the rule: `sensitivity_state` runs the real router and sends
the result, so the chip and the notice read a choice rather than guessing at one. The narrow rule
stays underneath as the fallback for a state that carries no answer.

Doing this half alone was the thing to avoid. Naming the model without the ordering would have
published an arbitrary pick — the chip would have said `haiku` because `h` sorts before `o`, and a
person would have gone looking for the decision behind it.

**It sends where the lock MOVES a turn, not what `resolve` would run**, which is why
`nearest_approved` is public and the state does not simply carry `resolve(...).model`. The two
differ on exactly one input: `resolve` hands back an approved pick unchanged. A browser holding that
answer names the old pick the moment somebody picks a barred model, which is the original defect
with a fresher source. `nearest_approved` reads no pick at all, so the answer is safe to hold across
one — and the browser can already see that an approved pick runs as itself.

**Two of them, one per composer.** Chat is pinned to the sovereign Ask slot and Build follows its
mode, so `model` and `chat_model` are separate fields. One would have made whichever surface it was
not computed for say the wrong model out loud.

Both composers draw a chip and both now read their own field. Build's had never gone through
`lockedLabel` at all — it named `override || pinnedModel` directly, so under a lock it went on
naming the barred pick while the turn ran on an approved model, and the only correction was a
notice with a "Got it" on it. That is the defect the amendment was written for, found on the other
composer. Its three render branches carry the label and the reason together, and the two sentences
that named the catalog outright (`Ask runs on X`, `Auto runs X to plan and Y to build`) give way to
the lock's own, because under it both are false.

**A label must be judged on the alias, never on the name it draws.** A `model_llm` row carries the
gateway alias under `alias` and its display name under `name`, and the approved set holds aliases.
Chat's chip asked `isApproved` with the label, so an Alias shown as "Opus 4.6" read as unapproved
while `opus` sat in the group — and the chip then replaced a good label with a model that was not
going to run, with no notice to correct it because nothing had switched. `isBarred` had it too,
which is the rail marking an approved Alias. The picker beside both has always keyed on
`option.alias`. Recorded because the mistake is invisible on any deployment where the two names
happen to match, which is most of them, and the test harness fed one string as both.

What is left after that is a running Auto build, where the shim's per-step classifier moves the
phase underneath a state nobody re-read. It needs a deployment whose sovereign slots differ AND are
separately approved, which is the only shape where the mode decides anything at all.

**The Workbench re-reads on a mode change and not on a model change.** Both follow from the
paragraphs above: the mode is an input to `nearest_approved` and the pick is not. A refresh per
model pick would have been the wrong fix for the right worry.

**The name must not be able to take the lock down.** `/api/project/sensitivity` answers a failed
read with `enabled: false`, and that fallback is right for the lock state and wrong for the label on
it — an unlocked answer puts non-approved models back in the picker, which is the surface a person
acts from. So resolving the model is caught separately: the chip falls back to "Approved model" and
everything that governs anything still ships.

## Amendment: several tags may mean sensitive

`SAGE_SENSITIVE_DATASET_TAG` is now a comma-separated list, and any one of the tags declares a
Dataset. The original comment argued for one name on the grounds that a list "invites the belief
that Sage understands their taxonomy". The belief is the risk; the singular was not the fix. A
customer arrives with `pii` on one team's Datasets and `confidential` on another's, and forcing one
name means either re-tagging Datasets Sage does not own or leaving half of them undeclared — which
is the leak, reached through configuration.

They are synonyms, not tiers. `assets/provider.py` (`sensitivity_tags`, `is_sensitive`) and
`resources/sensitivity.py` (`declared_keys`) take a set of wanted tags, matched by case-insensitive
equality exactly as before. Writing still picks one — `sensitivity_tag` answers the first configured
name — because tagging a Dataset with every synonym would make Sage an author of the vocabulary it
is supposed to be reading.

An empty entry is dropped rather than matched. A trailing comma is the likeliest way to write this
list, and a tag matching `""` would declare every Dataset on the deployment.

**Explicitly not tiered classification.** A tag routing to a DIFFERENT approved group — `pii` to one
group, `restricted` to another — is a separate decision and a separate ADR. It needs a mapping in
configuration rather than a list, an answer for a Dataset carrying two tags at once, and a story for
what the picker says when the bound Datasets disagree. None of that is answered by this amendment,
and shipping the list first does not prejudge it: a set of synonyms is the degenerate case of a
mapping, so tiers can be added over the top without taking anything back.

## Amendment: the lock follows the conversation, not the Binding

The gate above reads the live Bindings, and that was the whole of it. `unbind` edits the Binding
manifest and rewrites the app's resources; it does not touch the Chat Thread or the OpenCode session
behind it. The transcript persists and is re-sent on every turn after. So: turn 1 reads a declared
Dataset under the lock, turn 5 the creator unbinds it, turn 6 sends the same transcript — rows
included — to whatever vendor model is picked. Nothing in the gate had anything to say, because by
then nothing was declared.

Compaction is the sharpest form of it. It sends the WHOLE conversation, and `COMPACT_FALLBACK` is
`gpt-5.4`. The invariant pinned above — the shim rewrites `model` on every request — holds only for
a turn that is ARMED, and after the unbind nothing armed. `test_compaction_cannot_leave_the_lock`
proved the rewrite and could not have proved this; the two files now name each other.

**Once any turn of a conversation has run under the lock, that conversation stays locked.** The way
out is a new chat, not an unbind. `_sensitivity_for_turn` reads a sticky bit beside the live
declaration, and everything past it is unchanged: the bit does not depend on Bindings at all, so the
existing arm-or-refuse path runs exactly as it did. Both harnesses set it, from the one place each
of them already armed from.

**It is per conversation, and it is on disk.** Not on `SensitivityGate`, which is per Project and
holds caches — this is a fact about one conversation's history, not about the Datasets in scope.
Not in memory either: a restart clears a flag while the history it is about comes back, and a lock a
restart lifts is the hole with an extra step. It lives on `ProjectRecord` beside the session id and
the transcript, under `.sage/threads/<id>/`, for the reason the session id lives there — one
conversation can build several apps, and a bit filed under `apps/<appId>/` would be lost at exactly
the moment a Chat handoff creates the app it hands off to. That is also why the handoff needs no
mechanism of its own and is tested anyway: it carries the same conversation id, and a handoff that
dropped the bit would be the same leak with one extra step in front of it.

Written BEFORE the prompt goes out, so a turn that dies has still tainted the conversation — the
transcript it was building is what carries the rows. A turn REFUSED above never reaches it, which
matters in the other direction: marking there would leave a conversation locked for good on the
strength of a turn that never happened, after an administrator fixed the group.

**`None` is not `""`.** The preview proxy passes no conversation, because the previewed app's own
model call carries the app's current Bindings and no transcript; the unscoped Build turn — the CLI,
a test — passes the empty string, because it has a transcript like any other and keeps its slot
beside its session. The parameter has no default so that a harness added later has to answer the
question. A default would have made the hole reachable by omission, which is the shape it had.

**The old sentence is false the moment it is needed.** Every locked-turn sentence named the declared
Dataset, and that is exactly what is gone. Pointing a creator at `the Dataset claims` sends them to
a panel with no `claims` on it, to remove something already removed, and they come back to a lock
that has not moved. The sticky sentences name the reading instead — "this conversation has already
read data declared sensitive" — and carry the way out, which is the part nobody guesses: unbinding
is the obvious move and it is deliberately the wrong one. `sensitivity_state` reports `reason`
(`declared` / `session`) so the browser picks the sentence rather than inferring one from an empty
`datasets` list, which is also what an older server and a failed read look like. The live reason
wins while there is one: it names a row somebody can go and look at, which the other cannot.

The lock read now carries the Conversation. Asking without one un-greys every model the next turn
would refuse, on the surface the person acts from — so it is re-read wherever a Conversation opens,
and the way out is made to work at the moment it is taken: closing a Conversation DROPS a session
lock locally rather than re-reading, because that reset is synchronous and reaches no network. Only
a session lock, and only because `reason` is `session` exactly when no declared Dataset is bound —
there is no live half underneath it to lose. Dropping a Bindings lock there would put non-approved
models back in a picker while the Dataset barring them is still attached.

**Fail-closed edges are unchanged, deliberately.** A group that breaks after the conversation was
tainted still refuses the turn — the sticky lock is a lock, not a grandfather clause — and the
empty-set `ValueError` in `llm_router._nearest_approved` stays where it is.

### What this promise does not cover

Stated here so nobody later reads it as wider than it is. **A build under the lock can put rows
somewhere the conversation does not reach.** `public/data/`, a committed file, a line of code — all
of them outlive the session, and the same unbind also defeats the publish guard, which checks
current Bindings too. This amendment covers the models SAGE calls while it holds a transcript. It is
not a promise about a creator who unbinds deliberately in order to launder data out of a Project.

**A lock that cannot be written down refuses the turn.** Running unrecorded would leave declared
rows in a conversation that the next turn — and every turn after a restart — reads as clean, which
is the hole again with a full disk in place of an unbind. Both harnesses refuse the way they refuse
an unusable group: by disarming first. The marker is written after the Chat pin, the turn-mode pin
and the read-only and web grants are already armed and BEFORE the `try/finally` that releases them,
so a raise walking out of the generator would leave every one of them live on a `ModelControl` that
is not per-turn state.

**Two smaller edges, named rather than fixed.** The unscoped Build turn has one lock slot per
Project and no way to start a new one, so once it is locked it stays locked. No shipped path reaches
it: the Workbench sends a conversation on every build and mints one first if there is none, which
leaves this repo's tests and a hand-written POST with the field omitted. Worth writing down because
the older docstrings around `build_session_path` call that caller "the CLI" and there is no CLI —
if one is ever added, it needs a way out added with it, since the alternative is a transcript with
no lock. And the panel's sticky
sentence says the lock holds in Chat and in a build, where the Bindings sentence beside it also says
"at publish" — because `sensitive_model_problems` reads the CURRENT Bindings and there is no
publish-time gate left to promise once the Dataset is gone. A sentence about the lock that is false
at the moment somebody relies on it is the thing this whole amendment is against.

The blunt fix — a per-Project taint that never clears — was rejected on the same grounds as the
per-turn cache above: it over-restricts every Project that ever bound a declared Dataset, forever,
with no way back short of a new Project, and it would fire hardest on the deployments that opted in.
The narrower promise is the one that can be kept and said out loud.
