# ADR 0002: Request-scoped credentials for read-only reporting

- Status: Accepted
- Date: 2026-08-20

## Context

ADR 0001 limits a presented Dolibarr API key to one incoming MCP request and uses it first to
authenticate the request through `GET /api/index.php/users/info`. The original
`dolibarr_whoami` tool needs only the allowlisted identity placed in the MCP authentication
context, so the implementation deliberately replaces the credential there with a redacted marker.

Read-only time-reporting tools must call additional Dolibarr endpoints as the same user. A
server-wide key, tool argument, credential cache, or redacted authentication marker cannot provide
truthful per-user authorization. Requiring the client to present the same key separately as tool
data would expose it to schemas, model context, and histories.

## Decision

The authentication backend may place the successfully verified API key in a private context object
owned by the active ASGI request. The outer credential-context middleware creates that object before
authentication and clears it in a `finally` block after the response. Clearing the shared object
also invalidates references copied into child async contexts. The context is never stored on the
application, MCP server, shared HTTP client, SDK access token, logs, or tool results.

Reporting tools retrieve the credential only while handling their authenticated `tools/call`
request. `DolibarrClient` attaches it as `DOLAPIKEY` to each individual, fixed-path upstream GET.
Dolibarr therefore authorizes every identity lookup and every reporting read using the actual
requesting user. `dolibarr_whoami` remains unchanged and performs no second identity lookup.

The reporting surface is read-only and allowlisted. Tool arguments may select validated dates,
positive object identifiers, aggregation dimensions, and bounded output sizes. They cannot supply
an upstream host, path, query language, header, credential, or arbitrary Dolibarr filter. Upstream
responses are projected into typed reporting models; unrelated Dolibarr fields are discarded.

## Consequences

Positive:

- reports preserve Dolibarr's user permissions without a privileged service account;
- the key remains request-scoped, is never an MCP argument, and is actively cleared;
- fixed endpoints and typed projections bound the new data surface;
- aggregate tools omit notes, while only explicitly detailed tools return allowlisted notes;
- existing per-request revocation behavior remains intact.

Costs and constraints:

- a reporting `tools/call` performs the normal identity validation plus one or more authorized GETs;
- global reports may require bounded fan-out across accessible tasks because Dolibarr 23 has no
  global paginated time-entry endpoint;
- report completeness depends on the caller's Dolibarr permissions;
- the reporting endpoints require Dolibarr 23.0 or newer;
- no background task may retain a credential after the ASGI request completes.

## Rejected alternatives

- Storing the real key in the SDK `AccessToken` would make generic auth context consumers a secret
  boundary and contradict ADR 0001's redacted marker.
- A server or process environment key would collapse tenancy and user accountability.
- Passing a key as a tool argument would expose it to the model-facing protocol.
- Persisting or caching keys would extend credential lifetime and delay revocation.
- Accepting arbitrary REST paths or `sqlfilters` would turn the server into an unbounded proxy.
