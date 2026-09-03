# ADR 0006: Bounded upstream-response diagnostics

- Status: Accepted
- Date: 2026-09-03

## Context

Typed Dolibarr responses are deliberately collapsed to a generic public error when the upstream
status, JSON, or allowlisted schema is invalid. This prevents upstream content from crossing the MCP
trust boundary, but the previous log stream did not distinguish those failure classes. Operators
could not tell whether a request failed because of a status, malformed JSON, or one incompatible
field in a paged collection.

The existing request logging context already assigns a request ID. Diagnostics can use that
correlation without logging credentials, user identity, upstream content, or request-specific
resource identifiers.

## Decision

Keep the public MCP error unchanged. Immediately before raising
`InvalidDolibarrResponseError`, emit one `WARNING` event named
`upstream_response_invalid`.

The event may contain only:

- a fixed failure category: `client_status`, `unexpected_status`, `invalid_json`, or
  `schema_validation`;
- the allowlisted HTTP method and numeric status code;
- a fixed, code-owned schema name;
- for schema validation only, the total error count, a truncation flag, and at most 20 validation
  locations paired with Pydantic error types.

Validation locations and types are reduced to bounded tokens. A list position may appear because
it identifies which element of a bounded response page failed; it is not a Dolibarr object ID.

The event must never contain an upstream URL or path, query parameters, headers, request or response
bodies, rejected input values, Pydantic context, exception text, a traceback, credentials, Dolibarr
object IDs, or user identity. The standard logging formatter supplies the request ID separately.

## Consequences

Positive:

- operators can distinguish invalid statuses, malformed JSON, and schema drift;
- schema failures identify the incompatible field and error class without exposing its value;
- large invalid pages cannot create unbounded log records;
- clients continue to receive the same stable, non-sensitive error.

Costs:

- diagnostics intentionally omit the rejected value and upstream resource identifier;
- locating the exact record can require reproducing the request with narrower code-owned reads;
- a future response schema needs a fixed diagnostic name to avoid appearing as `unknown`.

## Rejected alternatives

- Logging raw Pydantic errors was rejected because they can include rejected input and context.
- Logging response bodies, URLs, query strings, or headers was rejected because those values can
  contain customer data, object identifiers, or credentials.
- Returning validation details in the MCP error was rejected because it would widen the public
  trust boundary.
- Logging every validation error was rejected because an upstream page can make the record
  unbounded.
