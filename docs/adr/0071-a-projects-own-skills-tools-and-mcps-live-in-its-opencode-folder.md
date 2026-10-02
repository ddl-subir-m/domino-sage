---
status: proposed
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

**Three ways in:** a file or zip (a skill folder, a `.ts` or `.py` tool), a git URL (cloned,
recognised pieces copied in, commit recorded), and a form for MCP servers. The form takes a name,
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
names of built-in tools. A user MCP named `sage-live-read` would otherwise replace Live read in
the merged config.

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
- Not measured: a dispose while a session is mid-turn.

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
