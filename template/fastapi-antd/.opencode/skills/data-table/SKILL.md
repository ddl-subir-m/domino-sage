---
name: data-table
description: Build a polished, truncation-safe data table or record list — sortable/filterable columns, a row-detail view, and empty/loading/zero-result/error states, on Ant Design's Table in the workspace theme. Load this when the app shows rows of records in a table, list, grid, or queue (e.g. a review queue, dashboard table, or search results).
metadata:
  stack: fastapi-antd
---

# Data tables in this workspace

Load this when the user's app displays **rows of records** — a table, list, queue, or grid. It
adds table-specific rules on top of `AGENTS.md` (the theme, states, one primary action per
screen). Don't restate those; apply them here.

## Build it with the workspace stack
- `antd.Table`, with `React.createElement` and no JSX. **Do not add a table library** — `Table`
  already sorts, filters, paginates and virtualises, and it is themed for you.
- Put the table in its own component under `static/components/` (e.g. `recordTable.js`, adding
  `window.app.RecordTable`), listed in `static/index.html` above `static/app.js`, not inline in
  `app.js`.
- Describe the columns as data: a `columns` array with `title`, `dataIndex`, `key`, and a
  `render` where a cell needs more than the raw value. Give every row a `key`.

## Columns & cells
- **Never truncate the primary identifier** (name, id, title) — it's how users tell rows apart.
- For other long cells, set `ellipsis: { showTitle: true }` on the column so the full value shows
  on hover and nothing is unreachable. Right-align numeric columns (`align: "right"`); keep units
  in the header, not every cell.
- Give columns comfortable `width`s so truncation is the exception, not the rule.

## Sorting & filtering
- Sort with the column's `sorter` when there are enough rows to warrant it; `Table` shows the
  active direction. Sort locally — the rows are already on the page.
- Filters go **above** the table as Controls (`antd.Select`, `antd.Input.Search`), labeled, and
  update the rows live through `React.useMemo`. When a filter empties the table, that's a
  **zero-result** state (below), not the empty state.

## Row detail
- For per-row detail or actions, prefer `antd.Drawer` (slides over, doesn't compress the table)
  with its close button and Escape-to-close. Don't push the table narrower.
- Keep row actions specific and verb-first ("Approve", "Escalate") and align them in a trailing
  actions column.

## States — required, not optional (this is the #1 polish signal)
- **Loading:** pass `loading: true` to `Table` while data loads — never a blank flash. Wire the
  initial fetch in a mount `React.useEffect` so a non-terminal state always reaches a terminal one.
- **Empty** (no records exist yet): a `locale.emptyText` that explains what the table is, why it's
  empty, and the action to add the first record — with a button. Never render bare headers over
  nothing.
- **Zero-result** (filters matched nothing): distinct from empty — tell the user their filters
  excluded everything and offer to clear them.
- **Error:** `antd.Alert` with a human-readable message plus a retry, in the table's place.

## Accessibility
- `Table` renders a real `<table>` with header cells. Icon-only row actions need an `aria-label`
  and a `title`. Meet AA contrast; never signal status (e.g. a fraud score) by color alone — pair
  it with text or a `Tag`.
