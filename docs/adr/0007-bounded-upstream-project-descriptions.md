# ADR 0007: Bounded upstream project descriptions

- Status: Accepted
- Date: 2026-09-03

## Context

Dolibarr 23.0.3 declares `llx_projet.description` as SQL `TEXT`. MySQL stores a `TEXT` value
with a two-byte length prefix and an actual byte length below `2^16`. The MCP upstream model
previously imposed a 4000-character limit on the same field.

Project search validates a fixed page before applying local lead and query filters. Consequently,
one accessible project with a longer description caused the complete page, including unrelated
lead searches, to fail with `InvalidDolibarrResponseError`. Search results do not expose the
description, and lead details already apply a separate 4000-character output limit.

## Decision

Use a 65,535-byte UTF-8 budget for the incoming project description. Before Pydantic validates the
project payload:

- leave non-string values unchanged so the typed schema can reject them;
- encode strings as UTF-8 with invalid surrogate replacement;
- keep at most 65,535 bytes;
- decode the prefix while dropping an incomplete trailing multibyte character.

Retain a Pydantic character limit of 65,535 as a secondary invariant. The byte normalization always
produces a value within that character limit.

This decision applies only to the upstream `DolibarrProjectPayload.description`. Lead search
continues to omit descriptions. Lead details, create inputs, and update inputs retain their
4000-character limits.

## Consequences

Positive:

- a valid project page is no longer rejected only because a description exceeds the previous
  application limit;
- storage and model memory remain explicitly bounded;
- truncation never emits malformed UTF-8 or splits a multibyte character;
- public response and write boundaries do not expand.

Costs:

- content beyond the byte budget is intentionally unavailable to local search and detail
  projection;
- a single-byte database character set can hold text that expands when encoded as UTF-8, so the
  transport-side representation may be truncated earlier than the database value;
- truncation is deterministic but does not add a new public metadata field.

## Rejected alternatives

- Removing the limit was rejected because paged reads need a hard memory bound.
- Keeping 4000 characters was rejected because it does not reflect Dolibarr's project storage and
  caused unrelated lead searches to fail.
- Counting characters only was rejected because it makes the memory bound depend on Unicode
  content.
- Raising the public detail and write limits was rejected because the reported problem is an
  upstream-read compatibility issue, not a request to widen the MCP data boundary.
