---
name: data-table
description: Build a polished, truncation-safe data table or record list — sortable/filterable columns, a row-detail view, and empty/loading/zero-result/error states, styled with the workspace design tokens. Load this when the app shows rows of records in a table, list, grid, or queue (e.g. a review queue, dashboard table, or search results).
metadata:
  stack: react-vite-ts
---

# Data tables in this workspace

Load this when the user's app displays **rows of records** — a table, list, queue, or grid. It
adds table-specific rules on top of `AGENTS.md` (design tokens, states, one primary action per
screen). Don't restate those; apply them here.

## Build it with the workspace stack
- Plain React + TypeScript + CSS with the tokens in `src/index.css`. **Do not add a table
  library** (AG Grid, MUI DataGrid, TanStack Table) unless the task genuinely needs virtualization
  for thousands of rows — a typed `<table>` covers the vast majority of apps and keeps the preview
  fast.
- Put the table in its own typed component under `src/components/` (e.g. `RecordTable.tsx`), not
  inline in `App.tsx`.

## Columns & cells
- **Never truncate the primary identifier** (name, id, title) — it's how users tell rows apart.
- For other long cells, truncate with ellipsis **and** show the full value on hover via `title` so
  nothing is unreachable. Right-align numeric columns; keep units in the header, not every cell.
- Give columns comfortable default widths so truncation is the exception, not the rule.

## Sorting & filtering
- Make column headers sortable when there are enough rows to warrant it; show the active
  sort direction with an arrow. Sort in local state — the data is already loaded from
  `public/data/`.
- Filters go **above** the table, labeled, and update the rows live. When a filter empties the
  table, that's a **zero-result** state (below), not the empty state.

## Row detail
- For per-row detail or actions, prefer an **overlay side drawer** (slides over, doesn't compress
  the table) with a close button and Escape-to-close. Don't push the table narrower.
- Keep row actions specific and verb-first ("Approve", "Escalate") and align them in a trailing
  actions column.

## States — required, not optional (this is the #1 polish signal)
- **Loading:** skeleton rows or a spinner in the table body while data loads — never a blank flash.
  Wire the initial fetch in a mount `useEffect` so a non-terminal state always reaches a terminal one.
- **Empty** (no records exist yet): explain what the table is, why it's empty, and the action to
  add the first record — with a button. Never render bare headers over nothing.
- **Zero-result** (filters matched nothing): distinct from empty — tell the user their filters
  excluded everything and offer to clear them.
- **Error:** a human-readable message plus a retry, in the table's place.

## Accessibility
- Use a real `<table>` with `<th scope="col">` headers. Icon-only row actions need an `aria-label`
  and a `title`. Meet AA contrast; never signal status (e.g. a fraud score) by color alone — pair
  it with text or an icon.
