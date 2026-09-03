# Development

## Setup

```bash
uv sync --locked --all-groups
uv run pre-commit install
```

Copy `config.example.toml` to the ignored `config.toml` for local execution. Keep only non-secret
application settings there. The server does not load `.env` or application configuration from
environment variables. Use a fictitious or isolated Dolibarr test account; the automated suite
uses mock transports and requires no Internet or real key.

## Required local gates

```bash
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src tests
uv run pytest
uv run pre-commit run --all-files
uv build
uv run twine check dist/*.whl dist/*.tar.gz
uv run pip-audit
uv run codespell
actionlint
zizmor .
hadolint Dockerfile
git diff --check
```

To test the built wheel independently:

```bash
python -m venv .wheel-smoke
.wheel-smoke/bin/python -m pip install --no-deps dist/*.whl
.wheel-smoke/bin/python -c "import dolibarr_mcp; print(dolibarr_mcp.__version__)"
.wheel-smoke/bin/dolibarr-mcp --help
.wheel-smoke/bin/dolibarr-mcp --version
```

On Windows use `.wheel-smoke\Scripts\python.exe` and
`.wheel-smoke\Scripts\dolibarr-mcp.exe`.

`tests/integration/test_live_network.py` owns the full loopback Uvicorn smoke. Other integration
tests still pass through the complete ASGI transport and auth middleware, using only a mock
Dolibarr upstream.

## Change rules

- Keep exactly one production HTTP client implementation and one shared connection pool.
- API keys are HTTP credentials, never tool arguments or application settings.
- New auth behavior requires HTTP integration tests; in-memory MCP tests do not exercise auth.
- Do not add retries without a separate, reviewed policy for idempotency and overload.
- Keep write-capable domain tests on mock transports. Any live write requires a rotated test key,
  disposable records, and separate explicit authorization for the exact mutation.
- Do not add OAuth metadata without a real external authorization server and an ADR.
- Update documentation, changelog, tests, and security analysis together.

## Releases

Tags use `vMAJOR.MINOR.PATCH` and must equal `project.version`. The release workflow rebuilds and
checks the package, creates checksums and an SBOM, publishes a multi-architecture GHCR image, adds
attestations, and creates a GitHub Release. PyPI publishing remains disabled unless maintainers
configure Trusted Publishing, protect the `pypi` environment, and set `PUBLISH_TO_PYPI=true`.
