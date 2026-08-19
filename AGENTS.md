# Agent guide

## Repository map

- `src/dolibarr_mcp/`: ASGI app, strict auth, MCP server, typed configuration and Dolibarr client.
- `tests/unit/`: parser, URL, client, model, logging and CLI tests.
- `tests/integration/`: full ASGI auth and real loopback Uvicorn/MCP tests.
- `docs/adr/`: security-model decisions; update this when the trust model changes.
- `.github/workflows/`: pinned CI, security, CodeQL, Scorecard and release automation.

## Commands

```bash
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src tests
uv run pytest
uv run pre-commit run --all-files
uv build
uv run twine check dist/*
```

Run the full extended list in `docs/development.md` before declaring completion.

## Non-negotiable architecture rules

- An API key is never an MCP tool argument or a server environment setting.
- Never log credentials, full headers, bodies, query strings, upstream payloads, or identities.
- Never add OAuth without a real authorization server and truthful discovery metadata.
- Authentication tests must pass through HTTP/ASGI; an in-memory MCP client bypasses auth.
- Keep one shared async HTTP pool, but pass `DOLAPIKEY` only on an individual request.
- Do not cache credentials or user identity; Dolibarr validates every MCP HTTP request.
- Keep `dolibarr_whoami` read-only and free of a second identity lookup.

## Completion criteria

Implementation, regression tests, docs, changelog, packaging and security analysis must agree. Keep
branch coverage at least 95%, mypy strict clean, the lockfile current, and every workflow action
pinned to a full SHA. Update or add an ADR before changing authentication, tenancy, credential
lifetime, logging boundaries, or the upstream trust model.
