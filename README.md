# dolibarr-mcp-server

A remote, stateless [Model Context Protocol](https://modelcontextprotocol.io/) server that
authenticates each caller with that caller's own Dolibarr API key. The current `0.1.0` MVP
exposes exactly one read-only tool: `dolibarr_whoami`.

> **MVP status:** suitable for evaluation and controlled deployments. The project intentionally
> has no local users, sessions, database, token cache, administrator key, or OAuth façade.

## Features

- official MCP Python SDK v2 and Streamable HTTP at `/mcp`;
- one shared, stateless MCP server and connection-pooled Dolibarr client;
- strict `Authorization: Bearer <DOLIBARR_API_KEY>` parsing on every MCP HTTP request;
- per-request verification through `GET /api/index.php/users/info` with `DOLAPIKEY`;
- request-scoped safe identity; API keys are not retained or returned;
- explicit timeouts, connection limits, TLS verification, Host and Origin allowlists;
- unauthenticated `/health/live` and `/health/ready` probes;
- structured logs with correlation IDs and central credential redaction;
- Python 3.12–3.14 CI, strict typing, branch coverage, packaging and container gates.

## Architecture and security model

```mermaid
flowchart LR
    C[MCP client] -->|Authorization: Bearer user key| A[ASGI transport]
    A --> H[Host and Origin validation]
    H --> B[Strict Bearer middleware]
    B -->|DOLAPIKEY: user key| D[Dolibarr /users/info]
    D -->|validated safe profile| X[request-scoped auth context]
    X --> M[MCPServer]
    M --> W[dolibarr_whoami]
```

The Bearer value is a Dolibarr API key, not an OAuth access token. The server does not publish
OAuth discovery metadata and does not pretend to be an authorization server. It validates the
key on every HTTP request, projects the upstream response to `user_id`, `login`, `first_name`, and
`last_name`, and drops the presented key when that request ends.

See [architecture](docs/architecture.md), [security design](docs/security.md), and
[ADR 0001](docs/adr/0001-direct-dolibarr-api-key-authentication.md).

## Requirements

- Python 3.12 or newer;
- [uv](https://docs.astral.sh/uv/);
- a Dolibarr installation with its REST API enabled;
- one Dolibarr API key per MCP user;
- HTTPS and a rate-limiting reverse proxy for production.

## Install and run with uv

```bash
uv sync --locked --all-groups
cp .env.example .env
# Edit DOLIBARR_BASE_URL and the MCP allowlists in .env.
uv run dolibarr-mcp
```

The server listens on `127.0.0.1:8000` by default. `dolibarr-mcp --help` and
`dolibarr-mcp --version` do not require application configuration.

## Docker and Compose

```bash
docker build -t dolibarr-mcp-server:0.1.0 .
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -p 8000:8000 \
  -e DOLIBARR_BASE_URL=https://erp.example.invalid/dolibarr \
  -e HOST=0.0.0.0 \
  -e MCP_ALLOWED_HOSTS=localhost:8000 \
  dolibarr-mcp-server:0.1.0
```

Or set `DOLIBARR_BASE_URL` in the operator environment and run `docker compose up --build`.
Neither the image nor `compose.yaml` contains a user API key.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOLIBARR_BASE_URL` | required | Fixed HTTPS Dolibarr base URL, including any subdirectory |
| `DOLIBARR_CA_BUNDLE` | unset | Optional private CA PEM file |
| `HOST` | `127.0.0.1` | Uvicorn bind host |
| `PORT` | `8000` | Uvicorn bind port |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `MCP_ALLOWED_HOSTS` | loopback only | Comma-separated exact `Host` values; localhost `:*` is accepted for development |
| `MCP_ALLOWED_ORIGINS` | empty | Comma-separated exact browser Origins; empty rejects requests carrying Origin |
| `DOLIBARR_CONNECT_TIMEOUT` | `5` | Connection timeout in seconds |
| `DOLIBARR_READ_TIMEOUT` | `10` | Read timeout in seconds |
| `DOLIBARR_WRITE_TIMEOUT` | `10` | Write timeout in seconds |
| `DOLIBARR_POOL_TIMEOUT` | `5` | Pool acquisition timeout in seconds |
| `DOLIBARR_MAX_CONNECTIONS` | `100` | Maximum shared upstream connections |
| `DOLIBARR_MAX_KEEPALIVE_CONNECTIONS` | `20` | Maximum idle keep-alive connections |
| `ALLOW_INSECURE_LOCALHOST` | `false` | Permit HTTP only for literal loopback development URLs |

There is deliberately no server-side `DOLIBARR_API_KEY` setting.

## Configure an MCP client

Use a client that can attach a custom HTTP header. The exact client syntax varies, but the shape is:

```json
{
  "servers": {
    "dolibarr": {
      "type": "http",
      "url": "https://mcp.example.invalid/mcp",
      "headers": {
        "Authorization": "Bearer ${DOLIBARR_API_KEY}"
      }
    }
  }
}
```

`DOLIBARR_API_KEY` in this example belongs to the client process, not the server. Never commit it.

A deliberately fictitious HTTP example:

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer FAKE_DOLIBARR_KEY_DO_NOT_USE' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  https://mcp.example.invalid/mcp \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"example","version":"1"}}}'
```

After initialization, call `dolibarr_whoami` with no arguments. Its structured result is:

```json
{
  "user_id": 42,
  "login": "example.user",
  "first_name": "Example",
  "last_name": "User"
}
```

## Health and operations

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

Health probes never contact Dolibarr. Production deployments must terminate TLS at a trusted
reverse proxy or ingress, preserve an allowlisted `Host`, reject oversized requests, apply rate
limits, and avoid logging authorization headers. The application does not trust `X-Forwarded-*`
headers. CORS is not enabled; `MCP_ALLOWED_ORIGINS` only validates an Origin if a browser sends one.

## Development and quality gates

```bash
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src tests
uv run pytest
uv run pre-commit run --all-files
uv build
uv run twine check dist/*
uv run pip-audit
uv run codespell
```

See [development.md](docs/development.md) for the clean-wheel, workflow, and container checks.

## MVP limitations

- only `dolibarr_whoami`; no resources, prompts, or write tools;
- no OAuth discovery or token exchange;
- clients must support a custom Bearer header;
- one configured Dolibarr host per server deployment;
- no automatic retry or in-process rate limiter;
- no multi-tenant base URL supplied by a caller or model.

Rotate and revoke user keys in Dolibarr. Report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md); never place credentials or exploit details in a public issue.

## Community

- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)
- [MIT License](LICENSE)
