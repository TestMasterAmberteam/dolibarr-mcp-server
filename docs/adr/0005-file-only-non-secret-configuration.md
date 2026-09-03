# ADR 0005: File-only non-secret configuration

- Status: Accepted
- Date: 2026-09-03

## Context

The server previously loaded operational values such as the Dolibarr URL, bind address, transport
allowlists, timeouts, and the opportunity-stage dictionary from process environment variables and
`.env`. Those values are configuration, not secrets. Mixing them with secret-oriented environment
handling obscures provenance and allows an inherited process variable to alter a deployment.

User Dolibarr API keys are different: they are request credentials owned by MCP clients. The server
must never load, store, or configure a global API key.

## Decision

Load all non-secret application settings from one explicit TOML file. The CLI defaults to
`config.toml` and accepts `--config PATH`. The file is parsed with the Python standard library and
validated as a closed typed model; unknown keys fail startup. Relative private-CA paths resolve
from the configuration file's directory.

The server does not load `.env` and does not overlay application settings from process environment
variables. `config.toml` is ignored because it is installation-specific; the repository ships
`config.example.toml`. Containers mount the selected file read-only.

The server continues to accept each user's API key only in the incoming `Authorization` header and
to attach it only to that request's fixed Dolibarr calls. No API key field is added to TOML or the
server environment.

## Consequences

Positive:

- non-secret configuration has one deterministic, reviewable source;
- inherited environment variables cannot silently redirect the upstream host or change allowlists;
- the custom lead-stage dictionary is readable TOML rather than encoded JSON in an environment
  variable;
- the server/client credential boundary remains unchanged.

Costs:

- deployments must create and mount `config.toml` before startup;
- changing configuration requires editing the file and restarting the process;
- container users must set `host = "0.0.0.0"` explicitly when published ports must be reachable.

## Rejected alternatives

- Environment-over-file precedence would retain two competing sources and allow inherited values
  to change security-sensitive routing.
- A `CONFIG_PATH` environment variable would make configuration provenance partly environment
  driven; the CLI flag is explicit instead.
- Adding a server-side Dolibarr key to `.env` would violate per-user authorization and auditability.
