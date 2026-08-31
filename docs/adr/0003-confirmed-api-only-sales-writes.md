# ADR 0003: Confirmed API-only sales writes

- Status: Accepted
- Date: 2026-08-21

## Context

ADR 0001 authenticates every MCP HTTP request with the caller's own Dolibarr API key. ADR 0002
allows read-only reporting tools to reuse that key only inside the active request. Sales support
now needs to create and update third parties and project-based leads without introducing a service
account, database access, arbitrary REST proxy, credential cache, or misleading definition of a
lead.

In Dolibarr 23.0.3, the Lead checkbox on a project is represented by `usage_opportunity=1`.
Opportunity stage (`fk_opp_status`) and project lifecycle state are separate. The third party's
customer classification can independently be `prospect`; it does not define whether a project is a
lead.

MCP write calls can be generated incorrectly or against a record that changed after it was read.
Dolibarr also provides no transaction spanning addition and removal of project contact relations.
Automatic retry after a timeout can duplicate creates or repeat a partially completed mutation.

## Decision

Add fixed, typed REST client methods for the official Dolibarr third-party, project, user, and
project-contact endpoints. The client continues to use one shared credential-free HTTP pool and
attaches the active request's `DOLAPIKEY` only to an individual call. No method accepts an upstream
host, arbitrary path, HTTP method, header, query language, `sqlfilters`, or untyped payload.

Sales input and output fields use explicit allowlists. Searches page through official list
endpoints and filter locally within a fixed 10,000-record processing bound. Upstream fields outside
the typed projection are discarded. Searches omit notes; detail results bound notes to 4000
characters.

Every write-capable tool defaults to `apply=false`. It returns a preview and a SHA-256 token over:

- the operation and target ID;
- normalized proposed values;
- the current allowlisted record state when a target exists;
- sorted duplicate candidate IDs for creates.

The server stores neither preview nor token. An `apply=true` call must repeat the same inputs and
token. The service re-reads current state and uses constant-time comparison. Missing or changed
state rejects the write before mutation. The token is a workflow and optimistic-concurrency guard,
not authentication or proof of human identity; Dolibarr permissions remain authoritative.

New lead projects require an existing third party and explicit opportunity stage. They are created
with `ref=auto`, `usage_opportunity=1`, and draft state. Opening a draft or reopening a closed lead
uses the dedicated `POST /projects/{id}/validate` API. Project closing and returning to draft are
not exposed because Dolibarr 23.0.3 has no equivalent dedicated REST action.

Lead assignment represents one owner by internal `PROJECTLEADER` relations. The service adds the
desired user before removing previous leaders. It does not affect project tasks. If a later call
fails, the tool returns `partial`, safe generic errors, and a refreshed lead rather than pretending
the operation was atomic.

Writes are never automatically retried. Logging continues to exclude headers, bodies, query
strings, upstream payloads, identities, and transport exception details.

## Consequences

Positive:

- caller permissions and audit identity remain those of the real Dolibarr user;
- lead semantics match the project checkbox rather than third-party prospect classification;
- accidental and stale writes require an explicit second call;
- the server remains stateless and API-only;
- partial multi-call assignment is visible and followed by an authoritative refresh.

Costs and constraints:

- a confirmed mutation performs authentication plus preview-state reads again before writing;
- search completeness stops at the documented local processing bound;
- create idempotency is best-effort duplicate detection, not a database uniqueness guarantee;
- a timeout after Dolibarr accepted a write is ambiguous and requires a read before retry;
- the complete lead-stage dictionary is unavailable through the official API in this version;
- adding and removing project leaders cannot be made transactional by this adapter.

## Rejected alternatives

- Direct database queries would bypass Dolibarr permissions, triggers, compatibility, and the
  user's API-only boundary.
- Arbitrary REST paths, methods, payloads, or `sqlfilters` would turn the server into an unbounded
  proxy.
- A privileged server key would collapse user accountability and tenancy.
- Server-side preview storage would add session state and a sensitive lifecycle to manage.
- A caller-provided boolean without a state-bound token would not detect stale records.
- Automatic write retry would risk duplicates and repeated partial operations.
- Treating third-party `prospect` as a lead would contradict the configured Dolibarr workflow.
