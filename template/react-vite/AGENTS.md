# Building apps in this workspace

A warm **React + TypeScript + Vite** starter. Dependencies are installed and the dev server is
already running with live reload. **Build the user's app by editing `src/`.** The preview reloads
automatically — there is no build step to run.

> **Every turn must end with edits to `src/`.** Do the minimal planning the task needs, then write
> code in the *same* turn — never stop at a plan, a todo list, or a question and wait for the user.
> A turn that produces no file edits has accomplished nothing.

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
- **Do not touch** `vite.config.ts`, `tsconfig*.json`, `package.json`, or `index.html` unless the
  user truly needs a new dependency. The config is known-good; regenerating it wastes turns and
  breaks the preview.
- Put the app UI in `src/App.tsx` (replace the placeholder). Split into `src/components/` as it grows.
- TypeScript everywhere. Small, typed components. Prefer plain React + CSS; add a library only if
  the task genuinely needs it.
- **Style with the CSS design tokens** defined in `src/index.css` `:root` (listed below). Reuse
  them — do **not** invent new colors, fonts, shadows, or radii.

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
