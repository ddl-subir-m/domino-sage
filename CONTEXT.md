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
A recorded link between a Built App and a Resource it uses. Picking a Resource produces a
Binding. A Data Source Binding also records a Scope.
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
The app Sage produces for a user and publishes on Domino, as distinct from Sage itself.
_Avoid_: child app, generated app, output

**Workbench**:
The shell that hosts Chat and Build as modes over one Domino project. Code and Manage are
tabs in the same chrome and are owned by a parallel branch.
_Avoid_: workspace (that word already means the Domino builder session), studio, platform

**Chat**:
The Workbench mode for open-ended questions and analysis. It produces Artifacts. It does not
edit `src/`. Driven by the OpenCode agent `sage-chat`.
_Avoid_: ask mode, assistant, sandbox, Jupyter, notebook

**Thread**:
One conversation inside a project. A project has many Threads; Untitled is one project, not
one Thread. Each Thread has its own OpenCode session and its own history.
_Avoid_: conversation, session (OpenCode already uses session for the harness object), chat
(that is the mode)

**Untitled**:
The display name of the caller's single persistent personal Domino project, provisioned on
first Workbench open, reused thereafter. `.sage/settings.json` carries `"untitled": true`
until the user names it. Files are real; nothing is ephemeral.
_Avoid_: Personal sandbox, ephemeral, temporary project

**Artifact**:
A file the chat agent wrote under `examples/<threadId>/` and indexed in that Thread's
manifest — a PNG chart, a table JSON, a query, a note. Handoff copies Artifacts; it does not
replay a chart object from memory.
_Avoid_: card, chart DSL, canvas, output, widget

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
