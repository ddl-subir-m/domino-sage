// The app's data, asked for by name (#13, #14).
//
// The browser sends a query NAME and parameter values. It never sends SQL, and there is no route
// that would accept any: the statement lives in `.sage/queries.json` in this app's own repo, and
// `serve.py` looks it up. That is the boundary, not a convenience — a published app reads its Data
// Source through a credential shared by every viewer, so an endpoint that ran arbitrary SQL would
// make this app a warehouse console for everyone it is shared with.
//
// Same origin, so there is no key here and no CORS: the request goes to the server that served this
// page, which holds the Data Source connection.
//
// Sage owns this file. Do not edit it — which Data Source this app reads is chosen in Sage, and the
// queries it can run are declared in `.sage/queries.json`.
import { useCallback, useEffect, useRef, useState } from "react";
import { appBase } from "./appBase";
import { reportRuntimeError } from "./reportRuntimeError";

/** A parameter value, in the types a declared parameter may take. A date is written `YYYY-MM-DD`. */
export type QueryParam = string | number | boolean;

/** Evidence about the data this query used. It describes the boundary and coverage, not row values. */
export type QueryDataUse = {
  kind: "data_source_query";
  query: string;
  source: {
    id: string | null;
    name: string | null;
    scope: string | null;
  };
  coverage: {
    columns: string[];
    returnedRows: number;
    truncated: boolean;
  };
  observedTransfer: "query_result_returned_to_viewer" | "unknown";
  modelView: "not_sent_to_model_by_query" | "unknown";
};

/** A single value in a query's answer. */
export type QueryValue = string | number | boolean | null;

/** One query's answer. `columns` names them in order; each row has one value per column, by
 * position. `records` is the same rows as objects keyed by those names, spelled the way the store
 * spells them. `truncated` is true when the store had more rows than this app will return.
 * `dataUsed` is the source and coverage evidence to show beside local tables, charts and
 * model-assisted text. */
export type QueryResult = {
  columns: string[];
  rows: QueryValue[][];
  records: Record<string, QueryValue>[];
  truncated: boolean;
  dataUsed: QueryDataUse;
};

type QueryDataUsePayload = {
  query?: unknown;
  source?: { id?: unknown; name?: unknown; scope?: unknown };
  coverage?: { columns?: unknown; returnedRows?: unknown };
  observedTransfer?: unknown;
  modelView?: unknown;
};

// Since #24 the preview answers queries too — Sage runs the very same `serve.py` beside the dev
// server and its proxy sends `/api/queries/*` there. So this no longer means "not published yet".
//
// The test is unchanged and still right: a 404 carrying NO JSON body did not come from `serve.py`,
// which always names what it refused. It came from Vite, which means nothing was there to intercept
// the call — no Data Source bound, or the query server did not come up. Both leave the app with data
// it cannot reach, which is what to say.
const NOT_SERVED =
  "This app's data isn't available. Check that its Data Source is still bound in Sage.";

/**
 * Run one of this app's named queries.
 *
 * Throws an `Error` whose `message` is written for the viewer — show it as it is rather than
 * replacing it, because the reasons need opposite responses (wait and retry, ask for access, tell
 * whoever published the app) and one generic sentence sends everyone down the wrong one.
 *
 *     const { columns, rows, records } = await runQuery("usage_by_account", { since: "2026-01-01" });
 */
