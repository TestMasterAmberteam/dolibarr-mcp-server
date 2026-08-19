# Architecture

## Components

The service has four explicit layers:

1. Starlette/Uvicorn owns HTTP, health routes, correlation IDs, and startup/shutdown.
2. Transport security and Bearer middleware validate `Host`, `Origin`, header syntax, and the
   presented key before MCP parses a message.
3. One `MCPServer` instance dispatches exactly one tool.
4. `DolibarrClient` owns one `httpx2.AsyncClient` pool and the fixed `/users/info` operation.

The parent lifespan opens the shared HTTP client and MCP session manager together. Shutdown closes
both. The MCP transport uses `stateless_http=True` and JSON responses. Current (2026-era) requests
are inherently self-contained; stateless mode also avoids retaining legacy transport sessions.

## Request flow

1. The outer request middleware generates an opaque request ID.
2. Host/Origin validation rejects DNS-rebinding candidates before an upstream request.
3. The auth backend extracts exactly one Bearer token from raw ASGI headers.
4. The backend calls the fixed Dolibarr identity URL with a per-call `DOLAPIKEY` header.
5. The validated upstream model is projected to `VerifiedIdentity`.
6. An MCP `AccessToken` contains a redacted marker plus safe identity claims, never the key.
7. SDK `AuthContextMiddleware` scopes that object to the active request.
8. `dolibarr_whoami` reads the existing context and performs no second Dolibarr call.
9. Context and local key references are released when the request ends.

All methods and subpaths under `/mcp` pass through the same authentication stack. Health routes do
not. There are no other application endpoints.

## Data boundaries

Operator input chooses `DOLIBARR_BASE_URL` only at startup. Neither the user nor a tool can change
the host. API keys are permitted only in the incoming `Authorization` header and the one outgoing
`DOLAPIKEY` header. They are never accepted as query, cookie, body, environment, or tool data.

The public identity is an allowlist: integer ID, login, optional first name, and optional last name.
Additional Dolibarr fields are ignored during validation and cannot reach the tool result.

## Failure boundaries

- Dolibarr 401/403 becomes HTTP 401 with `WWW-Authenticate: Bearer`.
- A syntactically valid, single `Retry-After` from Dolibarr 429 may be forwarded with HTTP 429.
- Timeouts, connection failures, and 5xx become HTTP 503.
- Malformed successful payloads and other unexpected statuses become HTTP 502.

No mapping returns upstream bodies, URLs, headers, exception chains, or credentials.
