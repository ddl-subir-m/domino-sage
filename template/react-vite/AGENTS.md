# Building apps in this workspace

A warm **React + TypeScript + Vite** starter. Dependencies are installed and the dev server is
already running with live reload. **Build the user's app by editing `src/`.** The preview reloads
automatically — there is no build step to run.

> **Every turn must end with edits to `src/`.** Do the minimal planning the task needs, then write
> code in the *same* turn — never stop at a plan, a todo list, or a question and wait for the user.
> A turn that produces no file edits has accomplished nothing.

## Talking to the user
Everything you say back — plans, summaries, answers — is shown directly to the person building the
app, who may not be technical. Keep it plain and friendly:
- Describe the app and what it does, not how the machinery works. Never mention your tools,
  permissions, modes, file access, or "the environment", and never invent tool names.
- Never say you're "blocked" or "unable", and never ask the user to enable, grant, or turn on a
  capability or tool. If you can't do something this turn, say what you'd do in plain terms instead.
- Say each thing once — don't repeat yourself.
- Describe the app from the user's point of view — what they'll get. Never mention the starter,
  scaffold, or "placeholder", or that you're replacing or filling in existing code. To the user
  it's simply the app being built.
- Write plans as natural prose, not a list of look-alike sentences. Don't begin every sentence the
  same way (e.g. "I will… I will… I will…"); vary the phrasing and describe the app, not a
  step-by-step narration of your own actions.

## Earlier turns
`.sage/history.md` is a record of what happened in this project before now — what the user asked
for, what you proposed, which steps ran.
- If you are unsure whether something was already asked for, already built, or already rejected,
  **grep `.sage/history.md` before you guess or ask the user again.** Long builds drop older detail
  out of view; this file is how you get it back.
- It is a past record, **not** current intent. `.sage/plan.md` is the live plan — don't treat an old
  turn as an instruction for this one.
- Don't edit it. It is regenerated each turn, so any change is overwritten.

## Project rules
- **Plan proportionally, then build.** Match the planning to the task. A simple app (one screen, a
  list, a form) needs only a quick mental plan — just start building. A complex one (multiple views,
  non-trivial state or data shape) deserves a short up-front pass over components, state, and the
  states below. Don't over-plan small tasks; a one-line prompt rarely needs a 7-step todo list.
  Either way, plan once and then implement in a sustained pass — avoid drifting back into
  re-planning unless requirements actually change.
- **Implement in the same turn — planning alone is a failed turn.** A turn that only writes a todo
  list, describes an approach, or asks what to do next without editing files under `src/` has
  accomplished nothing. Do the minimal planning the task needs, then **edit `src/App.tsx` (and any
  other files) in that same turn** — never stop to wait for confirmation before writing code. If you
  find yourself planning a second time without having written anything, stop planning and start
  editing now.
- **Do not touch** `vite.config.ts`, `tsconfig*.json`, `package.json`, or `index.html`. The config
  is known-good; regenerating it wastes turns and breaks the preview.
- **Never run `npm install` / `yarn add` / `pnpm add`.** It does not just fail — it breaks the
  workspace. `node_modules` here is a symlink to a warm, pre-installed copy, and npm refuses to
  write into a symlinked one: it deletes the link *before* it knows whether the install resolves.
  A package that doesn't exist leaves you with no dependencies at all, and the preview then can't
  start. Build with what is already installed (listed under "What exists"); if a task truly can't
  be done without a new package, say so plainly instead of trying to install it.
- Put the app UI in `src/App.tsx` (replace the placeholder). Split into `src/components/` as it grows.
- **`.sage/` is Sage metadata, not your spec.** Never read anything under `.sage/` (plan.md, history, settings) as the current app spec or state — the code in `src/` is the source of truth.
- TypeScript everywhere. Small, typed components. Plain React + CSS is the default, and the
  installed packages are the whole toolbox — there is no adding to it mid-build.
- **Style with the CSS design tokens** defined in `src/index.css` `:root` (listed below). Reuse
  them — do **not** invent new colors, fonts, shadows, or radii.
