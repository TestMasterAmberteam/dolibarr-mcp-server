# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project governance, security automation, and release preparation.
- Read-only `dolibarr_my_time_report`, `dolibarr_project_time_report`,
  `dolibarr_task_timespent`, `dolibarr_time_summary`, and `dolibarr_time_entries` tools.
- Typed, bounded time-entry normalization, filtering, aggregation, and pagination for Dolibarr 23+.
- Actively cleared request-scoped credential context for per-user reporting reads (ADR 0002).

### Fixed

- Delayed server-only imports so clean-wheel `--help` and `--version` checks need no dependencies.

### Security

- Raised the development test runner to `pytest>=9.0.3` to exclude `PYSEC-2026-1845`.

## [0.1.0] - 2026-08-19

### Added

- Initial open source repository bootstrap.
- Stateless MCP v2 Streamable HTTP server.
- Direct, per-request Dolibarr API-key authentication.
- Read-only `dolibarr_whoami` tool and operational health endpoints.
- Strict tests, packaging, container, CI, and security gates.
