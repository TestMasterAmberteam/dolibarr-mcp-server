# ADR 0010: Confirmed addition of users to project tasks

Status: Accepted

## Context

Users need to add people to tasks without replacing other assignees. Dolibarr 23 exposes task
contacts through `GET /tasks/{id}/contacts` and `POST /tasks/{id}/contacts`. Its standard internal
task roles are `TASKCONTRIBUTOR` and `TASKEXECUTIVE`. Contact `id` refers to a user or third-party
contact, while `rowid` is the relationship ID. Internal and external IDs can collide. Active link
status is `4`, distinct from the user's active status `1`.

Sources:

- [Tasks API](https://github.com/Dolibarr/dolibarr/blob/23.0/htdocs/projet/class/api_tasks.class.php) <!-- codespell:ignore projet -->
- [Contact roles](https://github.com/Dolibarr/dolibarr/blob/23.0/htdocs/install/mysql/data/llx_c_type_contact.sql)
- [Contact link semantics](https://github.com/Dolibarr/dolibarr/blob/23.0/htdocs/core/class/commonobject.class.php)

## Decision

Add `dolibarr_task_assign` with required project, task, and user IDs and a closed role enum.
Default to contributor and allow executive explicitly. Read task/project facts, verify ownership
and draft/open project state, and verify the target user is active. Read at most 1000 task-contact
projections containing only contact ID, relationship ID, source, code, and link status. Bind these
facts and proposed inputs into a stateless SHA-256 preview token; sort contacts by relationship ID.
Apply must repeat inputs and token and use the same state checks before constant-time comparison.

An existing active internal link for that user and role returns `no_op`. Otherwise send one fixed
POST with `fk_socpeople=user_id`, the selected role, `source=internal`, and `notrigger=0`.
Preserve all other assignments, roles, and project contacts. Never delete or automatically retry.
Reread contacts after the attempt. Return a safe requested-user summary, role, and verification
state (`true`, `false`, or unknown). A POST failure, reread failure, or absent active link is
`partial`, even if an ambiguous POST was followed by a successful confirming read.

## Consequences

- The feature adds one user role per call; multiple people can be added independently.
- Only user accounts are supported, not third-party contacts or arbitrary contact types.
- Existing inactive links do not count as assigned. Upstream may reject their reactivation;
  the adapter reports the failure and reread result rather than changing link state directly.
- Reads and writes are not transactional. Concurrent changes can cause conflicts; permissions
  and configured role availability remain Dolibarr's authority. Tokens are not idempotency keys.
- New writes reuse the shared pool and per-request credential without new identity caches,
  logging, database access, or server secrets. HTTP/ASGI tests verify both request credentials
  and the public schema. Automated validation performs no live Dolibarr writes.
