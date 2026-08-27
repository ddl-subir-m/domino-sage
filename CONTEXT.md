# Sage

Sage is a builder that turns a chat conversation into a working app, published on Domino.
This glossary covers the language Sage uses for the Domino things a user can bring into an
app, and for the artefacts Sage produces from them.

## Language

### Domino things a user can pick

**Resource**:
Anything Domino has provisioned that the builder can surface for a user to pick — a Data
Source, a Model API, or an LLM Alias.
_Avoid_: primitive, composable piece, integration, connector

**Data Source**:
A Domino connector to an external store, such as Snowflake or BigQuery, usable by anyone
holding permission on it.
_Avoid_: database, connection, datasource (one word), data connector

**Model API**:
A deployed Domino endpoint serving a conventional data-science model — one that answers a
prediction request rather than a conversation.
_Avoid_: model, endpoint, prediction API, inference endpoint

**LLM Alias**:
A named model registration in the LLM Gateway. It is the only name by which Sage or a built
app refers to a language model.
_Avoid_: model, model name, LLM, deployment

**Asset**:
A Domino Dataset mounted into the project container, holding files. Predates the term
Resource and keeps its own surface in the builder.
_Avoid_: resource, data source, volume

**Domino Artifacts**:
A separate Domino blob store, reachable at `/mnt/artifacts`, that Sage does not use. It is
hydrated at container start and pushed back only on a manual Sync, so it cannot hold live
shared state. Named here only so it is not mistaken for a Sage [[Artifact]], which is an
unrelated thing.
_Avoid_: Artifact, artifacts

### Gateways

**LLM Gateway**:
The deployed Domino App that fronts every language model, whether hosted in Domino or
external. Both Sage and the apps Sage builds reach language models only through it.
_Avoid_: gateway, AI Gateway, proxy

**AI Gateway**:
A separate, MLflow-based Domino feature that Sage does not use. Named here only so it is not
mistaken for the LLM Gateway.
_Avoid_: gateway

**Hosted GenAI Endpoint**:
A language model deployed inside Domino. It is reached through an LLM Alias, never called
directly, so it is a deployment detail rather than something a user picks.
_Avoid_: model API, endpoint, vLLM endpoint

### What Sage produces

**Resource Browser**:
The panel in which a user sees and picks Resources.
_Avoid_: resource panel, data panel, sidebar

**Binding**:
A recorded link between one Built App and a Resource it uses. Picking a Resource produces a
Binding. A Data Source Binding also records a Scope. A Resource is picked once for the Project
and can be bound by several of its Built Apps; a Binding always names exactly one of them, so
"what does this app read" has an answer per app.
_Avoid_: connection, link, reference, wiring

**Scope**:
The database, schema and optionally table a Data Source Binding is read at. Chosen from lists
Sage enumerates, never typed. A Binding may have none, which means the Resource is recorded but
the part of it the app reads is not.
_Avoid_: path, location, target, qualifier, selection

**Named query**:
A statement the app can run, declared by name in the Built App's own repo. The browser sends a name
and parameter values, never SQL. The agent writes them during the build, so what a published app can
ask its Data Source is fixed before anyone opens it.
_Avoid_: query, endpoint, SQL, prepared statement

**Sample rows**:
A few real rows from a bound table, shown to the agent because the creator asked for them. Never a
default and never inferred: the creator picks the tables and chooses whether the rows are treated as
sensitive, which is what decides if the session is [[Sovereign]]. Declining leaves the agent working
from column names and types, which is fully supported.
_Avoid_: preview, sample data, examples, peek

**Attachment**:
A file bound into the Built App, reachable by the app's code. A Binding may also produce an
Attachment, but most do not.
_Avoid_: upload, mount, bundled file

**Built App**:
An app Sage produces for a user, as distinct from Sage itself. It owns its code, its Bindings,
its plan and its build history. It exists from the moment a handoff is confirmed, so an app that
has never been published is still a Built App — publishing gives it a URL, not its existence. A
Project has many. See [ADR-0008](docs/adr/0008-a-project-holds-many-built-apps.md).
_Avoid_: child app, generated app, output, App (unqualified — that is the Domino thing)

**Domino App**:
The deployment Domino runs for a Built App: a container serving it at a URL. Publishing a Built
App creates one, and re-publishing gives that same one a new version, so the URL is stable. A
Domino project can hold many, each started from its own entry script. Distinct from the Built
App, which exists whether or not it has ever been deployed.
_Avoid_: app, Built App, published app, deployment

