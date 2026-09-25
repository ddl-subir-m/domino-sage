# Build verification

A completed writer and a clean code check do not prove the app ran. Build pins the app, turn, and
code tree, then starts a fresh preview generation. An explicit retry reserves that generation
before returning; its scheduled child spawn consumes the same reservation. Later crashes, reloads,
and retries still create new generations. The supervisor must see a successful app-entry
response before Workbench receives `preview-validation`. The iframe reloads with that validation
ID. Its reporter acknowledges the loaded document and uses the same ID for runtime errors.

Before launching a replacement process, the background worker waits up to three seconds for the
old listener to release the preview port. A busy port then produces an explicit startup failure.
Status, Retry, and Stop remain nonblocking; Stop cancels the wait before a new process launches.

Startup and page acknowledgment each have a finite `SAGE_BUILD_PAGE_ACK_WAIT_SECONDS` bound
(default 10 seconds). The existing runtime observation window starts only after acknowledgment
(default 4 seconds, `SAGE_BUILD_RUNTIME_ERROR_WAIT_SECONDS`). Stop cancels these checks and keeps
the written code. A hidden or switched preview, no browser, or a missing reporter leaves the
runtime unverified. A later visit does not reopen the completed turn.

Each repair gets a fresh ID and supervisor generation. Old documents, old attempts, other apps,
and completed checks cannot contribute runtime evidence. Phased Build validates after its final
phase. The synchronous Build API has no page event consumer and reports runtime unverified.

The terminal event carries `verification.overall` and stage outcomes (`passed`, `failed`,
`unverified`, `not_applicable`). `done.ok` means no known terminal failure; it does not mean runtime
verification passed. Workbench and diagnostic exports retain the distinction. An unverified build
says “Code checks passed; runtime not verified.” Python compilation is labeled “Syntax check.”

Written files are kept and recorded as app code even when code, startup, or runtime verification
fails. Failed phases keep their resume point. Saving work does not claim that the app works.

Data validation observes only the page's own query and platform requests. The reporter captures the
validation ID when the document loads and attaches it when each same-origin data request starts.
The proxy captures the app and validation before it waits for a response. An old response cannot
become evidence for a new app, document, or completed turn. Streamed responses remain pending until
the body completes; a body read failure is a failed request.

Read evidence distinguishes success, explicit empty results, failure, pending requests, and no
observed request. Bound data with no observed request, or a request still pending at the deadline,
is unverified. With page checks passed, the UI says “Page checks passed; data access not verified.”
HTTP 200 proves only that a request returned successfully, not business correctness. Empty taxonomy
tags require explicit empty arrays for every requested Dataset. Missing rows or omitted tag fields
do not establish that no tags exist. A mismatched bound/requested Dataset ID is actionable; 401/403
is access failure, while 404 alone does not prove a wrong ID. The platform repair remains one
attempt. Warehouse failures and unasked-source notices still reach the user, and written code stays
saved.

Diagnostic exports retain at most 20 safe read summaries: route without query values, bounded
resource IDs, HTTP status, outcome, and a fixed failure reason. Requests with more than 20 Dataset
IDs carry an identity-truncation flag and cannot establish an empty-tag result. Exports contain no response rows or
request tokens. Existing readable query failure details stay in the query feedback path, outside
these diagnostic records. More reads leave validation unverified. Sage does not replay requests,
click controls, run extra SQL, or make model calls to establish data correctness. Fresh templates
require distinct loading, error/retry, and valid-empty states; saved apps are not rewritten.
