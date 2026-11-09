# ADR 0011: Confirmed daily time registration

Status: Accepted

## Context

Users need to register a work date, task, hours, and comment. The official
[Dolibarr 23 Tasks API](https://github.com/Dolibarr/dolibarr/blob/23.0/htdocs/projet/class/api_tasks.class.php) <!-- codespell:ignore projet -->
exposes `POST /tasks/{id}/addtimespent` with a GMT date-time, duration in seconds,
user, note, and optional progress. It forces `timespent_withhour=1` and returns a success envelope,
without the created entry ID. It cannot create a native date-only entry.

## Decision

Expose `dolibarr_time_entry_create(work_date, task_id, hours, comment)` with the existing
preview/confirm flow. Record only the authenticated user's own time (`user_id=0` upstream).
No tool argument selects another employee, product, progress, endpoint, or payload property.
Hours use finite decimal arithmetic, must be positive and no greater than 24 per entry, and must
convert exactly to whole seconds. Comments are limited to 4000 characters. Dates start at
1970-01-01 to remain compatible with existing nonnegative time timestamps.

Represent the supplied day as `00:00:00` UTC. Explain in the tool, preview, and documentation that
the standard API stores a timed entry; midnight is a technical placeholder for the daily input.
A native date-only storage flag would require separate upstream support. Do not claim it here.

Read the task and its project using the request credential. Reject wrong resource IDs and closed
projects. Read the bounded task time collection (maximum 50,000 lines), reject cross-task,
cross-project, or repeated IDs, and select the current user's entries on that UTC day. Bind the
normalized proposal, verified user ID, task/project projections, and sorted selected entries to
the stateless confirmation token. Warn about matching existing entries without exposing others'
notes. Apply rereads these facts and rejects missing/stale tokens before writing.

Perform one fixed POST, explicitly leaving progress unchanged (`progress=-1`). Never retry.
Reread task time even after a POST error. Only one newly observed entry matching user, task,
project, day, seconds, and comment verifies success; return its ID and allowlisted daily facts.
Missing/ambiguous matches, POST errors, or reread errors produce `partial` and instructions to
inspect recorded time before retrying. The response target remains the task ID, with the entry
ID in `time_entry.entry_id` when verified.

## Consequences

- Credentials and identity retain the existing per-HTTP-request lifetime and shared pool rules.
- No live writes are needed for verification; HTTP/ASGI tests use mock upstreams.
- Sequential replay is rejected after a visible write changes the day's entries. Stateless
  confirmation cannot prevent simultaneous identical requests or stale upstream reads; it is
  neither an idempotency key nor an authorization mechanism. A concurrent matching creation
  cannot be conclusively attributed to this POST, because the API supplies no entry ID.
- Several entries per day are allowed, including deliberately confirmed duplicates. The 24-hour
  limit applies to one entry; Dolibarr remains authoritative for permissions and business policy.
- Logs and errors must never include comments, time lines, identities, or upstream payloads.
