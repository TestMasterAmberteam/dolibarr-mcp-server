# Projects and agenda history

These six tools are additive in 0.7.0. Existing `lead_*` tools still cover only projects with
`usage_opportunity=1`. A project note is a field; a history entry is a separate agenda event.

## Arguments and results

| Tool | Arguments |
| --- | --- |
| `dolibarr_project_search` | Optional `query` (ref/title substring), `thirdparty_id`, `project_state` (`draft`, `open`, `closed`), `usage_opportunity`, `offset=0`, `limit=200` |
| `dolibarr_project_get` | Required `project_id` |
| `dolibarr_agenda_event_search` | Optional `project_id`, `thirdparty_id`, `date_from`, `date_to`, `timezone="UTC"`, `owner_user_id`, `type_code`, `status`, `offset=0`, `limit=200` |
| `dolibarr_agenda_event_get` | Required `event_id`; optional `timezone="UTC"` |
| `dolibarr_agenda_event_create` | Required `title`, `event_type`, `project_id`, `start`; optional `end`, `timezone="UTC"`, `all_day`, `status="not_started"`, `description`, `thirdparty_id`, `apply=false`, `confirmation_token` |
| `dolibarr_agenda_event_delete` | Required `event_id`; optional `timezone="UTC"`, `apply=false`, `confirmation_token` |

IDs are positive; offset is 0..1,000,000 and limit is 1..500. Search returns `total_count`,
`returned_count`, `offset`, `limit`, `has_more`, and `rows`. Projects and events are ordered by
ascending ID. Read another page using `offset + returned_count` while `has_more` is true.
Counts refer only to readable matches. There is no default project lifecycle or opportunity filter.

Project rows contain ID, reference, title, third-party ID, state, opportunity flag and start/end
dates. Project detail adds bounded description/public/private notes. Event rows contain ID, title,
type code/label, event code, automatic flag, local start/end date-times, interpretation timezone,
all-day flag, percentage, owner and project/third-party IDs. Event detail adds bounded description,
public note and location. Raw objects, email recipients, assignments, headers and extrafields are
never returned. Missing dates/owners/links are explicit null values, including automatic history.

Lists are bounded to 10,000 upstream projects or agenda events; the limit produces an explicit
tool error, not a silent partial page. Only positive project/third-party criteria are compiled
internally for agenda lists, so project-specific history can work with a larger global agenda.
Other filtering is local. Per-record and linked-object GET checks reauthorize every matching
candidate. Permission failures and disappeared search candidates are omitted; direct get fails.
Concurrent changes can move rows between pages; this API does not provide snapshot pagination.

## Manual event types and completion

| `event_type` for creation | Upstream code |
| --- | --- |
| `email` | `AC_EMAIL` |
| `phone_call` | `AC_TEL` |
| `meeting` | `AC_RDV` |
| `other` | `AC_OTH` |

Creation supports these manual types only; the installation must contain their dictionary codes.
Search accepts a type code, including `AC_OTH_AUTO`, and does not exclude automatic history.
Completion statuses are `not_started` (0), `in_progress` (50 on create, 1..99 when searching),
`done` (100), and `not_applicable` (-1). Translate a user request for "wykonane" to `status="done"`.

The owner always comes from the authenticated HTTP request. The third party is resolved from the
project. If supplied explicitly it must equal that project's third party. An unlinked project
creates an event without a third party. All linked objects must be readable by the caller.
Closed projects can have history; this operation does not reopen or edit the project.

Creating an E-mail event records that a communication occurred. The MCP tool sends no message or
invitation and accepts no recipient or send flag. Dolibarr's custom server-side triggers remain
administrator-controlled and can still run when a history object is created/deleted.

## Local dates and DST

`start`/`end` accept YYYY-MM-DD, DD.MM.YYYY, or ISO date-times with whole-second precision. Supported
creation years are 1970..2099. `timezone` is an IANA name, for example `Europe/Warsaw`.

