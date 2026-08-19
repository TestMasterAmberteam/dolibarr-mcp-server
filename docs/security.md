# Security design and operations

## Trust model

This server is a narrow resource adapter. Dolibarr remains the identity authority. A user's API key
grants exactly the permissions assigned to that Dolibarr user. The server neither elevates those
permissions nor has a global administrator key.

The Bearer syntax is reused as an HTTP credential carrier. This is direct API-key authentication,
not OAuth. Clients must already possess the key and be able to set a custom Authorization header.

## Credential handling

- Accept one `Authorization` header with a case-insensitive `Bearer` scheme and bounded token68.
- Do not trim, normalize, copy into configuration, cache, or persist the key.
- Attach `DOLAPIKEY` only to the individual upstream request.
- Keep default shared-client headers credential-free.
- Use a redacted marker in the SDK access-token object.
- Never log headers, bodies, query strings, identities, upstream payloads, or exceptions containing
  transport details.
- Centrally redact credential-like mapping keys and recognizable Bearer/DOLAPIKEY strings.

## Transport requirements

Production traffic must use HTTPS. The application deliberately allows insecure HTTP only for
literal loopback hosts when `ALLOW_INSECURE_LOCALHOST=true`.

Set exact `MCP_ALLOWED_HOSTS` and, only for browser clients, exact `MCP_ALLOWED_ORIGINS`. Production
values may not use wildcards. The application does not enable CORS and does not trust
`X-Forwarded-*`. Configure the reverse proxy to:

- terminate TLS with a maintained certificate and modern policy;
- replace, not append, the `Host` sent upstream and match it in `MCP_ALLOWED_HOSTS`;
- remove untrusted forwarding headers unless another trusted component needs them;
- preserve `Authorization` only on the protected upstream location and never log it;
- limit request body size and timeouts;
- rate-limit by trusted network identity and, where safe, another non-secret classifier;
- expose health probes only where operationally necessary.

An in-process limiter is intentionally absent because it would be inconsistent across replicas.

## Upstream TLS and networking

`httpx2` uses TLS verification, no redirects, no environment proxy discovery, explicit timeouts,
and bounded pools. Mount a private CA read-only and set `DOLIBARR_CA_BUNDLE` if required. Do not
disable TLS verification. Egress policy should restrict the service to the configured Dolibarr host
and required infrastructure.

## Secret rotation and incident response

Users rotate or revoke their keys in Dolibarr. A suspected leak should be handled there immediately;
the MCP server has no stored credential to invalidate. Then review reverse-proxy, platform, and
Dolibarr access logs for the affected interval without copying tokens into an issue.

See the public reporting policy in the repository root [SECURITY.md](../SECURITY.md).
