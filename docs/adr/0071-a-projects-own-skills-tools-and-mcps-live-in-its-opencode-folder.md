---
status: accepted
extends: ADR-0052 (the LLM Gateway is the trusted enforcement point), ADR-0041 (a live read
  reaches the person without reaching the model)
reverses: docs/workbench/chat.md §8 "MCP connector UI" out of scope
---

# A Project's own skills, tools and MCPs live in its `.opencode/` folder

Today every skill and tool Sage offers ships in the repo and is copied into the container-wide
`~/.config/opencode/` at boot (`_install_opencode_config`, `backend/sage/orchestrator/app.py`).
A person cannot add their own. The resources panel already draws Agents, Skills and MCPs groups
as placeholders that never render.

## The decision

**Scope is the Project.** A person adds a skill, a custom tool or an MCP server from the
resources panel, and it is available to every Chat and Build in that Project, for everyone who
works in it. It is committed with the Project, like the rest of its files.

**Storage is OpenCode's own project slot, not a second mechanism.** Sage writes:

- `.opencode/skills/<name>/SKILL.md` (plus any files the skill ships)
- `.opencode/tools/<name>.ts`, and for Python `<name>.py` beside a generated `<name>.ts` wrapper
- `.opencode/opencode.json`, holding only an `mcp` block
- `.opencode/sage-extensions.json`, Sage's manifest: kind, source (upload or git URL and commit),
  default-enabled, and which files belong to which extension

These are at the root of the Project volume. OpenCode resolves project config from the git root
of the session directory, and both `.sage/chat-work` (Chat) and `apps/<appId>/` (Build) sit under
that root (`backend/sage/driver/server.py`), so one folder serves both. The project slot outranks
`OPENCODE_CONFIG`. Whether two `mcp` blocks merge key by key or the project's replaces the global
one is not yet measured; the global copy declares none today, so nothing is lost either way yet.

**Four ways in:** a file or zip (a skill folder, a `.ts` or `.py` tool), a git URL (cloned,
recognised pieces copied in, commit recorded), a Dataset in the Project (a skill folder, a
`SKILL.md` or a zip in it, read through the same asset provider as an attach, so mounted and
unmounted Datasets both work), and a form for MCP servers. A git or Dataset source is copied, not
linked: a later change there reaches the Project only when the skill is added again. The form takes a name,
then either a remote URL plus headers or a local command plus environment.

**Credentials are referenced, never stored.** Headers and environment values are written as
`{env:VAR}`, which OpenCode resolves from its own process environment. That environment is the
orchestrator's, which in Domino carries the user's and Project's environment variables. Sage never
holds the secret. A variable added after the app started needs a restart before it resolves.

**Python tools follow a small contract.** The `.py` declares `SPEC` (name, description, argument
schema) and `run(**args)`. Sage generates the TypeScript wrapper that OpenCode loads; the wrapper
spawns the interpreter with the arguments as JSON on stdin and returns stdout. OpenCode only loads
TypeScript and JavaScript tools, so the wrapper is the bridge rather than a second registry.

**User code is trusted.** Custom tools and local MCP servers run in the container as the
orchestrator's user, with no sandbox. That is a deliberate choice for v1: it is the person's own
container. It means such code can reach the control port and read the process environment.

**On by default, toggled per Thread and per Built App.** A new extension is enabled for new
Threads and Apps. A person can switch any one off for a Thread or an App.

