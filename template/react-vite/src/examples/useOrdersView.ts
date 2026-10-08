// The state and queries of the example screen in `./OrdersScreen.tsx`, apart from how they are drawn.
// Its queries are the fixture queries declared beside it in `orders.queries.json`, which this app does
// not have: copy the shape, and ask the queries in this app's own `.sage/queries.json`.
import { useState } from "react";
import { useDebouncedValue, useQuery } from "../appQuery";
import type { QueryState, QueryValue } from "../appQuery";

export const PAGE_SIZE = 50;

type Row = Record<string, QueryValue>;

// Records keyed the way the store spells the columns (`REGION` on Snowflake), read lower-case.
const lowerKeys = (record: Row): Row =>
  Object.fromEntries(Object.entries(record).map(([k, v]) => [k.toLowerCase(), v]));
const rowsOf = (q: QueryState): Row[] => (q.data ? q.data.records.map(lowerKeys) : []);

export function useOrdersView() {
  const [draft, setDraft] = useState("");
  const search = useDebouncedValue(draft.trim());
  const [region, setRegion] = useState("__all__");
  // The page belongs to one filter: a new search or region starts again at the first page.
  const filter = JSON.stringify([search, region]);
  const [paging, setPaging] = useState({ filter, page: 0 });
  const page = paging.filter === filter ? paging.page : 0;

  const regions = useQuery("example_order_regions");
  const summary = useQuery("example_orders_summary", { region, search });
  const detail = useQuery("example_orders_page", { region, search, offset: page * PAGE_SIZE });
  const counted = rowsOf(summary)[0];

  return {
    draft, setDraft, region, setRegion, page,
    setPage: (next: number) => setPaging({ filter, page: next }),
    regions, summary, detail,
    regionChoices: rowsOf(regions).map((r) => String(r.region)),
    rows: rowsOf(detail),
    total: counted ? Number(counted.order_count) : null,
    amount: counted ? Number(counted.total_amount) : null,
  };
}