Date-only start implies `all_day=true`, with inclusive local dates. Omit end for one day; for a
range use date-only end as well. Stored boundaries match Dolibarr's local form: 00:00:00 on the first
day through 23:59:59 on the last day, with `fulldayevent=1`. A Warsaw day at the spring transition
has 23 hours; an autumn transition day has 25 hours. Never add a fixed 86,400 seconds to form end.

Timed events use `all_day=false` (inferred from a date-time start). Omitted end means the same
instant. Nonexistent wall times are rejected. Ambiguous times require an offset, e.g.
`2026-10-25T02:30:00+02:00` or `2026-10-25T02:30:00+01:00` in `Europe/Warsaw`.
An explicit offset must match the named zone. Search date bounds are inclusive local days and
select events whose intervals overlap the requested range.

Dolibarr stores Unix instants, not the IANA zone. Supply the same display timezone to search/get.
For installations intentionally using `MAIN_STORE_FULL_EVENT_IN_GMT`, use timezone UTC for all-day
storage; MCP cannot discover this global policy through the six fixed endpoints.

## Example MCP calls

Examples are tool names plus JSON arguments, not direct Dolibarr API requests. IDs are illustrative:
replace project 123 with the ID returned by search, and event 456 with the verified created ID.

Find the ordinary project or opportunity:

```text
dolibarr_project_search
{"query":"L2026_0029","limit":20}

dolibarr_project_get
{"project_id":123}
```

Read its history, including automatic entries:

```text
dolibarr_agenda_event_search
{"project_id":123,"timezone":"Europe/Warsaw","offset":0,"limit":100}
```

Preview the requested completed E-mail record:

```text
dolibarr_agenda_event_create
{"title":"Mail przypominający został wysłany","event_type":"email","project_id":123,
 "start":"2026-11-17","timezone":"Europe/Warsaw","status":"done","apply":false}
```

Review changes, warnings and `matching_event_ids`. Repeat with identical arguments and the token
from that preview, replacing `TOKEN_FROM_CREATE_PREVIEW` with its 64-character value:

```text
dolibarr_agenda_event_create
{"title":"Mail przypominający został wysłany","event_type":"email","project_id":123,
 "start":"2026-11-17","timezone":"Europe/Warsaw","status":"done","apply":true,
 "confirmation_token":"TOKEN_FROM_CREATE_PREVIEW"}
```

Confirm the response has `outcome="applied"` and inspect the verified `agenda_event.event_id`.
If the result is partial, read/search first; do not automatically repeat the create call.

To delete exactly one event, preview first:

```text
dolibarr_agenda_event_get
{"event_id":456,"timezone":"Europe/Warsaw"}

dolibarr_agenda_event_delete
{"event_id":456,"timezone":"Europe/Warsaw","apply":false}

dolibarr_agenda_event_delete
{"event_id":456,"timezone":"Europe/Warsaw","apply":true,
 "confirmation_token":"TOKEN_FROM_DELETE_PREVIEW"}
```

Delete previews show title, dates, owner and project. A changed event snapshot invalidates the
token, including changes beyond the displayed note excerpt. Successful deletion is checked by
GET returning 404; access denial is not absence. There is no bulk deletion or atomic upstream
compare-and-delete. Tokens bind state and arguments, not a clock-based expiry or exclusive lock.

## Failure and release boundaries

Permission/not-found/validation failures use fixed safe messages. Timeout, server error, invalid
create response or failed verification yields a partial result and never an automatic retry.
Known IDs and newly observed matching event IDs help reconcile uncertainty; a single matching
event after a failed POST is still not proof of exactly-once delivery. Server-side normalization
that changes confirmed values is reported as a mismatch rather than silently accepted.

API semantics were checked against official 23.0.3 and 24.0.2 source. Local tests use mock upstreams
behind full authenticated ASGI/MCP transport. They do not validate a deployed MCP build, custom
Dolibarr modules/triggers, or a production write. See [ADR 0012](adr/0012-project-history-and-confirmed-agenda-events.md).