**Read-only turns get the enabled tools that are marked read-only.** Every user tool and MCP tool
carries a `readOnly` flag in the manifest. An MCP tool's comes from its `readOnlyHint` annotation,
read by Sage when the server is added; a custom tool's from its spec (`readOnly` in a Python
`SPEC`, or set on upload for TypeScript). The person can override either in the panel. Ask and plan
turns (Build's gated and answer-only turns) are offered only enabled tools marked read-only; every
other turn is offered every enabled tool. `READ_ONLY_DENIED` keeps stripping Sage's own write and
shell tools as before.

The flag is self-declared, which fits trusting user code. The existing revert stays as the
backstop: a Build turn that is gated or answer-only and changes the app's tree is reverted and
reported (`agent_wrote()` then `discard_changes()`, `backend/sage/orchestrator/service.py`). That
check reads the tree hash, not the tool name, so a mislabelled tool trips it like any other write.
When a user tool ran on that turn, the message names it rather than blaming "the agent".

**Chat stays confined to its Thread's folders.** Every Chat turn already undoes workspace writes
outside `examples/<thread>/`, `.sage/threads/<thread>/` and `.sage/scratch/<thread>/`
(`revert_denied_writes`, `backend/sage/workspace/threads.py`). User tools get no exemption.

**User-defined agents are out of scope.** No agent extension kind, and nothing on screen for one:
the resources panel's Agents placeholder and the catalog's Agents filter are removed.

**The toggle is enforced in the shim, per turn.** Config cannot do it: every Chat Thread in a
Project shares `.sage/chat-work`, so one config answers for all of them. The orchestrator passes
the turn's enabled set to the shim (`backend/sage/shim/enforcement.py`), which:

- strips tools belonging to disabled extensions: a custom tool by its id (the filename), an MCP
  tool by its `<key>_` prefix, which OpenCode adds to every MCP tool name;
- adds enabled user tools to the data-artifact lane's allowlist, which otherwise strips any tool
  it does not name, silently;
- removes disabled skills' `<skill>` entries from the system prompt's `available_skills` block,
  which is where OpenCode 1.18.4 lists skills (the `skill` tool's description does not name them).

**A change takes effect by disposing the instance.** OpenCode loads skills, tools and MCP config
once per directory instance and does not watch the files. After any add or remove, Sage calls
`POST /instance/dispose?directory=…` for `.sage/chat-work` and each `apps/<appId>`, and only while
no turn holds that Project's turn lock.

**Names are checked at the door.** The `sage-` and `sage_` prefixes are reserved, and so are the
names of built-in tools and skills. A user MCP named `sage-live-read` would otherwise replace Live
read in the merged config. The built-in skill names are read from what Sage ships
(`template/skills/*` and each stack's `.opencode/skills/*`), not from a list kept by hand, so a
skill added to Sage later is reserved without anyone remembering to.

**A user extension overlapping a built-in adds to it unless it says it replaces it.** Sage's
built-ins come in two shapes: skills, which reach the model the same way a user's do, and the
always-on sections of the app's `AGENTS.md` (`design`, `platform`), which are in every turn they
apply to. A user skill with a different name but the same purpose (a company design system, a
house table component) would otherwise sit beside the built-in with nothing saying which wins,
and the model picks one, both or neither from turn to turn.

- **By default it adds.** User skills are framed like the Project's standing instructions: on a
  conflict, Sage's rules win.
- **A skill may declare `replaces: <built-in>`**, chosen on upload from the derived list of
  built-ins and recorded in the manifest. While the replacing skill is enabled for a turn, the
  built-in is hidden: a replaced skill is stripped from `available_skills` by the same shim path
  as a switched-off one, and a replaced section is dropped by the implement-section chooser
  (`choose_instruction_sections`, `backend/sage/implementation_request.py`). Switching the user's
  skill off brings the built-in back.
- **A replaceable section keeps a core that cannot be replaced.** For `design` that is the theme
  variables and the Inter `@font-face`: breaking them breaks light and dark mode and the font, not
  only the look. The section is split into a replaceable style part and an always-on guardrail.

**A built-in that arrives after a Project skill of the same name wins, and the panel says so.**
OpenCode offers one skill per name, and Sage's copy is the one it picks (measured below), so the
Project's cannot win by where it sits. The panel marks it hidden behind the built-in and asks for a
rename; the shim leaves it out of its catalogue, so switching it off cannot hide Sage's. The files
live in different folders, so neither copy overwrites the other.

## Measured (OpenCode 1.18.4, isolated config home, 2026-10-02)

- A project `.opencode/skills/alpha` and `.opencode/tools/hello.ts` loaded on first use.
- A skill, a tool and a local MCP server added while the server ran were all **absent** until
  `POST /instance/dispose?directory=…`. After it, all three loaded and `GET /mcp` reported the new
  server `connected`.
- `{"environment": {"X": "{env:HOME}"}}` resolved to the server's `HOME` in `GET /config`.
- The model request carried `hello`, `py_add` (the Python wrapper) and `user-echo_echo` (the MCP
  tool, prefixed with its key) in its tool list.
- `permission.skill: {"beta": "deny"}` removed `beta` from the system prompt's `available_skills`,
  while `GET /skill` still listed it.
- A dispose while a session is mid-turn **aborts the turn**: the model stream is cut, the session
  stops at once, and its assistant message ends in `MessageAbortedError` with the partial text.
  Measured on 1.18.4, 2026-10-02 (#619). Waiting for the turn lock is therefore required, not
  caution.
- Two skills with one name: the model is offered exactly one. With copies in the global slot, an
  app's `.opencode/` and the Project root, Chat and Build both get the global copy; without the
  global one, Chat gets the Project root's and Build the app's (#620).

## Rejected

**Per-Thread config by giving each Thread its own directory.** Chat shares `.sage/chat-work` on
purpose (`backend/sage/workspace/threads.py`), and splitting it would change every path Chat's
prompt names. The shim already decides tools per turn, so the toggle goes there.

**`permission.skill` deny as the toggle.** It works, but it is per directory, which here means
per Project, not per Thread.

**Python tools as local MCP servers only.** It works today with no new code, but it asks a person
who wrote one function to write a server.

**Storing credentials in Sage.** It would make Sage a secret store for a value Domino already
manages.

**Leave overlaps to the model.** It costs nothing, and the result changes from turn to turn: an
always-on section is in every prompt while a skill loads only if the model asks for it, so the
built-in usually wins and sometimes blends with the user's.

**Guess the overlap from the skill's text.** Matching a description against "design system" is
the kind of scanner that cannot tell a skill about design from one that mentions it. The person
knows what their skill replaces; the upload asks.

**The Project's skill wins a name collision.** Chosen first, then measured impossible: OpenCode
keeps Sage's copy, and the only ways round it are renaming one of them or not installing Sage's,
which for the global slot means every Project on the machine.

## Consequences

- `_opencode_config_diag` flags any project-slot file Sage did not write that declares MCP servers
  (`shadowing_mcp`). The manifest has to make user extensions read as Sage-written, and the check
  should then flag only a reserved key.
- What a user MCP or tool returns is not covered by Live read's withholding (ADR-0041) or
  data-use rules (ADR-0052). It reaches the model as returned. That follows from trusting the code.
- A disabled skill is hidden from the prompt, but a model that names it anyway can still load it,
  because the `skill` tool runs inside OpenCode, not in the shim.
- Each local MCP server is one process per directory instance, so Chat plus N Built Apps can mean
  N+1 copies.
- Neither revert reaches what a tool does outside the tree it scans: an API call, a database
  write, a message sent, or a file on a Dataset mount, in `/tmp` or gitignored. "Read-only" for a
  user tool means its flag, and nothing Sage checks.

## Rejected (read-only turns)

**Offer every enabled tool and let the revert catch it.** A writing tool then fails the turn, with
a message blaming the agent, and its effects outside the tree happen anyway.

**Keep file writes a user tool made.** It needs a tree hash around every user tool call, gets
ambiguous when the model runs tools in parallel, and changes Ask from "never builds" to "Sage never
builds, your tools might".
