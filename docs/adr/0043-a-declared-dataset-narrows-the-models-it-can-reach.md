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
Dataset tagged `sensitive` now narrows the models Sage will use while it is attached, and narrows
the models the app Sage builds may be published against. The approved models are named by a
**Domino LLM Gateway alias group**, which an administrator curates and Sage only reads.

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
classification field at all — none exists to read. Because the gate is per-Project rather than
per-file, a Snowflake source bound beside a tagged Dataset is protected incidentally, and the real
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
is attached, reusing `sensitive-rows-to-vendor-model` — the code the deleted implementation used for
exactly this refusal.

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
