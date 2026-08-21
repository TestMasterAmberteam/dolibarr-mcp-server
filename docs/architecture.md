# Architecture

## Components

The service has four explicit layers:

1. Starlette/Uvicorn owns HTTP, health routes, correlation IDs, and startup/shutdown.
2. Transport security and Bearer middleware validate `Host`, `Origin`, header syntax, and the
   presented key before MCP parses a message.
3. One `MCPServer` instance dispatches six allowlisted read-only tools.
4. `DolibarrClient` owns one `httpx2.AsyncClient` pool, the fixed `/users/info` operation, and
   fixed project, task, user-label, and time-entry GET operations.

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
9. Reporting tools attach the same key only to individual, fixed-path GETs. Dolibarr authorizes
   every read with the caller's actual permissions.
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
label, optional product ID, and an explicitly requested bounded note. Additional Dolibarr fields
are ignored during validation and cannot reach tool results. Aggregate tools omit notes.

Callers can supply inclusive dates, positive object identifiers, one fixed aggregation dimension,
and bounded paging values. They cannot supply upstream hosts, paths, headers, query languages, or
arbitrary `sqlfilters`. Global reporting reads at most 1000 accessible tasks and 50,000 time lines;
result limits fail explicitly instead of silently returning incomplete totals.

## Failure boundaries

- Dolibarr 401/403 becomes HTTP 401 with `WWW-Authenticate: Bearer`.
- A syntactically valid, single `Retry-After` from Dolibarr 429 may be forwarded with HTTP 429.
- Timeouts, connection failures, and 5xx become HTTP 503.
- Malformed successful payloads and other unexpected statuses become HTTP 502.
- Reporting 403 and 404 responses become safe, typed tool errors without upstream bodies.

No mapping returns upstream bodies, URLs, headers, exception chains, or credentials.
