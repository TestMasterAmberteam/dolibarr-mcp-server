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
- After successful verification, retain the key only in the active ASGI request's private holder.
- Clear that mutable holder in `finally`, invalidating references copied to child async contexts.
- Attach the request key only to fixed-path reporting, sales, and leave API calls made during the
  same `tools/call`.
- Never log headers, bodies, query strings, identities, upstream payloads, or exceptions containing
  transport details.
- Centrally redact credential-like mapping keys and recognizable Bearer/DOLAPIKEY strings.

## Transport requirements

Production traffic must use HTTPS. The application deliberately allows insecure HTTP only for
literal loopback hosts when `allow_insecure_localhost=true` in `config.toml`.

Set exact `mcp_allowed_hosts` and, only for browser clients, exact `mcp_allowed_origins`. Production
values may not use wildcards. The application does not enable CORS and does not trust
`X-Forwarded-*`. Configure the reverse proxy to:

- terminate TLS with a maintained certificate and modern policy;
- replace, not append, the `Host` sent upstream and match it in `mcp_allowed_hosts`;
- remove untrusted forwarding headers unless another trusted component needs them;
- preserve `Authorization` only on the protected upstream location and never log it;
- limit request body size and timeouts;
- rate-limit by trusted network identity and, where safe, another non-secret classifier;
- expose health probes only where operationally necessary.

An in-process limiter is intentionally absent because it would be inconsistent across replicas.

## Upstream TLS and networking

`httpx2` uses TLS verification, no redirects, no environment proxy discovery, explicit timeouts,
and bounded pools. Mount a private CA read-only and set `dolibarr_ca_bundle` in `config.toml` if
required. Do not disable TLS verification. Egress policy should restrict the service to the
configured Dolibarr host and required infrastructure.

## Reporting data boundary

Reporting requires Dolibarr 23.0 or newer. Every read uses the requesting user's key, so Dolibarr
remains responsible for project, task, user, and time-entry authorization. The server has no
administrator credential and cannot broaden those permissions.

Only fixed project, task, user-label, and time-entry GETs are implemented. Tool inputs cannot select
an upstream URL or arbitrary Dolibarr filter expression. Upstream payloads are projected to typed
allowlists. Aggregate tools omit notes; detailed tools return notes only when explicitly requested
and truncate them to 4000 characters. Output rows, accessible tasks, and processed time lines have
hard bounds to limit memory use and model-context amplification.

## Sales data and write boundary

Sales support requires Dolibarr 23.0.3-compatible third-party, project, user, and project-contact
REST endpoints. The MCP server never connects to the Dolibarr database and never accepts or builds
`sqlfilters`. It does not install a Dolibarr module or call a custom endpoint.

Upstream sales objects are projected into separate typed allowlists. Searches expose company
labels and selected lead metadata but omit notes. Detail tools may return public and private notes
only for records the caller can read, truncated to 4000 characters. Tool inputs cannot select a
host, path, method, header, arbitrary payload property, extrafield, bank field, or personal contact.

Every write defaults to a non-mutating preview. Applying it requires the preview's stateless token
and the exact same normalized request while the allowlisted current state is unchanged. This
reduces accidental and stale writes but is not an authorization mechanism: Dolibarr still decides
whether the presented user's key may perform each API call. Preview tokens contain no credential
and are safe to invalidate by changing the request or upstream record.

No write is automatically retried. A timeout after an upstream write can have an ambiguous
outcome, so callers must re-read before trying again. Replacing project leaders uses several API
calls and may return `partial`; the refreshed result is authoritative for the state observed after
the failure. Returning a project to draft remains excluded. Project closing uses a separate fixed
`status=2` update because Dolibarr 23.0.3 lacks a dedicated close REST action. The request cannot
select another status or payload, and preview/result warnings disclose that close triggers and
closing-user/date metadata are not guaranteed. The refreshed project state is authoritative.

Lead-stage discovery scans only a minimal typed project projection within the existing 10,000-row
bound. It merges distinct ID/code pairs observed on lead projects the caller may read with an
optional startup-only `[dolibarr_lead_stage_catalog.<CODE>]` table in `config.toml`. Canonical codes,
aliases, IDs, labels, percentages, positions, activity flags, cardinality, and case-insensitive
uniqueness are validated; tools cannot modify the mapping. The result labels configured rows and
remains incomplete. It never queries the database, scrapes the UI, or claims to expose the live
Dolibarr dictionary. Stage-code writes resolve a configured canonical code or alias first, reject
configured inactive stages, or require one unambiguous observed ID before the ordinary
preview-token flow. The authoritative reread must expose the resolved ID, otherwise the result is
`partial` rather than `applied`.

## Leave-request data and write boundary

Leave support uses only Dolibarr's official Holidays and setup-dictionary REST endpoints. It does
not query the database, scrape the UI, accept `sqlfilters`, or expose an arbitrary endpoint,
method, header, or payload. The caller's own key remains the sole upstream credential, so Dolibarr
decides whether that caller may read an employee's request or execute a lifecycle action.

Search results expose typed identifiers, dates, half-day mode, and status. Detail results add only
bounded description and refusal reason. Leave-type lookup exposes the active code, label, and
configured balance flags. Processing is bounded to 10,000 accessible requests and 1,000 types.

Create, draft update, submit, approve, refuse, cancel, and reopen all use the same non-mutating
preview and current-state token rule as sales writes. The adapter additionally restricts legal
source statuses and uses only Dolibarr's dedicated action endpoints. It exposes no delete tool.
Approval previews always warn that Dolibarr remains authoritative for available balance, overlap,
permissions, configured approver, and negative-balance policy. The adapter never changes those
rules or claims that a request is affordable. Writes are not retried automatically; an unexpected
post-action status is returned as a partial result with refreshed state.

## Secret rotation and incident response

Users rotate or revoke their keys in Dolibarr. A suspected leak should be handled there immediately;
the MCP server has no stored credential to invalidate. Then review reverse-proxy, platform, and
Dolibarr access logs for the affected interval without copying tokens into an issue.

See the public reporting policy in the repository root [SECURITY.md](../SECURITY.md).
