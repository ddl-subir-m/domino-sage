---
status: accepted
---

# The Built App is served by Python, not Node

`template/react-vite/app.sh` still installs and builds with Node — `npm ci && npm run build`
is unchanged. Only the final process changes: instead of `exec npx vite preview`, a Python
server serves the same `dist/` **and** the app's query API. A React app served by Python looks
odd enough to be worth explaining.

## Why not Node

Querying a Domino Data Source is **only** possible over Arrow Flight gRPC. This was verified
against `dominodatalab-data` 6.7.4 rather than assumed:

- The single query call site is `data_sources.py:896-898`, `self.proxy.do_get(flight.Ticket(...))`,
  on a `FlightClient` built from `DOMINO_DATASOURCE_PROXY_FLIGHT_HOST`.
- `datasource-proxy`'s own OpenAPI declares **four** operations, none of which run SQL; its
  HTTP port serves object stores only.
- Domino's own HTTP-recording test for `execute()` captured no query traffic at all, because
  the test substitutes a live `FlightServerBase`.
- No Node, Go, or Java client exists. Domino's R client pip-installs the Python SDK and calls
  it through reticulate.

Node remains technically possible: the wire contract is fully specified — `DoGet` with a
`Ticket` carrying UTF-8 JSON `{datasourceId, sqlQuery, configOverwrites, credentialOverwrites}`
and `x-domino-jwt` gRPC metadata — and third-party Node Flight clients over native gRPC exist.
We rejected it because that contract is private and unversioned (`grpcio` is only a dev
dependency upstream), so a Domino upgrade could break it silently, in apps our users own
rather than in Sage. The official SDK is already preinstalled in the image.

Fetching the warehouse credential and connecting with a native driver is not an option
either: `credentialOverwrites` is populated only for OAuth and AWS IAM roles, and the proxy
injects the real secret server-side.

## Consequences

- One process and one port to supervise, not a Node server plus a Python sidecar.
- The query path authenticates differently from the rest of Sage: `x-domino-jwt` as gRPC
  metadata, where `/v4` metadata calls use `Authorization: Bearer` and the proxy's HTTP port
  uses `X-Domino-Jwt`. Three surfaces, three conventions.
- The cascade's database and schema selection passes as `configOverwrites`
  (`database`, `schema`, `warehouse`, `role`), so generated SQL stays unqualified.
- Unverified prerequisite: everything above was measured in a workspace. The App container
  must also expose the token sidecar at `$DOMINO_API_PROXY/access-token`, which
  `spikes/domino-probes/viewer_identity_app/` is written to confirm.
