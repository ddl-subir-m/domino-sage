// A working example of a screen with a delayed search, a select and a paged table. Copy its shape,
// not its names: nothing renders this file, and its queries are fixtures (see `./useOrdersView.ts`).
// A real screen lives in `src/screens/` and asks the queries in this app's own `.sage/queries.json`.
import { StatCard } from "./StatCard";
import { PAGE_SIZE, useOrdersView } from "./useOrdersView";

const COLUMNS = ["order_id", "placed_on", "customer", "region", "amount"];

function OrdersScreen() {
  const v = useOrdersView();
  const pages = v.total ? Math.ceil(v.total / PAGE_SIZE) : 0;
  return (
    <main>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <input type="search" placeholder="Search customers" value={v.draft}
               onChange={(e) => v.setDraft(e.target.value)} />
        <select value={v.region} onChange={(e) => v.setRegion(e.target.value)}
                disabled={v.regions.status === "error"}>
          <option value="__all__">All regions</option>
          {v.regionChoices.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>

      {v.summary.status === "error" ? <p role="alert">{v.summary.error}</p> : (
        <div style={{ display: "flex", gap: 12 }}>
          <StatCard label="Orders" value={v.total ?? "…"} />
          <StatCard label="Amount" value={v.amount === null ? "…" : v.amount.toFixed(2)} />
        </div>
      )}

      {v.detail.status === "error" ? <p role="alert">{v.detail.error}</p>
        : v.detail.status === "loading" ? <p>Loading…</p>
        : v.detail.status === "empty" ? <p>No orders match this search.</p> : (
          <table>
            <thead><tr>{COLUMNS.map((c) => <th key={c}>{c.replace("_", " ")}</th>)}</tr></thead>
            <tbody>
              {v.rows.map((row) => (
                <tr key={String(row.order_id)}>{COLUMNS.map((c) => <td key={c}>{String(row[c] ?? "")}</td>)}</tr>
              ))}
            </tbody>
          </table>
        )}

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button disabled={v.page === 0} onClick={() => v.setPage(v.page - 1)}>Previous</button>
        <span>Page {pages ? v.page + 1 : 0} of {pages}</span>
        <button disabled={v.page + 1 >= pages} onClick={() => v.setPage(v.page + 1)}>Next</button>
      </div>
    </main>
  );
}

export default OrdersScreen;
