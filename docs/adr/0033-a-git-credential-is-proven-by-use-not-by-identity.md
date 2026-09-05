---
status: accepted
---

# A Git credential is proven by use, not by identity

Domino wires a Git credential **per repository**, so a user legitimately holds several HTTPS
credentials for one host. `_git_credential_id()` took the first one `/api/users/beta/credentials/{uid}`
happened to list for the host and sent it, with no validity check and no tie-break (#157). When that
first one was dead — a revoked PAT, a wrong account — project creation failed, and the user could
not tell which credential was tried, that the others were never tried, or which one they should
have used.

The obvious fix is to identify the right credential: prefer the one the checkout already uses, which
is the one `credentials.extract_token()` resolves and the one that just created the repo. That is
the only candidate with **proven** access to a brand-new private repo, so it should win.

**It is not implementable.** The credentials list carries exactly `domain`, `fingerprint`,
`gitServiceProvider`, `id`, `name`, `protocol` — verified live on 2026-09-04 — and no `username`.
`git credential fill` answers `username` and `password`. So the only possible join between a listed
credential and a filled token was `fingerprint == some hash of the secret`. Probed in a Builder
against a `ghp_`-prefixed 40-character classic PAT (a stored secret, not a short-lived minted one),
seven preimages all missed: md5 of the token, of the token plus newline, of the token without its
prefix, of the username, of `user:token`, and sha256/sha1 truncated to 32 hex. There is no join.
Sage cannot name the credential it is holding.

## What Domino does validate

Two live probes on 2026-09-04, both cleaned up:

- **A real credential against a repo it cannot reach**: `500`, `"Cannot access Git repository with
  URI: … This may be due to invalid Git credentials"`, and **no project created**.
- **A credential id that does not exist, against a public repo**: `200`, project created.

So Domino validates **repo access**, not credential validity, and it does it inside the create call.
Sage's repos are always private, so the check always bites where it matters — but the signal is
access, and the ADR says so because a BYO public repo will never produce it.

The first probe is load-bearing: a rejected create leaves nothing behind. That is what makes trying
every candidate safe, and it is the reason there is no cap and no cleanup.

## The decision

**Sage stops guessing which credential is alive and lets Domino say.** It sends a candidate; if the
create fails, it sends the next.

- **The retry lives in `ProvisionService.create_app`, not in `DominoControlPlane`.** ADR-0028 gives
  "what a failure costs — a thin page, a retry, a refusal" to the caller, and names a retry
  explicitly. So `create_project` takes a `git_credential_id` it is told and reports what Domino
  said; the service, which knows it has just created a repo and is mid-provisioning, decides that a
  failure is worth another attempt. The provider gains `git_credentials()`, which reports the
  caller's credentials as `CredentialRef` — id, label, domain, protocol, and whether it applies to
  the configured host. It decides nothing.
- **Any failed create triggers the next attempt.** Never Domino's message text. The message is copy:
  unversioned, and free to change without notice. Control flow that reads it would silently stop
  retrying after a Domino wording change, with nothing failing loudly enough to notice. The observed
  rejection is a `500`, so keying on `400`/`403` would have excluded the only error that matters.
- **No cap.** The list is bounded by the account. A cap is a magic number that turns a solvable
  failure back into the dead end this issue is about.
- **No cache.** The old `_cred_id` saved two GETs per app creation. A remembered winner can be
  revoked between uses, and a stale winner is this bug again.
- **On total failure, group the candidates by what Domino said** and print each distinct message
  once. That separates "all my PATs are dead" from "one is dead and one hit a different problem",
  which are different fixes. Grouping needs Domino's own words pulled out of the error body,
  because every body carries a fresh `requestId` and two identical failures would otherwise never
  merge.
- **`/api/diag` reports the list side**, mirroring `credentials.credential_probe()`: which
  credentials the loop will try, in order, and which it will skip. No secret, and no `fingerprint` —
  it identifies nothing to a person reading a diagnostic, and it is not a hash of anything useful.

## Why not the alternatives

**Validate before use** — probe each candidate and send one that works. There is nothing to probe
with. Sage never holds the other candidates' secrets; the list carries none. The only validator is
Domino's own access check at create time, which is exactly what the retry uses.

**Let the user choose** — show the candidates in the door and remember the pick. It asks the user a
question Sage can answer by trying, puts a modal in front of the one path that must stay a single
click, and the remembered pick goes stale the same way a cache does.

**Only fix the error text** — name the credential that was tried and stop there. That shipped first
(`3116d80`) and it is a real improvement, but it hands the user a problem Sage can solve, and their
only lever is to delete a credential to change an order they cannot see.

**Ask Domino for a better endpoint.** Worth doing, and out of Sage's hands. Nothing here blocks it:
if the list ever carries a `username`, the join reopens and preferring the checkout's credential
becomes the cheaper answer, because it would send the right one first.

## The cost, stated plainly

A failure that has nothing to do with credentials — a name conflict, a bad field — now retries once
per candidate before it surfaces. Those attempts are wasted POSTs. We take that deliberately: the
alternative is reading Domino's copy to decide, and a missed retry is a dead end while a wasted POST
is a few hundred milliseconds. An account with one credential, which is the common case, pays
nothing.

The grouped error parses Domino's error body for **display**. If that shape changes, the text
degrades to the raw body and the behaviour is unaffected — the parse is deliberately kept out of the
retry decision so that a copy change can never alter what the code does.

And the fingerprint negative is a fact about today's API, recorded here so nobody re-runs the sweep.
It is not a promise about tomorrow's.
