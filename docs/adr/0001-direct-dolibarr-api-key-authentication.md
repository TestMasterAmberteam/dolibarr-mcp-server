# ADR 0001: Direct Dolibarr API-key authentication

- Status: Accepted
- Date: 2026-08-19

## Context

The MVP must act for different Dolibarr users without storing credentials or maintaining a parallel
account system. Dolibarr users can issue their own API keys, and its `GET /api/index.php/users/info`
endpoint validates a key while returning the current user's profile.

MCP's standard authorization model describes OAuth resource servers. This deployment has no real
authorization server, discovery document, client registration, authorization code, or token
exchange. Publishing invented issuer metadata would mislead clients and operators.

## Decision

Each compatible MCP client sends its Dolibarr key on every `/mcp` HTTP request as
`Authorization: Bearer <key>`. A strict ASGI authentication backend translates that one request to
Dolibarr's `DOLAPIKEY` header on the fixed `/users/info` URL.

Successful validation creates only a safe request-scoped identity. The SDK auth context stores a
redacted marker, fixed scope, Dolibarr user ID as subject, and the allowlisted identity. It does not
store the presented key. The tool reuses that context and does not repeat validation inside the
same HTTP request.

We use public SDK authentication context and scope middleware, but not `AuthSettings`, because the
latter necessarily advertises an OAuth issuer and protected-resource metadata that do not exist.
Strict parsing and non-401 upstream mappings remain application-owned.

## Consequences

Positive:

- no local credentials, sessions, database, Redis, or administrator key;
- immediate effect of Dolibarr key revocation because every request is revalidated;
- authorization stays aligned with the actual Dolibarr user;
- failures can distinguish invalid credentials from upstream availability.

Costs and constraints:

- each MCP HTTP request adds one Dolibarr identity lookup;
- clients must support a custom Authorization header and already possess a key;
- normal OAuth-capable clients cannot discover or obtain the credential automatically;
- production depends on TLS, reverse-proxy rate limiting, strict host policy, and safe logs;
- multiple Dolibarr installations require separate server deployments.

## Rejected alternatives

- A server-wide Dolibarr administrator key would collapse user accountability and privilege.
- API keys as tool arguments would expose secrets to models, histories, and schemas.
- Caching keys or identities would delay revocation and introduce secret state.
- Fake OAuth metadata would violate the actual trust model and cause broken discovery.

## Future direction

A later architecture may place a genuine OAuth gateway in front of Dolibarr, with a real issuer,
audience validation, scopes, discovery, and token lifecycle. That is a security-model change and
requires a new ADR plus migration guidance; it must not be simulated inside this MVP.
