# Build verification

A completed writer and a clean code check do not prove the app ran. While a Build turn writes, its
app's preview restarts on none of the writes; it restarts once when the turn ends, unless the check
below has already restarted it on the final code. Build pins the app and turn, then starts a fresh
preview generation, and pins the code tree that generation started from: the restart's own
writes, such as `sage_keys.json`, are part of the code it checks. An explicit retry reserves that generation
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

The headless check also reports what each screen it opened showed: its tab label, the charts and
tables it drew, whether it was still loading, and its visible text. An approved plan's review
(#716) reads that beside each step's Verify and the plan's Screens, and only the text the app's own
code spells goes to the model, so no row from a read reaches it. A step the review names unmet is
sent back once. The pass that answers that repair is reviewed again, and a step still unmet ends
the build "Incomplete — plan step N not met", never clean. A screen the walk did not open, such as
one reached only from a table row, is judged from the code alone (#750). The review also gets the
data reads the page made, by path and outcome only, so a section whose read never happened or came
back empty is visible; and since every screen is read as it first opens, an error message there
(two default periods that overlap) fails the step whose defaults produced it. The review runs even
when another plan repair (an unwritten step) spent the one repair; it then reports and does not
repair. The diag log says, per turn, how many reviews ran and which steps are still unmet (#765).

The review also reads the plan's overall Done when and Not doing sections, and the named, enabled
Project skills supplied to the build. These requirements can specify defaults that no step repeats.
Query definitions are code: the review receives their SQL and parameter names and types, with no
stored rows or parameter values. A change to only the query catalog still runs the review.
Each phase receives the overall app requirements beside its own brief. The final phase runs the
same whole-plan checks as an ordinary approved build, with one repair and a second review; an unmet
requirement cannot become a successful phased build or reset the repair budget with a phase retry.
If the review times out, fails, or returns an unreadable answer, the app is kept and the turn can
finish. Its verification remains unverified, and both the transcript and Build history say that
the plan review was not completed. It is not reported as a clean build.

A screen the walk opened whose plan step names catalog queries, by a name in backticks or one
holding `_` or `-`, and which asked for none of them as it first opened, fails the data stage. Its
`verification.reason` names the screen and the step (#765). A query the app calls by name only
through `runQuery`, never `useQuery`, is not held to the open: the walk presses no button, and a
button's handler cannot call the `useQuery` hook (#767).

Each repair gets a fresh ID and supervisor generation. Old documents, old attempts, other apps,
and completed checks cannot contribute runtime evidence. Phased Build validates after its final
phase. The synchronous Build API has no page event consumer and reports runtime unverified.

The terminal event carries `verification.overall` and stage outcomes (`passed`, `failed`,
`unverified`, `not_applicable`). `done.ok` means no known terminal failure; it does not mean runtime
verification passed. Workbench and diagnostic exports retain the distinction. An unverified build
says “Code checks passed; runtime not verified.” Python compilation is labeled “Syntax check.”
`verification.reason` says why runtime went unverified when Sage knows: the preview did not start
or did not load the changed page within the wait, the build was stopped, or another app was
opened. The status line appends it.

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
