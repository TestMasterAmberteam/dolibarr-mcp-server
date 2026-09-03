# ADR 0003: Confirmed API-only sales writes

- Status: Accepted
- Date: 2026-08-21
- Amended: 2026-09-03

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
uses the dedicated `POST /projects/{id}/validate` API.

Dolibarr 23.0.3 has no official endpoint for the configured opportunity-stage dictionary. A
read-only stage tool therefore scans a minimal, fixed `/projects` projection and merges distinct
ID/code pairs observed on accessible projects with an optional operator-owned
`[dolibarr_lead_stage_catalog.<CODE>]` table in `config.toml`. Each record contains the canonical
Dolibarr code plus a positive ID, bounded label and aliases, probability percentage, display
position, and activity flag. The catalog is bounded and validated at startup for safe identifiers,
positive unique IDs, and global case-insensitive code/alias uniqueness. Its wire result always sets
`complete=false`, marks configured rows, exposes their static metadata, and warns about source
limitations. It does not query the database or scrape the GUI.

The status-change tool accepts exactly one numeric `stage_id` or bounded `stage_code`. Code lookup
prefers a canonical code or alias from the immutable operator catalog, otherwise it requires exactly
one matching ID observed on accessible leads. A configured inactive stage is rejected whether
selected by identifier or numeric ID. Missing or ambiguous codes fail before preview. The resolved
numeric ID and canonical code are bound into the preview token; only `fk_opp_status` is written.
The post-write reread must expose the resolved ID, otherwise the result is `partial`.

Dolibarr 23.0.3 also has no dedicated project-close REST action, although the general project PUT
accepts lifecycle `status=2`. A separate preview-confirmed close tool is restricted to open leads
and sends the fixed payload `{"status": 2}` to `PUT /projects/{id}`. It then re-reads the lead and
returns `partial` unless the state is closed. Every preview and result warns that this generic path
does not guarantee the `PROJECT_CLOSE` trigger, closing user/date metadata, or GUI-equivalent
semantics. Returning a project to draft remains unexposed.

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
- the complete lead-stage dictionary remains unavailable; unobserved custom codes require an
  operator to verify and configure their numeric IDs;
- the generic close transition reaches state `closed` but cannot promise close triggers or audit
  metadata that Dolibarr's unexposed `Project::setClose()` would create;
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
- Scraping the dictionary UI or querying `c_lead_status` directly would bypass the API-only trust
  boundary and caller authorization.
- Treating `P3L` as a Dolibarr code would conflate the business prefix in `P3L - Lost` with the
  canonical `LOST` code. The operator catalog represents it explicitly as an alias.
- Guessing or shipping a universal `LOST` ID would be incorrect because lead-stage IDs belong to
  the specific Dolibarr installation; the mapping is explicit operator configuration instead.
- Naming the generic project PUT trigger-equivalent would overstate Dolibarr 23.0.3 behavior; the
  tool instead discloses the semantic gap before confirmation and in the final result.
