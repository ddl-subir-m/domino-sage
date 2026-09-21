// The app's data, asked for by name (#13, #14).
//
// The browser sends a query NAME and parameter values. It never sends SQL, and there is no route
// that would accept any: the statement lives in `.sage/queries.json` in this app's own repo, and the
// server looks it up. That is the boundary, not a convenience — a published app reads its Data
// Source through a credential shared by every viewer, so an endpoint that ran arbitrary SQL would
// make this app a warehouse console for everyone it is shared with.
//
// Same origin, so there is no key here and no CORS: the request goes to the server that served this
// page, which holds the Data Source connection.
//
// Sage owns this file. Do not edit it — which Data Source this app reads is chosen in Sage, and the
// queries it can run are declared in `.sage/queries.json`.
window.sage = window.sage || {};

(function () {
  // The preview answers queries too: Sage runs the app's own query module beside the preview and
  // its proxy sends `/api/queries/*` there. A 404 carrying NO JSON body did not come from it, which
  // always names what it refused — nothing was there to answer, because no Data Source is bound or
  // the query server did not come up. Both leave the app with data it cannot reach.
  const NOT_SERVED =
    "This app's data isn't available. Check that its Data Source is still bound in Sage.";

  /**
   * Run one of this app's named queries.
   *
   * Throws an `Error` whose `message` is written for the viewer — show it as it is rather than
   * replacing it, because the reasons need opposite responses (wait and retry, ask for access, tell
   * whoever published the app) and one generic sentence sends everyone down the wrong one.
   *
   *     const { columns, rows } = await sage.runQuery("usage_by_account", { since: "2026-01-01" });
   *
   * `columns` names them in order; each row has one value per column, by position. `truncated` is
   * true when the store had more rows than this app will return. `dataUsed` is the source and
   * coverage evidence to show beside local tables, charts and model-assisted text.
   */
  sage.runQuery = async function runQuery(name, params = {}, options = {}) {
    const url = sage.url("api/queries/" + encodeURIComponent(name));
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ params }),
        signal: options.signal,
      });
    } catch (error) {
      if (error && error.name === "AbortError") throw error;
      throw new Error("This app could not reach its data. Check your connection and try again.");
    }

    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const message = body && body.error;
      throw new Error(message || (response.status === 404 ? NOT_SERVED : "This app could not read its data."));
    }
    if (!body || !Array.isArray(body.columns) || !Array.isArray(body.rows)) {
      throw new Error("This app's data came back in a form it could not read.");
    }
    const truncated = Boolean(body.truncated);
    return {
      columns: body.columns,
      rows: body.rows,
      truncated,
      dataUsed: normalizeDataUsed(name, body, truncated),
    };
  };

  function normalizeDataUsed(name, result, truncated) {
    const raw = result.dataUsed || {};
    const source = raw.source || {};
    const coverage = raw.coverage || {};
    const text = (v) => (typeof v === "string" && v ? v : null);
    return {
      kind: "data_source_query",
      query: text(raw.query) || name,
      source: { id: text(source.id), name: text(source.name), scope: text(source.scope) },
      coverage: {
        columns: Array.isArray(coverage.columns) ? coverage.columns.map(String) : result.columns,
        returnedRows: typeof coverage.returnedRows === "number" ? coverage.returnedRows : result.rows.length,
        truncated,
      },
      observedTransfer:
        raw.observedTransfer === "query_result_returned_to_viewer" ? "query_result_returned_to_viewer" : "unknown",
      modelView: raw.modelView === "not_sent_to_model_by_query" ? "not_sent_to_model_by_query" : "unknown",
    };
  }
})();
