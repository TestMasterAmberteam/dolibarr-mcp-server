# Contributing

Thank you for improving `dolibarr-mcp-server`.

## Environment

Install Python 3.12+ and uv, then run:

```bash
uv sync --locked --all-groups
uv run pre-commit install
uv run pytest
```

Automated tests use a mock Dolibarr. Do not use production credentials, paste headers into an issue,
or add API keys to `.env.example`, fixtures, snapshots, recordings, commits, or CI secrets.

## Changes and commits

Use focused branches and [Conventional Commits](https://www.conventionalcommits.org/) such as
`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `build:`, or `chore:`. Keep unrelated formatting and
refactoring out of a functional patch.

Before opening a pull request, run every gate in [docs/development.md](docs/development.md). Update
tests, documentation, architecture records, and `CHANGELOG.md` where the behavior changes.

## Pull requests

A pull request should:

- explain the problem, solution, risk, and verification evidence;
- stay within one reviewable concern;
- avoid changes to the authentication model unless explicitly proposed and documented;
- preserve strict typing and at least 95% branch coverage;
- use HTTP/ASGI integration tests for authentication behavior;
- contain no generated build artifacts or secrets;
- pass the stable `quality-gate` and security checks;
- resolve review conversations before merge.

At least one maintainer approval is expected. Maintainers may request a smaller patch or an ADR.

## Definition of Done

- implementation, regression tests, docs, and changelog agree;
- API keys remain header-only, request-scoped, unlogged, and absent from tool schemas;
- Ruff, mypy strict, pytest, pre-commit, build, clean-wheel smoke, audits, and workflow/container
  checks pass;
- `git diff --check` is clean;
- no unexplained TODO, FIXME, placeholder, dead code, or owner-specific metadata remains.

Security concerns must follow [SECURITY.md](SECURITY.md), not the normal issue process.