export async function runQuery(
  name: string,
  params: Record<string, QueryParam> = {},
  options: { signal?: AbortSignal } = {},
): Promise<QueryResult> {
  const url = `${appBase.replace(/\/$/, "")}/api/queries/${encodeURIComponent(name)}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params }),
      signal: options.signal,
    });
  } catch (error) {
    if ((error as Error)?.name === "AbortError") throw error;
    throw new Error("This app could not reach its data. Check your connection and try again.");
  }

  const body = (await response.json().catch(() => null)) as { error?: string } | QueryResult | null;
  if (!response.ok) {
    const message = (body as { error?: string } | null)?.error;
    throw new Error(message || (response.status === 404 ? NOT_SERVED : "This app could not read its data."));
  }
  const result = body as QueryResult | null;
  if (!result || !Array.isArray(result.columns) || !Array.isArray(result.rows)) {
    throw new Error("This app's data came back in a form it could not read.");
  }
  const truncated = Boolean(result.truncated);
  return {
    columns: result.columns,
    rows: result.rows,
    records: recordsOf(name, result.columns, result.rows),
    truncated,
    dataUsed: normalizeDataUsed(name, result, truncated),
  };
}

/** What `useQuery` reports. `"empty"` means the query answered with zero records, never that it
 * failed. `error` is the viewer's sentence. `refreshing` is true while `refresh()` asks again with
 * the previous `data` still on screen. */
export type QueryState = {
  status: "loading" | "error" | "empty" | "ready";
  data: QueryResult | null;
  error: string | null;
  refreshing: boolean;
  refresh: () => void;
};

// Answers kept for the page's life, keyed by query name and params in a fixed order, so a screen
// shown again draws at once. Only answers are kept; a failure is asked again next time.
const answers = new Map<string, QueryResult>();
const keyOf = (name: string, params: Record<string, QueryParam>) =>
  JSON.stringify([name, Object.keys(params).sort().map((k) => [k, params[k]])]);

type Held = { key: string | null; data: QueryResult | null; error: string | null; pending: boolean };

/**
 * Run a named query from a component and track its state (#681).
 *
 *     const q = useQuery("usage_by_account", { since });
 *     if (q.status === "ready") draw(q.data.records);
 *
 * A changed param or an unmount aborts the request in flight. `{ enabled: false }` sends nothing
 * (status stays `"loading"`) until a value the query needs exists. Answers are kept for the page's
 * life; `refresh()` asks again.
 */
export function useQuery(
  name: string,
  params: Record<string, QueryParam> = {},
  opts: { enabled?: boolean } = {},
): QueryState {
  const key = keyOf(name, params);
  const enabled = opts.enabled !== false;
  const [run, setRun] = useState(0);
  const [state, setState] = useState<Held>({ key: null, data: null, error: null, pending: false });
  const latest = useRef(params);
  latest.current = params;
  const lastRun = useRef(0);

  useEffect(() => {
    if (!enabled) return undefined;
    const forced = run !== lastRun.current;
    lastRun.current = run;
    const kept = answers.get(key);
    if (kept && !forced) {
      setState({ key, data: kept, error: null, pending: false });
      return undefined;
    }
    const controller = new AbortController();
    setState((s) => ({ key, data: s.key === key ? s.data : kept || null, error: null, pending: true }));
    runQuery(name, latest.current, { signal: controller.signal }).then(
      (data) => {
        answers.set(key, data);
        setState({ key, data, error: null, pending: false });
      },
      (error) => {
        if ((error as Error)?.name === "AbortError") return;
        setState((s) => ({ key, data: s.key === key ? s.data : null,
                           error: (error as Error)?.message || String(error), pending: false }));
      });
    return () => controller.abort();
  }, [key, name, enabled, run]);

  const refresh = useCallback(() => setRun((n) => n + 1), []);
  const kept = answers.get(key) || null;
  const view: Held = state.key === key ? state : { key, data: kept, error: null, pending: enabled && !kept };
  const status = view.error ? "error"
    : view.data ? (view.data.records.length ? "ready" : "empty") : "loading";
  return { status, data: view.data, error: view.error, refreshing: view.pending && !!view.data, refresh };
}

// A read of a name the query does not return is `undefined`, which a page draws as 0 or an empty
// state over rows that are there (#673). Report it the way a crash is reported, once per name, so
// the build that wrote the read is told which names exist. Not reads: symbols, Object.prototype
// names, `toJSON` (JSON.stringify), `then` (await), and `key` and `children`, which antd's Table
// reads off every row.
const PROBES = new Set(["toJSON", "then", "key", "children"]);

function recordsOf(name: string, columns: string[], rows: QueryValue[][]): Record<string, QueryValue>[] {
  const reported = new Set<string>();
  return rows.map((row) => new Proxy(Object.fromEntries(columns.map((c, i) => [c, row[i]])), {
    get(record, key, receiver) {
      if (typeof key === "string" && !(key in record) && !PROBES.has(key) && !reported.has(key)) {
        reported.add(key);
        reportRuntimeError(`query ${name} has no column '${key}'; columns are ${columns.join(", ")}`,
                           new Error().stack);
      }
      return Reflect.get(record, key, receiver);
    },
  }));
}

function normalizeDataUsed(name: string, result: QueryResult, truncated: boolean): QueryDataUse {
  const raw = result.dataUsed as QueryDataUsePayload | undefined;
  const source = raw?.source ?? {};
  const coverage = raw?.coverage ?? {};
  return {
    kind: "data_source_query",
    query: typeof raw?.query === "string" && raw.query ? raw.query : name,
    source: {
      id: typeof source.id === "string" && source.id ? source.id : null,
      name: typeof source.name === "string" && source.name ? source.name : null,
      scope: typeof source.scope === "string" && source.scope ? source.scope : null,
    },
    coverage: {
      columns: Array.isArray(coverage.columns) ? coverage.columns.map(String) : result.columns,
      returnedRows: typeof coverage.returnedRows === "number" ? coverage.returnedRows : result.rows.length,
      truncated,
    },
    observedTransfer:
      raw?.observedTransfer === "query_result_returned_to_viewer"
        ? "query_result_returned_to_viewer"
        : "unknown",
    modelView: raw?.modelView === "not_sent_to_model_by_query" ? "not_sent_to_model_by_query" : "unknown",
  };
}
