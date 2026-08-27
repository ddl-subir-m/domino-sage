# Workbench

Chat and Build as modes over one Domino project. Manage and Code are tabs in the same chrome and
are owned by a parallel branch.

| Doc | What it decides |
|-----|-----------------|
| [ADR-0003](../adr/0003-workbench-chat-and-untitled.md) | Lift the prototype shell; artifacts are files |
| [ADR-0004](../adr/0004-workbench-is-the-door.md) | Workbench App is the door; Chat and Build run in this viewer's Sage Builder |
| [ADR-0007](../adr/0007-the-plan-document-is-durable-the-handoff-is-not.md) | The plan page is a real document; `plan.md` stays the transient copy the build reads |
| [chat.md](chat.md) | `sage-chat`, Thread storage, Artifact layout, chips / Session context |
| [handoff.md](handoff.md) | Detect once, suggest, file payload, then existing `sage-plan` / `sage-implement` |
| [brand.md](brand.md) | OEM chrome + voice; Domino default keeps AI Workbench / Sage split |
| [template/chat/AGENTS.md](../../template/chat/AGENTS.md) | Prompt body for `sage-chat` (source of truth; inline into `opencode.json`) |

Language: [CONTEXT.md](../../CONTEXT.md). Mock: `etanlightstone/sage_explorations`.
