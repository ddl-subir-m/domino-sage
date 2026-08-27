# Building apps in this workspace

A warm **React + TypeScript + Vite** starter. Dependencies are installed and the dev server is
already running with live reload. **Build the user's app by editing `src/`.** The preview reloads
automatically — there is no build step to run.

> **Every turn must end with edits to `src/`.** Do the minimal planning the task needs, then write
> code in the *same* turn — never stop at a plan, a todo list, or a question and wait for the user.
> A turn that produces no file edits has accomplished nothing.
>
> **One exception, and it is narrow.** If the request cannot be acted on at all — it asks about data,
> a file, or a table that is not in this project, and no edit to the app would be an answer — then
> say so plainly in a sentence or two and write nothing. End that reply with `NOTHING_TO_BUILD` on a
> line by itself. Sage reads that line, ends the turn cleanly, and shows the user what you said.
>
> This is for having **nothing** to build. It is not a way to stop at a plan, to ask what to do next,
> to check a decision first, or to put off something awkward — all of those are the failed turn the
> rule above describes, and the marker does not make them succeed. If any part of the request can be
> built, build that part instead and say what you left out.

## Talking to the user
Everything you say back — plans, summaries, answers — is shown directly to the person building the
app, who may not be technical. Keep it plain and friendly:
- Describe the app and what it does, not how the machinery works. Never mention your tools,
  permissions, modes, file access, or "the environment", and never invent tool names.
- Never say you're "blocked" or "unable", and never ask the user to enable, grant, or turn on a
  capability or tool. If you can't do something this turn, say what you'd do in plain terms instead.
  Something the *user* would supply is different, and you should name it: a table or file that isn't
  in the project is a fact about the project, not a capability you lack. Say which one is missing and
  what you'd build once it's there (see `NOTHING_TO_BUILD` above).
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
  editing now. The single exception is a request with nothing in it to build at all, which ends with
  `NOTHING_TO_BUILD` instead (see the top of this file) — never reach for that because a task is
  large, unclear, or would be easier after a question.
- **Do not touch** `vite.config.ts`, `tsconfig*.json`, `package.json`, or `index.html`. The config
  is known-good; regenerating it wastes turns and breaks the preview.
- **Do not touch `src/sageLlm.ts` or `src/sageLlm.config.ts` either.** Sage owns both and rewrites
  them: they hold which language model this app calls, which is chosen in Sage rather than in code.
  Import from them, never edit them. If no model has been chosen, `sageLlm.config.ts` is all nulls
  and there is nothing to fix here — say the app needs a model chosen in Sage.
- **Never run `npm install` / `yarn add` / `pnpm add`.** It does not just fail — it breaks the
  workspace. `node_modules` here is a symlink to a warm, pre-installed copy, and npm refuses to
  write into a symlinked one: it deletes the link *before* it knows whether the install resolves.
  A package that doesn't exist leaves you with no dependencies at all, and the preview then can't
  start. Build with what is already installed (listed under "What exists"); if a task truly can't
  be done without a new package, say so plainly instead of trying to install it.
- Put the app UI in `src/App.tsx` (replace the placeholder). Split into `src/components/` as it grows.
- **Send one edit at a time to a given file.** Several edits to the same file go out in parallel,
  so every one after the first is applied against a file that already changed under it and comes
  back rejected; you then re-read, re-edit, and race yourself again, and the turn makes no
  progress. Change a file, let that change land, then make the next one. Editing *different*
  files at once is fine and still worth doing.