**Gallery**:
The list of Built Apps a viewer is allowed to see — apps published from Sage Builder
sessions, filtered by that viewer's Domino credentials. It lives in Sage Builder chrome
after the door bounce. Opening an item opens that Built App; it does not switch Project.
_Avoid_: Hub, marketplace, catalog, project list

**Workbench**:
The published Domino App that is Sage. Opening it is how a viewer reaches Chat and Build.
Code and Manage are tabs in the same chrome and are owned by a parallel branch. It is not a
Built App and it is not a Hub.
_Avoid_: workspace (that word already means the Domino builder session), studio, platform,
Built App, Hub

**Project**:
A git-backed Domino project whose Control Plane and git name start with `sage-`. Threads,
Artifacts, Builds, and the Built Apps for that work live in it. It has many of each; it is a
place work lives, not one app. The chip lists only these.
Default is a Project. New project creates another, starts Sage Builder, and lands in Chat.
_Avoid_: folder, sandbox, scratch, workspace (that is Sage Builder)

**Sage Builder**:
The Domino workspace, belonging to this viewer, in a Project where Chat and Build run. The
Workbench App is the door: it starts or resumes that builder, then they work there. Switching
the chip does the same for another Project. Left running unless something stops it. Two
viewers in the same Project each have their own Sage Builder.
_Avoid_: Hub, notebook, App container (that is the Workbench process)

**Chat**:
The Workbench mode for open-ended questions and analysis. It produces Artifacts. It does not
edit `src/`. Driven by the OpenCode agent `sage-chat`.
_Avoid_: ask mode, assistant, sandbox, Jupyter, notebook

**Thread**:
One conversation inside a Project. A Project has many Threads; Default is one Project, not
one Thread. Each Thread has its own OpenCode session and its own history. A Thread points at a
Built App each time a handoff is confirmed: two Threads may drive one Built App, and one Thread
may produce several over its life. It never owns one.
_Avoid_: conversation, session (OpenCode already uses session for the harness object), chat
(that is the mode)

**Default**:
The Sage display name of the caller's one persistent personal Project, created the first
time they open the Workbench, reused thereafter. Naming it changes the chip only; it stays
this viewer's Default. The Domino/git name is `sage-<user-slug>-<id>`, not the word Default.
_Avoid_: Untitled, sandbox, ephemeral, temporary project, scratch

**Artifact**:
A file the chat agent wrote under `examples/<threadId>/` and indexed in that Thread's
manifest — a PNG chart, a table JSON, a query, a note. The directory is named for the role
these files play at handoff, not for the term. Handoff names Artifacts by path; it does not
copy them, and it does not replay a chart object from memory.
_Avoid_: card, chart DSL, canvas, output, widget, Domino Artifacts

**Plan**:
What Sage proposes before it builds, and the document that proposal is kept in. A plan turn
writes one: a short brief — the problem, who opens it, what it does, the screens, what is
deliberately out of scope, and how you know it is done — followed by the numbered build steps
and any open questions. It is durable and versioned. People open it, edit a section, resolve a
question, comment on one, and approve. A separate transient copy is what the build actually
reads, and that copy is archived the moment it does; so "the plan" in conversation means the
document, not the copy. See
[ADR-0007](docs/adr/0007-the-plan-document-is-durable-the-handoff-is-not.md).
_Avoid_: spec, PRD, requirements, ticket, brief, `plan.md` (that is the handoff copy, not the
document)

**Session context**:
The Resources and Artifacts in scope for this Thread right now, shown as chips on the
composer. Distinct from a Binding, which is what the Built App will need to run. A chip is
the Session context row the user can see and remove.
_Avoid_: attachment (that is a file in the Built App), binding (durable app dependency)

### Handling rules

**Shared credential**:
A Data Source credential belonging to a service account rather than a person, so every
Domino user reaches the store as the same principal.
_Avoid_: service credential, global credential

**Individual credential**:
A Data Source credential belonging to one person, so using it on their behalf re-exports
their private access.
_Avoid_: personal credential, user credential

**Sovereign**:
The property of a model call that never leaves Domino. Sage's sovereign slots resolve to LLM
Aliases backed by Hosted GenAI Endpoints.
_Avoid_: private, local, on-prem
