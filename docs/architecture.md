# Architecture

## Components

The service has four explicit layers:

1. Starlette/Uvicorn owns HTTP, health routes, correlation IDs, and startup/shutdown.
2. Transport security and Bearer middleware validate `Host`, `Origin`, header syntax, and the
   presented key before MCP parses a message.
3. One `MCPServer` instance dispatches 28 allowlisted reporting, sales, and leave tools.
4. `DolibarrClient` owns one `httpx2.AsyncClient` pool, the fixed `/users/info` operation, and
   fixed project, third-party, contact-relation, user-label, time-entry, leave-type, and
   leave-request REST operations.

The parent lifespan opens the shared HTTP client and MCP session manager together. Shutdown closes
both. The MCP transport uses `stateless_http=True` and JSON responses. Current (2026-era) requests
are inherently self-contained; stateless mode also avoids retaining legacy transport sessions.

## Request flow

1. The outer request middleware generates an opaque request ID.
2. Host/Origin validation rejects DNS-rebinding candidates before an upstream request.
3. The auth backend extracts exactly one Bearer token from raw ASGI headers.
4. The backend calls the fixed Dolibarr identity URL with a per-call `DOLAPIKEY` header.
5. The validated upstream model is projected to `VerifiedIdentity`.
6. The authentication backend stores the verified key only in the active request's private,
   mutable credential holder; the MCP `AccessToken` still contains only a redacted marker.
7. SDK `AuthContextMiddleware` scopes the safe identity and the private holder scopes the key to
   the active request.
8. `dolibarr_whoami` reads the existing identity and performs no second Dolibarr call.
9. Reporting, sales, and leave tools attach the same key only to individual, fixed-path API
   requests. Dolibarr authorizes every read or write with the caller's actual permissions.
10. The credential holder is cleared in `finally` and all context references are released when the
    request ends.

All methods and subpaths under `/mcp` pass through the same authentication stack. Health routes do
not. There are no other application endpoints.

## Data boundaries

Operator input chooses `DOLIBARR_BASE_URL` only at startup. Neither the user nor a tool can change
the host. API keys are permitted only in the incoming `Authorization` header and the one outgoing
`DOLAPIKEY` header. They are never accepted as query, cookie, body, environment, or tool data.

The public identity is an allowlist: integer ID, login, optional first name, and optional last name.
Reporting projections allowlist time-line ID, UTC day, duration, user label, project label, task
label, optional product ID, and an explicitly requested bounded note. Sales projections separately
allowlist company identity and address fields, lead opportunity fields, project state, and internal
`PROJECTLEADER` relations. Leave projections allowlist the request ID, reference, employee,
approver, type, dates, half-day mode, status, description, and refusal reason. Additional Dolibarr
fields are ignored during validation and cannot reach tool results. Search and aggregate tools
omit notes.

Callers can supply inclusive dates, positive object identifiers, one fixed aggregation dimension,
and bounded paging values. They cannot supply upstream hosts, paths, headers, query languages, or
arbitrary `sqlfilters`. Global reporting reads at most 1000 accessible tasks and 50,000 time lines;
result limits fail explicitly instead of silently returning incomplete totals.

Sales search pages through fixed `/thirdparties`, `/projects`, and `/users` endpoints and performs
text matching locally. It never accepts or constructs a Dolibarr `sqlfilters` expression. Scans are
bounded at 10,000 records. A lead is accepted only when the project payload has
`usage_opportunity=1`; third-party `client=2` remains an independent prospect classification.

Leave searches page through fixed `/holidays` and
`/setup/dictionary/holiday_types` endpoints, then apply typed filters locally. They never accept
`sqlfilters` and stop at fixed processing bounds. Dates are normalized to UTC calendar dates;
half-day codes and statuses are mapped to closed literal sets.

## Confirmed mutation flow

Create and update tools have one stable wire shape and default to `apply=false`. A preview contains
the normalized proposed values, current allowlisted state where applicable, duplicate candidate
IDs, differences, warnings, and a SHA-256 confirmation token. No secret or record is cached.

The caller repeats the same operation with `apply=true` and the token. The service re-reads the
current API state and recomputes the token. A mismatch returns a safe conflict before any write.
The token is a workflow and stale-state guard, not a credential or replacement for Dolibarr
authorization. Callers already holding the API key could call Dolibarr directly.

Third-party and lead creates use fixed POSTs. Partial field edits and sales-stage changes use fixed
PUTs. Draft validation and reopening use `POST /projects/{id}/validate`. Owner replacement first
adds the desired internal `PROJECTLEADER`, then removes previous relations. Because Dolibarr offers
no transaction spanning those calls, the tool reports `partial` and refreshes the lead when a later
step fails. Writes are never automatically retried.

Leave creation and draft-only edits use fixed `POST /holidays` and `PUT /holidays/{id}` calls.
Submission maps to Dolibarr's `validate` action; approval, refusal, cancellation, and reopening
use their dedicated action endpoints. The service constrains source states before previewing a
transition, re-reads state for token confirmation, and refreshes state after the action. Dolibarr
remains authoritative for permission, overlap, configured approver, and balance rules.

## Failure boundaries

- Dolibarr 401/403 becomes HTTP 401 with `WWW-Authenticate: Bearer`.
- A syntactically valid, single `Retry-After` from Dolibarr 429 may be forwarded with HTTP 429.
- Timeouts, connection failures, and 5xx become HTTP 503.
- Malformed successful payloads and other unexpected statuses become HTTP 502.
- Reporting 403 and 404 responses become safe, typed tool errors without upstream bodies.
- Write validation failures and conflicts become safe typed errors without upstream bodies.
- Multi-call owner assignment can return an explicit partial result with the refreshed lead state.
- A leave action that does not persist its expected status returns a partial refreshed result.

No mapping returns upstream bodies, URLs, headers, exception chains, or credentials.
