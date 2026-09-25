# Build verification

A completed writer and a clean code check do not prove the app ran. Build pins the app, turn, and
code tree, then starts a fresh preview generation. The supervisor must see a successful app-entry
response before Workbench receives `preview-validation`. The iframe reloads with that validation
ID. Its reporter acknowledges the loaded document and uses the same ID for runtime errors.

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
