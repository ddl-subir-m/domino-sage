---
status: accepted
supersedes: 0003 (Decision 1 only)
---

# Workbench is the door; Chat and Build run in this viewer's Sage Builder

ADR-0003 Decision 1 ran Chat and Build in a scratch dir on the published Workbench App and
treated New project as a later slice. That is withdrawn. The Workbench App is the door: with
extended identity the sidecar is the viewer; first open finds or creates their **Default**
Project (`sage-<user-slug>-<id>`), starts or resumes **their** Sage Builder, and takes them
there. Hub is not a product. ADR-0003 Decisions 2 and 3 (prototype shell, artifacts as files)
are unchanged.

## Considered options

**Scratch in the App** — ADR-0003 Decision 1. Rejected: files die on App rebuild, New project
is a no-op, and Chat in the App cannot use the workspace Flight path.

**Stay on the App origin and proxy into Sage Builder** — two orchestrators. Rejected: that is
how Chat in the App hung on a Data Source.

**One Sage Builder shared by every collaborator in a Project** — Hub's single workspace named
`sage`. Rejected: a Domino workspace is owned by the person who started it.

**Hub as a second published App** — rejected: the Workbench App provisions and attaches;
Gallery is Built Apps inside Sage Builder chrome, not a Hub home.

**Compute in the App, Project is only a git checkout** — rejected: Build on `main` already is
Sage Builder; Chat and Build stay there.

## Consequences

- **New conversation** is a Thread in the current Project. **New project** creates a git-backed
  `sage-*` Project (GitHub repo + git-based Domino project + this viewer's Sage Builder) and
  lands in Chat there.
- The chip lists only `sage-*` Projects and switches by attaching that viewer's Sage Builder.
  Naming Default changes the chip only; it stays this viewer's Default.
- **Gallery** is Built Apps this viewer may see. Opening an item opens that app; it does not
  switch Project. It is not the Workbench App's home screen.
- Publish is a Sage Builder verb. Default is a real git Project, so Publish is no longer
  disabled just because the person entered through the door App.
- Language: [CONTEXT.md](../../CONTEXT.md).
