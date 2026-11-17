# ADR 0012: Project reads and confirmed agenda history

Status: Accepted

## Context

Lead tools intentionally cover only opportunity projects. Users also need ordinary projects and
their agenda history. The official Dolibarr 23.0.3 and 24.0.2 APIs expose projects and agenda events,
but older agenda list implementations do not enforce all checks performed by the event GET.
An E-mail history entry is a record, not an instruction to send mail.

## Decision

Keep lead semantics unchanged. Add separately typed project search/detail and agenda search/detail,
create, and single-record delete tools. Use the existing shared HTTP pool and caller credential.
Only fixed official project and agenda endpoints are used. No SQL, arbitrary properties, email
recipient, invitation, HTTP path, or owner override is an MCP argument.

Page lists in ascending identifier order, within 10,000 records. For agenda reads, compile only
optional positive project/third-party IDs into fixed numeric universal-search criteria; do not
forward caller strings or `user_ids`. Other event filters run locally. This lets project history
remain usable when the global agenda exceeds the bound without bypassing owner checks.
Reauthorize each candidate through
its individual GET and check readable project/third-party links before returning agenda data.
Skip forbidden/disappeared search candidates without exposing their data or counts. Search fails
explicitly when the processing bound is reached; it never silently returns a complete-looking
partial history. Credentials, identities, payloads, and exception details are not logged or cached.

The create owner is the verified HTTP request identity. Resolve the third party from the project,
reject inconsistent explicit links, and verify related objects through the same credential.
Allow only manual event types, including `email` mapped to `AC_EMAIL`; automatic history remains
readable and individually deletable subject to Dolibarr permissions. Send neither email fields
nor send/invitation calls. Dolibarr's administrator-configured ACTION_CREATE/DELETE triggers remain
outside the adapter's control.

Accept ISO local dates/date-times (also DD.MM.YYYY dates) and an explicit IANA timezone. Date-only
input implies an all-day event. Match Dolibarr's form convention: local midnight through 23:59:59
on the inclusive final date, using `datep`, `datef`, and `fulldayevent`. Compute each boundary
independently across DST. Reject nonexistent local times and ambiguous times without an explicit
UTC offset; validate supplied offsets against the selected timezone. The timezone is not stored by
the API. Installations using MAIN_STORE_FULL_EVENT_IN_GMT must explicitly use timezone UTC.

Use the existing stateless preview-token canonicalization. Bind normalized input, verified owner,
project/third-party state, and matching duplicate IDs. Delete binds the requested event ID and its
full allowlisted upstream state, including modification time and untruncated allowed text; previews
show title, dates, owner, and project. Reread on apply and reject stale tokens before writing.
Tokens are state/workflow guards, not credentials or expiring authorization grants.

New tools translate only sanitized domain messages into the SDK's expected ToolError, including
an explicit scan-limit failure. Validation input and upstream exception text never enter messages.

Issue one POST or DELETE at most and perform an authoritative reread. Definite permission and
validation errors propagate safely. Uncertain transport/server responses return partial outcomes,
known IDs and reconciliation candidates where available, even if a matching saved record is found.
Never retry or assert exactly-once delivery. Delete verification requires a GET returning 404;
403 is not proof of absence. Existing hooks and concurrent writes prevent atomic compare-and-delete
and transactional create/reread; document that limitation.

## Verification

Use mock HTTP upstreams behind the full ASGI authentication/MCP stack. Cover ordinary projects,
unchanged leads, automatic history, owner isolation, related-object access, dates/DST, stale tokens,
one-shot uncertain writes, delete races, safe projections and bounded scans. No production writes.

## References

- [Agenda API 24.0.2](https://github.com/Dolibarr/dolibarr/blob/24.0.2/htdocs/comm/action/class/api_agendaevents.class.php)
- [Agenda API 23.0.3](https://github.com/Dolibarr/dolibarr/blob/23.0.3/htdocs/comm/action/class/api_agendaevents.class.php)
- [ActionComm persistence](https://github.com/Dolibarr/dolibarr/blob/24.0.2/htdocs/comm/action/class/actioncomm.class.php)
- [Agenda form dates](https://github.com/Dolibarr/dolibarr/blob/24.0.2/htdocs/comm/action/card.php)
- [Projects API](https://github.com/Dolibarr/dolibarr/blob/24.0.2/htdocs/projet/class/api_projects.class.php) <!-- codespell:ignore projet -->
