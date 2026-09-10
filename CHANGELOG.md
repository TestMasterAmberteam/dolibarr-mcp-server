# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial open source repository bootstrap.
- Stateless MCP v2 Streamable HTTP server.
- Direct, per-request Dolibarr API-key authentication.
- Read-only `dolibarr_whoami` tool and operational health endpoints.
- Strict tests, packaging, container, CI, and security gates.
- Bounded, request-correlated diagnostics for invalid upstream statuses, JSON, and typed payloads
  without logging response content, credentials, identities, URLs, or query parameters (ADR 0006).
- Project governance, security automation, and release preparation.
- Read-only `dolibarr_my_time_report`, `dolibarr_project_time_report`,
  `dolibarr_task_timespent`, `dolibarr_time_summary`, and `dolibarr_time_entries` tools.
- Typed, bounded time-entry normalization, filtering, aggregation, and pagination for Dolibarr 23+.
- Actively cleared request-scoped credential context for per-user reporting reads (ADR 0002).
- Read-only third-party, project-lead, and active-user lookup tools.
- Confirmed create and update tools for third parties and project-based leads.
- Separate opportunity-stage, project validate/reopen, and `PROJECTLEADER` assignment operations.
- Read-only lead-stage catalog merging validated operator records (canonical code, ID, label,
  aliases, percentage, position, and activity) with observations from accessible leads, while
  retaining explicit incompleteness metadata.
- Sales-stage changes by either numeric `stage_id` or a uniquely resolvable canonical code or alias;
  for example, business alias `P3L` resolves to canonical Dolibarr code `LOST` and ID `7` only when
  explicitly configured.
- File-only `config.toml` loading for all non-secret application settings, with an explicit
  `--config` path and no `.env` or process-environment overlay (ADR 0005).
- Preview-confirmed lead-project closing through a fixed `status=2` project update, authoritative
  reread, partial-outcome handling, and warnings about missing dedicated close semantics.
- Stateless preview tokens with stale-state checks, duplicate warnings, and explicit partial-write
  results (ADR 0003).
- Read-only leave-type, leave-request search, and leave-request detail tools.
- Confirmed create, draft update, submit, approve, refuse, cancel, and reopen tools using
  Dolibarr's fixed official Holidays API endpoints.
- Closed status transitions, half-day normalization, balance warnings, stale-state checks, and
  refreshed partial results for unexpected transition outcomes (ADR 0004).

### Fixed

- Corrected Dolibarr 23 opportunity-stage writes to submit the `opp_status` API property instead
  of the `fk_opp_status` database-column name, and made reads prefer a non-empty `opp_status`
  while retaining `fk_opp_status` as a compatibility fallback.
- Prevented overlong Dolibarr project descriptions from invalidating a complete lead-search page
  by truncating the upstream value to the SQL `TEXT`-aligned 65,535-byte UTF-8 boundary while
  retaining 4000-character public-output and write-input limits (ADR 0007).
- Delayed server-only imports so clean-wheel `--help` and `--version` checks need no dependencies.
- Distinguished canonical Dolibarr lead-stage codes from business label prefixes and rejected
  configured inactive stages for both identifier- and ID-based status changes.

### Security

- Raised the development test runner to `pytest>=9.0.3` to exclude `PYSEC-2026-1845`.
- Kept sales access API-only with fixed methods, paths, and payload allowlists; no database,
  `sqlfilters`, credentials, headers, or arbitrary upstream paths are exposed to tools.
- Kept stage discovery read-only and bounded; operator stage mappings are validated startup
  metadata, while project closing accepts no caller-supplied status or payload and always warns
  that Dolibarr's close trigger and audit metadata are absent.
- Removed non-secret application configuration from `.env`, process environment variables, and
  Compose interpolation; the per-user API key remains request-scoped and absent from server config.
- Kept leave access API-only with fixed routes, typed projections, local filters, and dedicated
  action endpoints; no database, browser automation, delete, balance override, or arbitrary
  `sqlfilters` is exposed.
