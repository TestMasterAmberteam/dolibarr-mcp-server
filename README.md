# dolibarr-mcp-server

A remote, stateless [Model Context Protocol](https://modelcontextprotocol.io/) server that
authenticates each caller with that caller's own Dolibarr API key. It exposes 30 allowlisted tools
for identity, time reporting, sales records, and leave requests while preserving
Dolibarr's per-user permissions.

This is an independent community project. It is not affiliated with, endorsed by, or maintained
by the Dolibarr project or its maintainers.

> **MVP status:** suitable for evaluation and controlled deployments. The project intentionally
> has no local users, sessions, database, token cache, administrator key, or OAuth façade.

## Features

- official MCP Python SDK v2 and Streamable HTTP at `/mcp`;
- one shared, stateless MCP server and connection-pooled Dolibarr client;
- strict `Authorization: Bearer <DOLIBARR_API_KEY>` parsing on every MCP HTTP request;
- per-request verification through `GET /api/index.php/users/info` with `DOLAPIKEY`;
- request-scoped safe identity and credential context; API keys are actively cleared and never
  retained or returned;
- typed time-entry detail and aggregation by day, user, project, task, month, and ISO week;
- API-only third-party and project-lead search with allowlisted typed projections;
- typed leave-type lookup and leave-request search, detail, create, update, submit, approve,
  refuse, cancel, and reopen operations through official fixed API paths;
- two-step preview-token confirmation for every create, update, and lifecycle transition;
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
    D -->|validated safe profile| X[request-scoped auth and credential contexts]
    X --> M[MCPServer: 30 allowlisted tools]
    M -->|DOLAPIKEY: same user key| R[Fixed Dolibarr REST operations]
    R -->|allowlisted reporting, sales, and leave data| M
    M --> P[Stateless mutation preview and stale-state token]
```

The Bearer value is a Dolibarr API key, not an OAuth access token. The server does not publish
OAuth discovery metadata and does not pretend to be an authorization server. It validates the
key on every HTTP request, projects the upstream response to `user_id`, `login`, `first_name`, and
`last_name`, and drops the presented key when that request ends.

See [architecture](docs/architecture.md), [security design](docs/security.md), and
[ADR 0001](docs/adr/0001-direct-dolibarr-api-key-authentication.md) plus
[ADR 0002](docs/adr/0002-request-scoped-reporting-credentials.md) and
[ADR 0003](docs/adr/0003-confirmed-api-only-sales-writes.md) plus
[ADR 0004](docs/adr/0004-confirmed-api-only-leave-request-workflow.md) and
[ADR 0005](docs/adr/0005-file-only-non-secret-configuration.md). The remaining accepted records
are listed in [`docs/adr`](docs/adr/).

## Requirements

- Python 3.12 or newer;
- [uv](https://docs.astral.sh/uv/);
- Dolibarr 23.0 or newer with its REST API, Projects module, and
  Leave/Holiday module enabled;
- one Dolibarr API key per MCP user;
- HTTPS and a rate-limiting reverse proxy for production.

## Install and run with uv

```bash
uv sync --locked --all-groups
cp config.example.toml config.toml
# Edit dolibarr_base_url and the MCP allowlists in config.toml.
uv run dolibarr-mcp
```

The server listens on `127.0.0.1:8000` by default. `dolibarr-mcp --help` and
`dolibarr-mcp --version` do not require application configuration. Use
`dolibarr-mcp --config path/to/config.toml` to select another file explicitly.

## Docker and Compose

```bash
docker build -t dolibarr-mcp-server:0.4.1 .
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -p 8000:8000 \
  --mount type=bind,src="$PWD/config.toml",dst=/app/config.toml,readonly \
  dolibarr-mcp-server:0.4.1
```

For a container, set `host = "0.0.0.0"` and the external Host allowlist in `config.toml` before
running Docker or `docker compose up --build`. Compose mounts that file read-only. Neither the
image, Compose file, nor server configuration contains a user API key.

## Configuration

| `config.toml` key | Default | Purpose |
| --- | --- | --- |
| `dolibarr_base_url` | required | Fixed HTTPS Dolibarr base URL, including any subdirectory |
| `dolibarr_ca_bundle` | unset | Optional private CA PEM path, relative to the config file or absolute |
| `host` | `127.0.0.1` | Uvicorn bind host |
| `port` | `8000` | Uvicorn bind port |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `mcp_allowed_hosts` | loopback list | Exact `Host` values; localhost `:*` is accepted for development |
| `mcp_allowed_origins` | empty list | Exact browser Origins; empty rejects requests carrying Origin |
| `dolibarr_connect_timeout` | `5` | Connection timeout in seconds |
| `dolibarr_read_timeout` | `10` | Read timeout in seconds |
| `dolibarr_write_timeout` | `10` | Write timeout in seconds |
| `dolibarr_pool_timeout` | `5` | Pool acquisition timeout in seconds |
| `dolibarr_max_connections` | `100` | Maximum shared upstream connections |
| `dolibarr_max_keepalive_connections` | `20` | Maximum idle keep-alive connections |
| `[dolibarr_lead_stage_catalog.<CODE>]` | empty | Optional verified stage rows with ID, label, aliases, percentage, position, and activity |
| `allow_insecure_localhost` | `false` | Permit HTTP only for literal loopback development URLs |

The server does not load `.env` or application settings from process environment variables.
There is deliberately no server-side `DOLIBARR_API_KEY` setting. A client may keep its own API key
in a secret environment variable and interpolate it into the request header shown below.

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

## Tools

All dates use `YYYY-MM-DD`, are interpreted in UTC, and form an inclusive range. Durations always
include exact `duration_seconds`; `duration_hours` is a rounded display value. Positive identifiers
are fixed filters, never arbitrary Dolibarr paths or `sqlfilters`.

| Tool | Required arguments | Result |
| --- | --- | --- |
| `dolibarr_whoami` | none | The safe identity already verified for the request |
| `dolibarr_my_time_report` | `date_from`, `date_to` | Current user's time grouped by day, project, and task |
| `dolibarr_project_time_report` | `project_id`, `date_from`, `date_to` | Project totals grouped independently by user and task |
| `dolibarr_task_timespent` | `task_id`, `date_from`, `date_to` | Paged detailed entries for one task, including bounded notes |
| `dolibarr_time_summary` | `date_from`, `date_to`, `group_by` | Totals by `user`, `project`, `task`, `month`, or `week` |
| `dolibarr_time_entries` | `date_from`, `date_to` | Paged raw entries with optional `user_id`, `project_id`, and `task_id` filters |

Grouped tools accept `limit` from 1 to 500 and report `truncated` explicitly. Detailed tools accept
`offset` and `limit` from 1 to 500, and return `entry_count`, `returned_entry_count`, and `has_more`.
Aggregate tools never return notes. Only `dolibarr_task_timespent` and `dolibarr_time_entries`
return notes, truncated to 4000 characters.

Sales reads use only fixed official Dolibarr REST endpoints. A lead is a project whose
`usage_opportunity` flag is set; it is not inferred from the third party's `prospect`
classification. Incoming project descriptions are normalized to at most 65,535 UTF-8 bytes before
typed validation; excess content is truncated at a complete character boundary instead of
rejecting the entire project page. Search results still omit descriptions, lead details truncate
them to 4000 characters, and create/update inputs retain their 4000-character limit.

| Read-only tool | Main arguments | Result |
| --- | --- | --- |
| `dolibarr_thirdparty_search` | optional `query`, `customer_status` | Paged third parties matched on allowlisted fields |
| `dolibarr_thirdparty_get` | `thirdparty_id` | Allowlisted third-party details and bounded notes |
| `dolibarr_user_search` | optional `query` | Active users safe to select for lead assignment |
| `dolibarr_lead_search` | optional query, third party, stage, state, owner | Only projects with `usage_opportunity=1` |
| `dolibarr_lead_stage_list` | optional stage-code or ID `query` | Operator-configured plus observed stage ID/code pairs, explicitly incomplete |
| `dolibarr_lead_get` | `project_id` | Lead data, sales stage, project state, and `PROJECTLEADER` owners |

Leave reads also use fixed official endpoints and local filtering. Results include only typed
request fields; Dolibarr decides which employees and requests the caller may access.

| Read-only tool | Main arguments | Result |
| --- | --- | --- |
| `dolibarr_leave_type_list` | none | Active leave types and their balance behavior |
| `dolibarr_leave_request_search` | optional employee, status, and overlapping date range | Paged leave-request summaries |
| `dolibarr_leave_request_get` | `request_id` | One allowlisted leave request with bounded text |

Write tools always default to preview. The first call uses `apply=false` and returns changes,
warnings, and a 64-character `confirmation_token`. Repeat the same call with `apply=true` and that
token to write. The server re-reads the current API state and rejects a missing or stale token.

| Confirmed write tool | Behavior |
| --- | --- |
| `dolibarr_thirdparty_create` | Create a third party with an explicit customer classification |
| `dolibarr_thirdparty_update` | Update only allowlisted company fields |
| `dolibarr_lead_create` | Create a draft project with `usage_opportunity=1`, existing third party, and stage |
| `dolibarr_lead_update` | Update lead facts without changing its control fields |
| `dolibarr_lead_change_status` | Change the opportunity stage using exactly one `stage_id` or resolvable `stage_code` |
| `dolibarr_lead_assign` | Replace internal `PROJECTLEADER` relations with one active user |
| `dolibarr_lead_open_project` | Validate a draft project or reopen a closed one |
| `dolibarr_lead_close_project` | Set an open lead project's lifecycle state to closed with an explicit REST-semantics warning |
| `dolibarr_leave_request_create` | Create a draft request; Dolibarr validates balance and overlap |
| `dolibarr_leave_request_update` | Update allowlisted fields only while the request is draft |
| `dolibarr_leave_request_submit` | Move a draft request to submitted |
| `dolibarr_leave_request_approve` | Approve a submitted request with an explicit balance warning |
| `dolibarr_leave_request_refuse` | Refuse a submitted request with a required reason |
| `dolibarr_leave_request_cancel` | Cancel a submitted or approved request |
| `dolibarr_leave_request_reopen` | Reopen a canceled request to submitted |

`customer_status` accepts `neutral`, `customer`, `prospect`, or `customer_and_prospect`. Create a
third party intended for a new lead with `customer_status="prospect"`, then pass its returned ID to
`dolibarr_lead_create`. Lead creation leaves the project in draft. Opening and closing it are
separate confirmed calls.

Dolibarr 23.0.3 does not expose its complete opportunity-stage dictionary through REST. Configure
stages only after verifying their values in the target Dolibarr installation. Use the canonical
Dolibarr code as the table key and keep business shorthand in `aliases`, for example:

```toml
[dolibarr_lead_stage_catalog.LOST]
id = 7
label = "P3L - Lost"
aliases = ["P3L"]
percent = 0
position = 70
active = true
```

`dolibarr_lead_stage_list` merges that trusted operator catalog with stages observed on accessible
leads and exposes configured metadata. `dolibarr_lead_change_status` accepts either the numeric
`stage_id` or a canonical code/alias in `stage_code`; configured resolution prefers the operator
catalog and otherwise requires exactly one observed match. A configured inactive stage is rejected
whether selected by code, alias, or ID. The list remains `complete=false`, so an empty result is
never presented as a complete Dolibarr dictionary.

Dolibarr 23.0.3 has no dedicated project-close REST action. The close tool therefore uses the
official fixed project update endpoint with the sole payload `{"status": 2}`, re-reads the lead,
and reports `partial` unless the closed state is visible. Every preview and result warns that this
path does not guarantee `PROJECT_CLOSE` triggers, closing-user/date metadata, or GUI-equivalent
semantics.

Leave-request status transitions are deliberately constrained:
`draft -> submitted -> approved|refused`, `submitted|approved -> canceled`, and
`canceled -> submitted`. Delete is not exposed. Approval never bypasses Dolibarr's permissions,
balance, overlap, or module policy; the preview calls this out before confirmation.

`half_day_mode` accepts:

- `full_days`;
- `start_afternoon_end_afternoon`;
- `start_morning_end_morning`;
- `start_afternoon_end_morning`.

## Health and operations

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

Health probes never contact Dolibarr. Production deployments must terminate TLS at a trusted
reverse proxy or ingress, preserve an allowlisted `Host`, reject oversized requests, apply rate
limits, and avoid logging authorization headers. The application does not trust `X-Forwarded-*`
headers. CORS is not enabled; `mcp_allowed_origins` only validates an Origin if a browser sends one.

Invalid Dolibarr statuses, JSON, and typed payloads still produce the same generic MCP error. The
server also writes one `WARNING` event named `upstream_response_invalid`, correlated by the normal
request ID. It contains only a fixed failure category, HTTP method, status, fixed schema name, and
for schema failures at most 20 validation field locations and error types. It never includes the
upstream URL, path, query, headers, response body, rejected values, object IDs, user identity, or
exception text.

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
uv run twine check dist/*.whl dist/*.tar.gz
uv run pip-audit
uv run codespell
```

See [development.md](docs/development.md) for the clean-wheel, workflow, and container checks.

## MVP limitations

- no resources or prompts, and no delete tools;
- no OAuth discovery or token exchange;
- clients must support a custom Bearer header;
- one configured Dolibarr host per server deployment;
- no automatic retry or in-process rate limiter;
- no multi-tenant base URL supplied by a caller or model.
- global reports use bounded fan-out because Dolibarr 23 has no global paginated time-entry API;
  requests fail explicitly beyond 1000 accessible tasks or 50,000 time lines.
- sales search uses bounded API pagination and never accepts `sqlfilters`;
- sales writes exclude extrafields, bank data, personal contacts, arbitrary payload fields, and
  returning a project to draft;
- Dolibarr 23.0.3 has no official endpoint exposing the complete lead-stage dictionary; the stage
  tool merges the optional verified operator catalog with ID/code pairs observed on accessible
  leads and marks the result incomplete;
- project closing uses a fixed generic update because Dolibarr 23.0.3 lacks a dedicated close REST
  action; close triggers and close audit metadata are not guaranteed and are warned before apply;
- multi-call owner replacement is not transactional; a partial result returns the refreshed state.
- leave searches process at most 10,000 accessible requests and never accept `sqlfilters`;
- only drafts can be edited, status transitions use dedicated Dolibarr action endpoints, and no
  delete operation is exposed;
- leave balance and negative-balance policy remain exclusively authoritative in Dolibarr.

Rotate and revoke user keys in Dolibarr. Report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md); never place credentials or exploit details in a public issue.

## Community

- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)
- [GitHub publication checklist](docs/github-publishing.md)
- [MIT License](LICENSE)
