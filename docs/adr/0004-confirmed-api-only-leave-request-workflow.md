# ADR 0004: Confirmed API-only leave-request workflow

- Status: Accepted
- Date: 2026-08-31

## Context

The server already reuses the authenticated caller's Dolibarr API key for fixed, allowlisted reads
and confirmed sales mutations. Leave requests add employee data, balance-sensitive decisions, and
several state transitions. An approval generated from stale context could record absence or consume
leave despite a changed request, while an adapter-side balance calculation could disagree with the
organization's current Dolibarr configuration.

Dolibarr's Holidays REST API exposes fixed request endpoints plus dedicated `validate`, `approve`,
`refuse`, `cancel`, and `reopen` actions. Its status codes map to draft, submitted, approved,
canceled, and refused. The upstream object also encodes four full/half-day combinations. Dolibarr
remains responsible for employee visibility, approver permissions, overlap, balance, and
negative-balance policy.

## Decision

Add ten typed MCP tools:

- active leave-type list;
- leave-request search and detail;
- create and draft-only update;
- submit, approve, refuse, cancel, and reopen.

All client calls use fixed official paths. List filtering is local and typed; no tool accepts an
upstream host, path, method, header, arbitrary payload, or `sqlfilters`. The client pages within
hard processing bounds of 1,000 leave types and 10,000 requests. Upstream payloads are projected to
explicit identifiers, dates, half-day mode, status, and bounded text.

The adapter exposes these state transitions:

- draft to submitted;
- submitted to approved or refused;
- submitted or approved to canceled;
- canceled to submitted.

Submission uses Dolibarr's `validate` action. Each other transition uses its same-named dedicated
action endpoint. Refusal requires a non-empty bounded reason. Editing is allowed only while a
request is draft. Delete is deliberately absent.

Every write defaults to `apply=false`. Its preview contains normalized changes, warnings, and a
SHA-256 token over the operation, target, proposed values, and current allowlisted state. The server
stores neither preview nor token. A repeated `apply=true` call re-reads current state, recomputes
the token, and compares it in constant time before writing.

Approval previews always warn that Dolibarr remains authoritative for leave balance and policy.
The adapter does not calculate, reserve, override, or promise available leave. It also does not
automatically retry writes. After a transition, it re-reads the request; an unexpected status
produces an explicit partial result with refreshed state.

Credentials remain request-scoped. The shared HTTP pool has no credential header, and `DOLAPIKEY`
is attached only to the individual upstream call. Logging continues to exclude headers, bodies,
query strings, identities, upstream payloads, and transport exception details.

## Consequences

Positive:

- leave actions preserve the real Dolibarr user's permissions and audit identity;
- every mutation has an explicit, stale-state-bound second step;
- approval cannot silently rely on an adapter-side balance approximation;
- fixed routes, payload allowlists, and closed status sets keep the MCP surface narrow;
- an unpersisted transition is visible instead of reported as success;
- no database, browser automation, credential cache, or server-side workflow state is added.

Costs and constraints:

- callers make a preview call before each write and repeat the operation to confirm;
- each confirmed update or transition performs current-state reads around the mutation;
- list completeness ends at documented processing bounds;
- access to other employees' requests depends entirely on Dolibarr permissions;
- a timeout after Dolibarr accepted an action is ambiguous and requires a fresh read;
- balance details are not reproduced in MCP output.

## Rejected alternatives

- Direct database access would bypass Dolibarr permissions, triggers, and compatibility.
- Browser automation would be brittle, session-bearing, and outside the API-only trust boundary.
- Arbitrary REST proxying or `sqlfilters` would expose a much broader attack surface.
- A privileged server key would erase per-user accountability and permission boundaries.
- Computing leave balance in the adapter would duplicate mutable Dolibarr business rules.
- Approval without a state-bound token would allow accidental or stale generated actions.
- Server-side preview storage would introduce sensitive session state and cleanup requirements.
- Automatic write retry could repeat a transition after an ambiguous timeout.
- Exposing delete would make recovery harder without adding a necessary lifecycle capability.
