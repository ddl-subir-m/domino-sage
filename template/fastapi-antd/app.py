"""This app's server. `app.sh` runs it as `uvicorn app:app --host 0.0.0.0 --port 8888`.

The page and everything it loads are plain files under `static/` — there is no build step. Sage's
half of the server (the page, the static tree, the attachments under `public/data/`, the app's named
queries) is mounted by the one call below. Add this app's own routes underneath it.

Keep routes under `/api/`, return JSON, and let errors print to stdout: Domino shows this process's
output as the App's log, and that log is the only place a problem in a route can be read.
"""
from __future__ import annotations

import sage_serve
from fastapi import FastAPI

app = FastAPI(title="app", docs_url=None, redoc_url=None)
sage_serve.mount(app)


# Your routes go here. For example:
#
# @app.get("/api/summary")
# def summary() -> dict:
#     return {"rows": 0}
