---
status: accepted
---

# Picking a Resource creates a live connection, not a baked-in extract

When a user picks a Domino Resource in the Resource Browser, the Built App queries it **at
request time** rather than receiving a snapshot copied in during the build. We chose this
knowing it is the more expensive option: the alternative was to have the agent run a query
during the build session and write the result into `public/data/` through the existing
`.sage/attachments.json` and `rehydrate-data.mjs` path, which works today with no new
infrastructure at all.

## Considered options

**Extract** — the agent queries at build time and bakes the result in. Ships without a
server, reuses the whole attachment and rehydrate path unchanged (verified: the manifest
needs no schema change, and `rehydrate-data.mjs` needs no edit, provided the bytes are
written onto a dataset mount and `public/data/` holds a symlink). Rejected because the data
is frozen at build time — the app cannot answer a question its creator did not anticipate,
which is the ceiling that prompted this work.

**Connection** — chosen. The app holds a Binding and queries live.

## Consequences

- The Built App needs a request handler, which `npx vite preview` is not. That is a real
  prerequisite the original scope did not name, previously sized at 2-3 weeks on its own, and
  it gates almost everything else here. See ADR-0002 for what serves it.
- Extract is not lost. It remains available later behind the same picker, because the
  Binding, not the file, is the contract.
- Because queries now run for viewers rather than only for the builder, credential handling
  stops being incidental: an app runs as its publisher regardless of who is looking, and
  per-viewer identity propagation is selectable only by admins at publish time, so Sage's
  users cannot have it. The two guards — Shared credentials only, and never publish `PUBLIC`
  — exist because of this decision, enforced at publish time rather than at pick time.