- **A component carries its own styles** — colocated inline styles or its own `.css` next to it, as
  `src/examples/StatCard.tsx` does. Keep component rules OUT of `src/index.css`, which holds the
  tokens, resets and global scaffolding that everything else depends on. Adding to it means reading a
  file that grows with every component just to find somewhere safe to insert, and two components can
  quietly claim the same class name. Editing a token, a font or the reset there is still fine — that
  is what it's for.

## Design system — build a polished product, not a prototype

Every app must look intentional and consistent. These rules are what separate a crafted UI from a
"vibe-coded" one. Follow them even when the user doesn't ask.

### Use the tokens (defined in `src/index.css`)
- **Color:** `var(--accent)` (Domino purple `#543FDE`) for primary actions and links;
  `var(--text)` / `var(--text-muted)` for copy; `var(--border)` for dividers and input borders;
  `var(--bg)` / `var(--surface)` for backgrounds; `var(--ok)` / `var(--warn)` / `var(--danger)`
  for status. **Never hardcode hex values** — use the variables so light and dark themes both work.
- **Type:** Inter. Scale — page title 28–32px/600, section heading 20px/600, card title 16px/600,
  body 14–15px/400, caption 12px. One `<h1>` per screen. Left-align body text.
- **Spacing:** 8px grid (4 / 8 / 12 / 16 / 24 / 32). Space **within** a group ≈ half the space
  **between** groups. Be generous; don't crowd elements.
- **Radius & shadow:** use `var(--radius)` and `var(--shadow)`; keep them consistent everywhere.

### Layout & components
- **One clear primary action** per screen — a filled `--accent` button. Everything else is
  secondary (outline) or a link. Never place two filled primary buttons side by side.
- Buttons and labels **start with a verb** and are specific ("Add ingredient", not "Submit").
- **Cards:** consistent padding (16–24px), 1px `--border`, `--radius`, subtle `--shadow`. Use them
  to group related content.
- **Inputs:** label *above* the field (not placeholder-as-label); visible focus ring in `--accent`;
  validate on blur, not on every keystroke.
- Cap main content width (~64–72rem) and center it on large screens, but let it fill smaller ones.
  Comfortable line length is 50–75 characters.

### States — do not skip these (this is the #1 polish signal)
Scope these to the screens/collections the current request actually touches — don't add them to
components outside what was asked.
- **Empty state:** for a list/collection you're building or editing that can be empty, add one that
  says *what it is*, *why it's empty*, and *the action to fill it* — with a button. Never render a
  blank area.
- **Loading:** show a spinner or skeleton for async work; never a blank flash. If you drive the UI
  with a `loading`/`ready`/`empty`/`error` state machine, **wire the initial load in a mount
  `useEffect`** — a loader defined but only called from a retry button leaves the page stuck on the
  spinner forever. Every non-terminal state must have a code path that reaches a terminal one.
- **Error:** a human-readable message plus how to recover.
- **Interactive elements:** hover and focus styles; explain disabled states.

### Accessibility & restraint
- Meet AA color contrast; never rely on color alone to convey meaning.
- Icon-only buttons need an `aria-label` (and a `title` for tooltip).
- Respect `prefers-color-scheme` — the tokens already define dark values.
- No gratuitous gradients, no clashing accent colors, no inconsistent corner radii. Restraint reads
  as quality.

## What exists
- `src/App.tsx` — entry component (currently a placeholder to replace).
- `src/components/` — put reusable components here.
- `src/examples/StatCard.tsx` — a golden example: a small, typed, token-styled component. Copy its shape.

### Installed packages — this is the whole toolbox
Import these directly. They are already installed; nothing else is, and nothing else can be added.

| Package | Use it for |
|---------|-----------|
| `react`, `react-dom` | Everything. Plain React + CSS is still the default. |
| `recharts` | Charts. Line, area, bar, pie. Feed it the design tokens for colors — don't take its defaults. |
| `react-router-dom` | More than one view. A single-screen app does not need it. |
| `date-fns` | Formatting, parsing and date ranges. Import per function (`import { format } from 'date-fns'`). |
| `lucide-react` | Icons. Size them in `em` so they scale with their text. |

There is no UI component kit — no Ant Design, no MUI, no Tailwind. Build components yourself from
the design tokens in `src/index.css`, the way `src/examples/StatCard.tsx` does. If a request seems
to need a package that isn't on this list, build the nearest thing you can from what is here and
tell the user what you left out — do not try to install it.