- **`.sage/` is Sage metadata, not your spec.** Never read anything under `.sage/` (plan.md, plan-docs, history, settings) as the current app spec or state — the code in `src/` is the source of truth. The one exception is `.sage/queries.json`, which you write when this app reads a Data Source: it holds the app's SQL, and there is a section below about it whenever there is a Data Source to write it for.
- **Never delete anything under `.sage/` or `public/data/`, whatever the request.** These are not
  yours and they are not "what you built": `.sage/` is Sage's own record of the project, and
  `public/data/` holds the files the user attached — each one a link the user made in the builder,
  with a manifest behind it. A request to start over, reset, or "remove everything you have built"
  means the app's own code (`src/`, and the app files you added), never these. Deleting one takes the
  user's attachment out of the builder: the `@` menu stops offering it, and they have to find and
  attach the file again to say the same sentence. Rewrite `src/App.tsx` instead, and leave the
  attachments where they are — the next turn almost always still wants them.
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
- **Type:** Inter, served from this app's own origin. The `@font-face` at the top of
  `src/index.css` and the file it points at are Sage's — leave both alone, or the app quietly falls
  back to a system font. Scale — page title 28–32px/600, section heading 20px/600, card title
  16px/600, body 14–15px/400, caption 12px. One `<h1>` per screen. Left-align body text.
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

### Charts
- **Series color:** `var(--chart-1)` … `var(--chart-6)`, in order. Never `--ok` / `--warn` /
  `--danger` for a data series — those mean status, so a green bar reads as "this is good" rather
  than "this is revenue".
- **Every series needs an explicit `name`.** Without it recharts renders `name="undefined"` into the
  DOM and the tooltip and legend both say "undefined".
- **`Tooltip`'s `formatter`: leave its parameters unannotated.** recharts types `value` as
  `ValueType | undefined`, and `ValueType` is `number | string | ReadonlyArray<number | string>` —
  so `(value: number)` fails `tsc`, and so does the obvious second guess,
  `(value: number | string | undefined)`, which still misses the readonly array. Let both
  parameters infer and convert inside the body:
  `formatter={(value, name) => ["$" + Number(value).toFixed(2), name]}`. Same for `labelFormatter`.
- **Label it:** axis labels with units, and a title unless the surrounding card already says it.
  Bar-chart y-axes start at zero. Tooltips show the exact value.
- An empty or still-loading chart gets the same treatment as any other collection — see below.

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
- **A screen whose whole data source is unreachable is NOT an empty collection.** An empty list is
  one region with nothing in it; this is every control on the screen going inert at once, and the
  two need opposite treatments. Do not reach for the empty state above by analogy — if this app
  reads a store, "The app's data" below says what to render instead.
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
| `react-router-dom` | More than one view. A single-screen app does not need it. **Give the router a basename** — see below. |
| `date-fns` | Formatting, parsing and date ranges. Import per function (`import { format } from 'date-fns'`). |
| `lucide-react` | Icons. Size them in `em` so they scale with their text. |

**Import from the package root, never from a path inside it.** `import { format } from 'date-fns'`,
not `from 'date-fns/format'`. Every package above is pre-bundled for the preview before you start; a
path inside one is not, so the first import of it makes the dev server rebuild its dependencies and
swap the running module graph underneath the open page. What you get back is a burst of
`ReferenceError: X is not defined` and `Invalid hook call` pointing at code that is perfectly
correct — the page recovers on its own a moment later, but the errors reach you first and describe a
bug that does not exist. If you ever see those two together and the file reads fine, that is what
happened: reload the preview rather than editing anything.

### Routing: the basename is not optional
A published app is served under a path that its own code cannot know at build time. `src/sageBase.ts`
works it out at runtime, so pass it to the router:

```tsx
import { BrowserRouter } from "react-router-dom";
import { sageBase } from "./sageBase";

<BrowserRouter basename={sageBase}>
```

Leave it out and the app works in the preview and shows a blank page once published, because the
router matches the viewer's full path against routes you wrote without the prefix.

There is no UI component kit — no Ant Design, no MUI, no Tailwind. Build components yourself from
the design tokens in `src/index.css`, the way `src/examples/StatCard.tsx` does. If a request seems
to need a package that isn't on this list, build the nearest thing you can from what is here and
tell the user what you left out — do not try to install it.
