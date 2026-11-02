# ADR 0009: Confirmed project task creation

Status: Accepted

## Context

Users need to add tasks to existing ordinary projects and project leads. The Dolibarr 23 Tasks
REST API exposes creation at `POST /tasks`, requiring `ref`, `label`, and `fk_project`.
`ref=auto` invokes the configured task numbering module. The similarly named
`POST /projects/{id}/tasks` implementation is commented out in upstream source and is not used.

Sources (upstream uses the French directory name `projet`): <!-- codespell:ignore projet -->

- [Tasks API](https://github.com/Dolibarr/dolibarr/blob/23.0/htdocs/projet/class/api_tasks.class.php) <!-- codespell:ignore projet -->
- [Projects API](https://github.com/Dolibarr/dolibarr/blob/23.0/htdocs/projet/class/api_projects.class.php) <!-- codespell:ignore projet -->
- [Task fields](https://github.com/Dolibarr/dolibarr/blob/23.0/htdocs/projet/class/task.class.php) <!-- codespell:ignore projet -->

## Decision

Add a typed `dolibarr_task_create` tool requiring project ID and task label. Accept only an
optional same-project parent, a 4000-character description, ordered calendar dates, and planned
workload in integer seconds bounded to the upstream signed integer storage range. Use UTC noon
for dates, consistent with project creates. Accept tasks in draft/open projects; reject closed
projects. Dolibarr remains authoritative for write permissions and configured task behavior.

Read project and optional parent facts before both preview and apply. Read a bounded list of
project task metadata without requesting time entries, and warn about equal normalized labels.
The token binds these facts, duplicate IDs, and proposed inputs. Require constant-time token
comparison before a single fixed POST, followed by a task detail reread. No automatic retry,
new credential lifetime, identity lookup, arbitrary endpoint, logging, or SQL access is added.
Authentication still passes through HTTP/ASGI on every MCP request.

## Consequences

- Duplicate detection is advisory, caller-visible, and cannot prevent concurrent creates.
- Tokens are stale-state workflow guards, not authentication or replay-proof idempotency keys.
- A successful POST followed by a failed reread returns `partial` with the known ID. A timeout
  or invalid POST response may leave an unknown write outcome; inspect the project before retrying.
- A reread differing from confirmed fields returns `partial`, without attempting compensating edits.
- Parent/project reads and the create are not transactional; upstream permissions remain decisive.
- More than 1000 task metadata records blocks creation rather than silently skipping duplicates.
- Task assignment, editing, deletion, and time-entry creation remain outside this tool.
- Tests use mock upstream transports through real HTTP auth and MCP; no live write is implied.
