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
