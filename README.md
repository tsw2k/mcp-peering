# mcp-peering

Local [Model Context Protocol](https://modelcontextprotocol.io/) server that lets an
LLM-driven assistant query and manage:

- a self-hosted **[Peering Manager](https://github.com/peering-manager/peering-manager)**
  instance via its REST API (`/api/`, token auth), and
- the public **[PeeringDB](https://www.peeringdb.com/)** REST API.

It can run **locally over stdio** (spawned by your MCP client) **or as a remote
network service** over Streamable HTTP / SSE with optional bearer-token
authentication, so the same binary fits both single-user setups and a shared
team deployment.

## Features

PeeringDB tools:

| Tool | Purpose |
| ---- | ------- |
| `peeringdb_list_resources` | Enumerate supported PeeringDB resources. |
| `peeringdb_search` | Generic search with Django-style filters; pages are followed automatically. |
| `peeringdb_get` | Fetch a single object by id. |
| `peeringdb_get_network` | Look up a network by ASN (with optional inlined relations). |
| `peeringdb_network_presence` | Show every IXP and facility where an ASN is present. |
| `peeringdb_search_ix` | Search Internet Exchanges by name / country / city. |
| `peeringdb_ix_members` | List networks present at an IX (auto-paginated). |

Peering Manager tools:

| Tool | Purpose |
| ---- | ------- |
| `pm_endpoints` | List supported endpoint short-names. |
| `pm_status` | Read `/api/status/` from your instance. |
| `pm_list` / `pm_get` | Read objects from any endpoint (`pm_list` auto-paginates). |
| `pm_create` / `pm_update` / `pm_delete` | Generic CRUD against any endpoint (write mode only). |
| `pm_find_autonomous_system` | Find an AS by ASN inside Peering Manager. |
| `pm_sync_as_with_peeringdb` | Trigger `sync_with_peeringdb` for an AS (write mode only). |
| `pm_router_configuration` | Render the configuration for a router. |
| `pm_poll_internet_exchange_sessions` | Trigger BGP session polling for an IX (write mode only). |
| `compare_as_with_peeringdb` | Diff an AS between Peering Manager and PeeringDB. |

PeeringDB GET responses are served from a TTL cache and outbound requests
are spaced by a rate limiter, so agent loops that repeat similar queries do
not hammer the public API (both tunable, see below). HTTP clients are shared
across tool calls for the lifetime of the server, reusing connections and
TLS sessions.

## Requirements

- Python 3.10+
- A Peering Manager API token (Admin → Users → API Tokens) for write/admin tools
- (Optional) a [PeeringDB API key](https://docs.peeringdb.com/howto/api_keys/)

## Installation

```bash
git clone https://github.com/tsw2k/mcp-peering.git
cd mcp-peering
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or install from a local checkout into another environment:

```bash
pip install /path/to/mcp-peering
```

After installing the `mcp-peering` console script is available.

## Configuration

Copy `.env.example` to `.env` (or export the variables in your shell / MCP
client config):

```ini
PEERING_MANAGER_URL=https://peering-manager.example.com
PEERING_MANAGER_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
PEERING_MANAGER_VERIFY_SSL=true
PM_READONLY=false            # true: hide mutating pm_* tools entirely

PEERINGDB_URL=https://www.peeringdb.com/api
PEERINGDB_API_KEY=          # optional
PEERINGDB_USERNAME=         # optional, legacy basic auth
PEERINGDB_PASSWORD=

PEERINGDB_CACHE_TTL=300     # seconds to cache PeeringDB GET responses (0 = off)
PEERINGDB_CACHE_SIZE=128    # max cached responses (LRU eviction)
PEERINGDB_RATE_LIMIT=2      # outbound PeeringDB requests per second (0 = off)

HTTP_TIMEOUT=30

# Transport (only needed when running as a network service)
MCP_TRANSPORT=stdio          # stdio | streamable-http | sse
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_PATH=                    # default /mcp for streamable-http, /sse for sse
MCP_AUTH_TOKEN=              # bearer token required on incoming HTTP/SSE requests
```

The PeeringDB tools work without credentials (rate-limited public access). The
`pm_*` tools require both `PEERING_MANAGER_URL` and `PEERING_MANAGER_TOKEN`.

### Read-only mode

Set `PM_READONLY=true` (or pass `--pm-readonly`) to run against Peering Manager
without any ability to change it: the mutating tools (`pm_create`, `pm_update`,
`pm_delete`, `pm_sync_as_with_peeringdb`, `pm_poll_internet_exchange_sessions`)
are not registered at all, so MCP clients cannot even discover them, and the
API client additionally rejects write requests as a second line of defence.
Combine it with a read-only API token for defence in depth.

## Running

### Local (stdio)

```bash
mcp-peering        # default transport: stdio
# or
python -m mcp_peering
```

The server speaks MCP over stdio, so it is normally launched by your MCP client.

### Remote (HTTP / SSE)

Run on a separate host so multiple clients (or remote Claude installations)
can share the same server:

```bash
export MCP_AUTH_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')
export PEERING_MANAGER_URL=https://peering-manager.example.com
export PEERING_MANAGER_TOKEN=...

mcp-peering \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000
```

CLI flags (`--transport`, `--host`, `--port`, `--path`, `--auth-token`,
`--pm-readonly`) override the corresponding environment variables.

When `MCP_AUTH_TOKEN` is set, the server requires
`Authorization: Bearer <token>` on every incoming request and returns 401
otherwise. **Never expose the server without either the bearer token or a
trusted reverse proxy in front.** For TLS, terminate HTTPS in nginx / Caddy
/ Traefik and proxy to `127.0.0.1:8000`.

#### Example nginx in front of the server

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.example.com;
    # ... ssl_certificate / ssl_certificate_key ...

    location /mcp {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-For $remote_addr;
        proxy_set_header   X-Forwarded-Proto $scheme;
        # Streamable HTTP / SSE need long-lived connections:
        proxy_buffering    off;
        proxy_read_timeout 1h;
    }
}
```

#### Docker

```bash
cp .env.example .env        # fill in the variables
docker compose up -d --build
```

The provided `Dockerfile` runs as a non-root user and exposes port 8000;
`docker-compose.yml` binds the port to `127.0.0.1` by default — add a reverse
proxy / TLS for external access.

### Claude Desktop / Claude Code (local stdio)

Add an entry to `claude_desktop_config.json` (Desktop) or `~/.claude/mcp.json`
(Code):

```json
{
  "mcpServers": {
    "peering": {
      "command": "mcp-peering",
      "env": {
        "PEERING_MANAGER_URL": "https://peering-manager.example.com",
        "PEERING_MANAGER_TOKEN": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "PEERINGDB_API_KEY": ""
      }
    }
  }
}
```

If `mcp-peering` is not on `PATH`, point `command` at the absolute path of the
script inside your virtualenv (e.g. `/opt/mcp-peering/.venv/bin/mcp-peering`).

### Connecting Claude to a remote server

Claude Desktop launches stdio servers, so for a remote instance use the
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) bridge — it speaks
stdio to the client and proxies to your HTTP endpoint:

```json
{
  "mcpServers": {
    "peering": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://mcp.example.com/mcp",
        "--header",
        "Authorization: Bearer xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      ]
    }
  }
}
```

Clients with native HTTP/SSE MCP support (Cursor, Continue, recent Claude
Code builds) can connect directly — point them at
`https://mcp.example.com/mcp` and add the `Authorization` header.

### Cursor / Continue / other MCP clients

For local use the stdio launch line above works. For network use, configure
the client with the HTTP URL and the bearer token (see your client's docs
for the exact field names).

## Example interactions

- _"Find AS15169 in PeeringDB and show every IX where it is present."_ →
  `peeringdb_network_presence` with `asn=15169`.
- _"Compare what we have for AS64500 in Peering Manager vs PeeringDB and tell
  me what differs."_ → `compare_as_with_peeringdb`.
- _"Sync AS object 42 with PeeringDB."_ → `pm_sync_as_with_peeringdb`.
- _"Render the configuration for router 7."_ → `pm_router_configuration`.
- _"List BGP sessions on IX 3 with peeringdb_synced=False."_ →
  `pm_list("internet-exchange-peering-sessions", filters={"internet_exchange_id": 3})`.

## Development

```bash
pip install -e ".[dev]"
pytest        # unit tests with mocked HTTP (no install needed; see conftest.py)
ruff check src tests
```

Tests use [`respx`](https://github.com/lundberg/respx) to mock both APIs, so
no live credentials are required.

## Project layout

```
src/mcp_peering/
├── __init__.py
├── __main__.py        # `python -m mcp_peering`
├── cache.py           # TTL cache for PeeringDB GET responses
├── config.py          # env-driven configuration
├── peeringdb.py       # async PeeringDB REST client (cache + rate limit)
├── peering_manager.py # async Peering Manager REST client (read-only guard)
├── ratelimit.py       # minimum-interval async rate limiter
├── server.py          # FastMCP server + tool definitions
└── transport.py       # HTTP/SSE runner + bearer-token middleware
conftest.py            # makes tests runnable from a checkout without install
tests/                 # mocked HTTP unit tests
```

## Security notes

- The Peering Manager token grants the same permissions as the user that owns
  it. Use a dedicated read-only token if you only need read tools, and set
  `PM_READONLY=true` so mutating tools are not even registered.
- Avoid setting `PEERING_MANAGER_VERIFY_SSL=false` outside of trusted lab
  environments — it disables certificate verification.
- Treat any tool calls that mutate state (`pm_create`, `pm_update`,
  `pm_delete`, `pm_sync_*`, `pm_poll_*`) as privileged; review them before
  approving in your MCP client.

## License

MIT
