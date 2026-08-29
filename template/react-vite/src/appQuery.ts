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
import { appBase } from "./appBase";

/** A parameter value, in the types a declared parameter may take. A date is written `YYYY-MM-DD`. */
export type QueryParam = string | number | boolean;

/** One query's answer. `columns` names them in order; each row has one value per column, by
 * position. `truncated` is true when the store had more rows than this app will return. */
export type QueryResult = {
  columns: string[];
  rows: (string | number | boolean | null)[][];
  truncated: boolean;
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
 *     const { columns, rows } = await runQuery("usage_by_account", { since: "2026-01-01" });
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
  return { columns: result.columns, rows: result.rows, truncated: Boolean(result.truncated) };
}
