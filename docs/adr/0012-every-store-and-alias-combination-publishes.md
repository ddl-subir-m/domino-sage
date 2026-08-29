---
status: accepted
---

# Every Data Source and LLM Alias combination publishes, with the egress named

A published app can read a Data Source and put those rows in front of an LLM Alias whose model
runs outside Domino, at run time, for every viewer. We decided that **no combination of the two is
refused at publish**. Publish names what will leave Domino and the creator decides.

This answers the question #35 could not write its guidance without: the AGENTS.md block explains a
consequence, not a rule, because there is no rule to explain.

## Why not a refusal

**There is no declared sensitivity to gate on.** The `sensitive` marker from #16 does not survive:
`render_samples` drops it and `parse_samples` ignores it in an older file
(`backend/sage/resources/bound_schema.py`). Nothing anywhere records that one store is more
sensitive than another. So a refusal cannot be selective — it is blanket or it does not exist.

**A blanket refusal refuses the ordinary case.** Only a Domino-hosted Alias carries an
`endpoint_url`; 12 of the 14 on cloud-dogfood do not, because they are vendor models
(`backend/sage/resources/provider.py`). "Read a table, summarise it with a model" is most of what
Sage is for. A guard that refuses the common case is a guard people route around, and the route
around this one is #94.

**The grant was already made upstream, by someone with more authority.** A Domino administrator
registered that Alias and made it callable, and configured the Data Source and its credential.
Sage refusing the pair overrides two decisions taken above it, with less context than either had.

**It would fail open on the adverse case.** A guard reads declared Bindings. An app that calls an
Alias it never declared is invisible to it (#94), so the refusal would stop a careful creator and
miss a careless one. ADR-0010 rejected a derived veto for the same fail-open reason; a declared
gate with an undeclared bypass fails the same way.

## Egress is not re-export

ADR-0001's two publish guards look like a precedent for a third and are not. Both refuse a **change
of principal at publish**: an `Individual` credential that was one person's access becomes every
viewer's, and an app anyone can open puts the store in front of whoever finds the URL. Neither
condition exists before the app is shared, which is why the gate is at publish and not at pick.

Egress does not change at publish. The rows already went to that vendor during the build, under the
creator's own eyes, on the creator's own prompt. Publishing changes the **volume** and makes it
**unattended** — both real, and both worth a sentence. Neither is a new act by a new principal, so
neither is refusal-shaped.

## Considered options

**Refuse blanket** — any Data Source Binding plus any vendor-backed Alias Binding is refused.
Rejected: it refuses the mainline, and it fails open on the case it exists for.

**Refuse only a marked store** — build back a per-Data-Source sensitivity declaration and refuse
only that combination. Rejected for now: it invents a marking ritual whose default outcome is
identical to refusing none, and it is a larger piece of work than the thing it guards. This is the
option that reopens if the assumption below expires.

**Refuse none, and say what leaves** — chosen.

## Consequences

**The decision is only real if a person is told.** "Allowed, with the consequence explained"
degrades to plain "allowed" when nothing explains it, and Sage has this failure already:
`GET /api/publish-check` (#26) has no caller anywhere — its docstring says "a warning the UI shows"
and no UI shows it. So #35 owns the pre-publish notice **surface** as well as the sentence, and
`publish_check`'s existing query warnings render on it. A third unshown warning would not be a
partial delivery of this decision; it would be none of it.

**The read is network, and it fails quiet.** Telling a vendor-backed Alias from a sovereign one
needs the Alias listing, so this cannot live inside `publish_check`, which promises "local and
pure ... no network". It is a separate read beside it. When the listing does not arrive, no
sentence is shown. That is the opposite of `publish_problems`, which refuses when it cannot check,
and the asymmetry is deliberate: an unverified credential is a hole, an unwritten notice is not.

**It fires only on the join, and says nothing when the Alias is sovereign.** A vendor-backed Alias
with no store bound is not this decision's subject, and firing on every vendor Alias would fire on
nearly every app and be tuned out inside a week. Silence when the call stays inside Domino, for the
same reason.

**Sage cannot name the vendor.** An Alias record carries no vendor field; the only signal is the
absence of a Domino endpoint behind it. The sentence can honestly say the model is not hosted on
Domino, and no more. It names the Alias and the Data Sources it is paired with, the way the
open-visibility refusal already names its stores.

**The completeness of the record now carries weight it did not carry before.** With no refusal, the
sentence at publish is the whole of the protection, and it is built from declarations. An Alias the
app calls but never declared makes that sentence understate. #94 is therefore no longer only a
question of honest bookkeeping, and it takes acceptance criteria against this decision rather than
staying open-ended.

**A Model API is out of scope, deliberately.** It is deployed inside Domino, so calling one is not
egress. Its own leak — the access token compiled into the published bundle, readable by anyone who
opens the app — is real and unrelated to this decision, and belongs with #34's lineage.

**What would reopen this.** The load-bearing reason is an absence: nothing declares that a store is
sensitive. If that stops being true — Sage grows a per-Data-Source sensitivity declaration, or
Domino exposes a classification Sage can read — then "refuse only a marked store" becomes available
and this decision should be taken again. Nothing else here changes the answer; a refusal is not
unlocked by more Aliases, more stores, or a better scan.
