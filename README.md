# GoCanvas MCP Server (read-only)

A minimal [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes the **read-only** endpoints of the [GoCanvas API v3](https://www.gocanvas.com)
as MCP tools. Scope is limited to four areas: **Forms, Submissions, Reports, and
Reference Data**. No create/update/delete operations are exposed.

## Tools

### Forms
| Tool | Endpoint |
| --- | --- |
| `list_forms` | `GET /forms` |
| `get_form` | `GET /forms/{form_id}` |
| `list_form_assigned_users` | `GET /forms/{form_id}/assigned_users` |
| `list_form_shared_departments` | `GET /forms/{form_id}/shared_departments` |

### Submissions
| Tool | Endpoint |
| --- | --- |
| `list_submissions` | `GET /submissions` (requires `form_id`) |
| `get_submission` | `GET /submissions/{submission_id}` |
| `list_submission_revisions` | `GET /submissions/{submission_id}/revisions` |
| `get_submission_value` | `GET /submissions/{submission_id}/values/{value_id}` |

### Reports
| Tool | Endpoint |
| --- | --- |
| `list_form_reports` | `GET /forms/{form_id}/reports` |
| `get_form_report` | `GET /forms/{form_id}/reports/{report_id}` |
| `get_submission_default_pdf` | `GET /submissions/{submission_id}/pdf` (PDF) |
| `get_submission_report_pdf` | `GET /submissions/{submission_id}/reports/{report_id}` (PDF) |
| `get_submission_standard_pdf` | `GET /submissions/{submission_id}/standard_pdf` (PDF) |

The three PDF tools return the binary PDF **inline as base64** (`content_base64`,
`content_type`, `size_bytes`) — the server is a pure passthrough and never writes
to disk, so the tools work on read-only / ephemeral hosts such as AWS Lambda.

### Reference Data
| Tool | Endpoint |
| --- | --- |
| `list_reference_data` | `GET /reference_data` |
| `get_reference_data` | `GET /reference_data/{reference_data_id}` |

### Authentication
| Tool | Endpoint |
| --- | --- |
| `refresh_oauth_token` | `POST /oauth/token` (client-credentials) |

`refresh_oauth_token` forces a fresh bearer token to be fetched and cached. It
only applies to **server-side OAuth** (`GOCANVAS_CLIENT_ID` /
`GOCANVAS_CLIENT_SECRET`) mode; in passthrough mode the caller owns the token and
the server cannot refresh it. It is normally unnecessary — the server fetches a
token on startup and refreshes it automatically before expiry and on a `401` — but
it is exposed so the agent can rotate the token explicitly. The returned access
token is masked.

## Setup

This project uses [`uv`](https://docs.astral.sh/uv/). With `uv` installed, no
manual environment setup is required — `uv run` resolves and installs
dependencies (from `pyproject.toml`) automatically on first launch.

```bash
# optional: pre-create the environment
uv sync
```

<details>
<summary>Alternative: plain pip + venv</summary>

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
</details>

## Configuration

The server is a thin passthrough to the GoCanvas API and **starts with no
credentials configured**. Authentication is resolved **per request**, in the
following priority order:

| Source | Description |
| --- | --- |
| Incoming `Authorization` header | Forwarded verbatim to the GoCanvas API. This is the passthrough mode used when the server is hosted publicly behind a caller that performs its own OAuth flow (e.g. a Microsoft 365 Copilot custom agent). No server-side credentials are needed. |
| `GOCANVAS_CLIENT_ID` / `GOCANVAS_CLIENT_SECRET` | OAuth 2.0 client credentials. A short-lived bearer token is fetched from `/oauth/token`, cached, and auto-refreshed on expiry or `401`. |
| `GOCANVAS_API_TOKEN` | Static bearer token. |
| `GOCANVAS_USERNAME` / `GOCANVAS_PASSWORD` | HTTP Basic auth (fallback). |

Other optional variables:

| Variable | Description |
| --- | --- |
| `GOCANVAS_OAUTH_SCOPE` | Optional OAuth scope to request (server-side OAuth only). |
| `GOCANVAS_BASE_URL` | Defaults to `https://api.gocanvas.com/api/v3`. |
| `GOCANVAS_TIMEOUT` | HTTP timeout in seconds (default `30`). |
| `GOCANVAS_TRANSPORT` | `stdio` (default), `streamable-http`, or `sse`. |
| `GOCANVAS_HOST` | Bind host for HTTP transports (default `127.0.0.1`). |
| `GOCANVAS_PORT` | Bind port for HTTP transports (default `8000`). |

If no usable credentials are available for a call (no incoming `Authorization`
header and no configured env credentials), the tool returns a clear error — the
server itself still starts fine.

## Running

### Locally over stdio (default)

```bash
GOCANVAS_CLIENT_ID=... GOCANVAS_CLIENT_SECRET=... uv run server.py
```

### Publicly over HTTP (e.g. Microsoft 365 Copilot custom agent, AWS Lambda)

Run with an HTTP transport and **no** GoCanvas credentials — the agent's OAuth
bearer token is forwarded per request:

```bash
GOCANVAS_TRANSPORT=streamable-http GOCANVAS_HOST=0.0.0.0 GOCANVAS_PORT=8000 uv run server.py
```

The MCP endpoint is served at `/mcp`. Point your 365 Copilot custom agent's MCP
connection at the public URL and configure its OAuth so it obtains a GoCanvas
token; that token is passed through to the GoCanvas API on every tool call. No
PDFs or other state are written to disk, so the server runs cleanly on read-only
/ ephemeral hosts.

### MCP client configuration

Use `uv run` as the command. `--directory` points `uv` at this project so it
uses the right dependencies regardless of the client's working directory:

```json
{
  "mcpServers": {
    "gocanvas": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/absolute/path/to/GoCanvas",
        "server.py"
      ],
      "env": {
        "GOCANVAS_CLIENT_ID": "your_client_id",
        "GOCANVAS_CLIENT_SECRET": "your_client_secret"
      }
    }
  }
}
```

If `uv` isn't on the client's `PATH`, use its absolute path (e.g.
`~/.local/bin/uv`) as the `command`.

<details>
<summary>Alternative: point at a virtualenv interpreter (pip users)</summary>

```json
{
  "mcpServers": {
    "gocanvas": {
      "command": "/absolute/path/to/GoCanvas/.venv/bin/python",
      "args": ["/absolute/path/to/GoCanvas/server.py"],
      "env": {
        "GOCANVAS_CLIENT_ID": "your_client_id",
        "GOCANVAS_CLIENT_SECRET": "your_client_secret"
      }
    }
  }
}
```

On Windows the interpreter is at `.venv\Scripts\python.exe`. Using a bare
`python` will fail with `ModuleNotFoundError: httpx` because the client does not
use your activated shell environment.
</details>

## Notes

- **Pagination:** list tools accept a `page` argument. Response pagination headers
  (`link`, `current-page`, `page-items`, `total-count`, `total-pages`) are surfaced
  under a `pagination` key in the tool result.
- **Rate limiting:** the server honors `429 Too Many Requests` responses, waiting
  according to the `RateLimit-Reset` / `RateLimit-Remaining` headers (or a bounded
  exponential backoff) before retrying, per GoCanvas best practices.
