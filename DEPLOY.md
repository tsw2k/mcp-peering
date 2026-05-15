# DEPLOY.md — Operator Guide for Automated Agents

> Audience: AI/automation agents (and humans) tasked with installing, running
> and integrating **mcp-peering**. Every step is intended to be machine-
> executable. Each procedure has explicit success criteria so an agent can
> verify the outcome before moving on.

## 1. Pick a deployment mode

Decide once, then follow the matching section.

| Mode | When to use | Section |
| ---- | ----------- | ------- |
| **Local stdio** | Single user on the same machine as the MCP client (Claude Desktop / Claude Code / Cursor). Lowest setup. | [§3](#3-local-stdio-mode) |
| **Remote HTTP (Streamable HTTP)** | Server hosted on a separate machine, shared by one or more clients, or Claude installation is not on the same host. Recommended for any networked setup. | [§4](#4-remote-streamable-http-mode) |
| **Remote SSE** | Same as above, but the client only speaks the legacy SSE transport. | [§4](#4-remote-streamable-http-mode) (substitute `--transport sse`) |
| **Docker Compose** | Hosted deployments where you want a long-lived service unit, healthchecks and restarts. | [§5](#5-docker-compose-mode) |

Decision rule for agents:
- If `${MCP_TRANSPORT}` is set in the environment, use that.
- Else, if the user mentioned a hostname, domain, or "remote" / "shared",
  pick **Remote HTTP**.
- Otherwise default to **Local stdio**.

## 2. Prerequisites

| Item | How to check | How to install |
| ---- | ------------ | -------------- |
| Python ≥ 3.10 | `python3 --version` | distro package or `pyenv` |
| `pip` | `python3 -m pip --version` | `python3 -m ensurepip` |
| `git` (for source install) | `git --version` | distro package |
| `docker` + compose plugin (Docker mode only) | `docker version && docker compose version` | https://docs.docker.com/engine/install/ |
| Peering Manager API token | Log in → Admin → Users → API Tokens → Add | — |
| (Optional) PeeringDB API key | https://docs.peeringdb.com/howto/api_keys/ | — |

Generate a strong bearer token (used by network modes):

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Store it as `MCP_AUTH_TOKEN`. Treat it like any production secret.

## 3. Local stdio mode

### 3.1 Install

```bash
git clone https://github.com/tsw2k/mcp-peering.git
cd mcp-peering
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Success criteria: `mcp-peering --help` prints the usage block including
`--transport {stdio,streamable-http,sse}`.

### 3.2 Configure the MCP client

Choose the appropriate config file for the host client:

| Client | Config file |
| ------ | ----------- |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Code | `~/.claude/mcp.json` (user) or `.mcp.json` (project) |
| Cursor | Settings → MCP → Add server (stdio) |

Add this block (replace the absolute path if the executable is not on
`PATH`):

```json
{
  "mcpServers": {
    "peering": {
      "command": "/absolute/path/to/.venv/bin/mcp-peering",
      "env": {
        "PEERING_MANAGER_URL": "https://peering-manager.example.com",
        "PEERING_MANAGER_TOKEN": "REPLACE_ME",
        "PEERING_MANAGER_VERIFY_SSL": "true",
        "PEERINGDB_API_KEY": ""
      }
    }
  }
}
```

### 3.3 Verify

Restart the client. The `peering` server should appear in its MCP list with
tools `peeringdb_*` and `pm_*`. Invoke `peeringdb_get_network` with
`asn=15169` — a successful JSON response confirms the install.

## 4. Remote (Streamable HTTP) mode

### 4.1 Install on the server

```bash
sudo useradd --system --create-home --home /opt/mcp-peering mcp-peering
sudo -u mcp-peering bash -lc '
  cd /opt/mcp-peering
  git clone https://github.com/tsw2k/mcp-peering.git src
  cd src
  python3 -m venv ../venv
  ../venv/bin/pip install -e .
'
```

### 4.2 Configuration file

Create `/etc/mcp-peering.env` with mode `0600` owned by `mcp-peering`:

```ini
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1            # bind locally; expose via reverse proxy
MCP_PORT=8000
MCP_AUTH_TOKEN=<token from §2>

PEERING_MANAGER_URL=https://peering-manager.example.com
PEERING_MANAGER_TOKEN=<pm-token>
PEERING_MANAGER_VERIFY_SSL=true

PEERINGDB_URL=https://www.peeringdb.com/api
PEERINGDB_API_KEY=             # optional

HTTP_TIMEOUT=30
```

### 4.3 systemd unit

`/etc/systemd/system/mcp-peering.service`:

```ini
[Unit]
Description=mcp-peering MCP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mcp-peering
Group=mcp-peering
EnvironmentFile=/etc/mcp-peering.env
ExecStart=/opt/mcp-peering/venv/bin/mcp-peering
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mcp-peering
sudo systemctl status mcp-peering --no-pager
```

### 4.4 Reverse proxy + TLS (nginx example)

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.example.com;
    ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

    location /mcp {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $remote_addr;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_buffering    off;        # required for streaming responses
        proxy_read_timeout 1h;         # MCP keeps connections open
        proxy_send_timeout 1h;
    }
}
```

Caddy equivalent (auto-TLS):

```caddy
mcp.example.com {
    reverse_proxy /mcp* 127.0.0.1:8000 {
        flush_interval -1
        transport http { read_timeout 1h }
    }
}
```

### 4.5 Verify the server

From the server host:

```bash
# Liveness (should be 401 because no token):
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/mcp
# Expected: 401

# Authenticated negotiation (initialize message):
curl -s -i \
  -H "Authorization: Bearer ${MCP_AUTH_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -X POST http://127.0.0.1:8000/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' | head -40
# Expected: HTTP/1.1 200 OK and an `Mcp-Session-Id` header.
```

From a client machine (replace domain):

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://mcp.example.com/mcp
# Expected: 401
```

Failure modes:
- **000** → DNS/firewall problem.
- **502/504** → reverse proxy misconfig or service not running (`systemctl status mcp-peering`).
- **401 with correct token** → token mismatch; the value in `/etc/mcp-peering.env` and the one the client sends must be identical.
- **200 GET on /mcp** → the server isn't actually reached; nginx is serving a default site.

### 4.6 Connect a client

**Claude Desktop / Claude Code** (stdio-only clients) via
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote):

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
        "Authorization: Bearer ${MCP_AUTH_TOKEN}"
      ]
    }
  }
}
```

**Cursor / Continue / native HTTP clients**: configure a server with URL
`https://mcp.example.com/mcp` and add the `Authorization: Bearer <token>`
header in the client's UI.

## 5. Docker Compose mode

### 5.1 Prepare

```bash
git clone https://github.com/tsw2k/mcp-peering.git
cd mcp-peering
cp .env.example .env
# Edit .env: set MCP_AUTH_TOKEN, PEERING_MANAGER_URL, PEERING_MANAGER_TOKEN
```

Required keys in `.env`: `MCP_AUTH_TOKEN`, `PEERING_MANAGER_URL`,
`PEERING_MANAGER_TOKEN`. Compose will refuse to start if any are missing.

### 5.2 Run

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f mcp-peering
```

By default the service binds to `127.0.0.1:8000`. Front it with a reverse
proxy as in §4.4, or change the published port in `docker-compose.yml` if
you intentionally want to expose it.

### 5.3 Verify

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/mcp        # → 401
docker compose exec mcp-peering python -c "import socket; socket.create_connection(('127.0.0.1',8000),2)"
```

## 6. Configuration reference

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `PEERING_MANAGER_URL` | — | Base URL of your Peering Manager (no trailing `/api`). |
| `PEERING_MANAGER_TOKEN` | — | API token (`Authorization: Token <token>`). |
| `PEERING_MANAGER_VERIFY_SSL` | `true` | Set `false` only for trusted lab self-signed certs. |
| `PEERINGDB_URL` | `https://www.peeringdb.com/api` | Override only if mirroring. |
| `PEERINGDB_API_KEY` | empty | Optional API key. |
| `PEERINGDB_USERNAME` / `PEERINGDB_PASSWORD` | empty | Legacy basic auth. |
| `HTTP_TIMEOUT` | `30` | Outbound HTTP timeout (seconds). |
| `MCP_TRANSPORT` | `stdio` | `stdio` / `streamable-http` / `sse`. |
| `MCP_HOST` | `127.0.0.1` | Bind address for network transports. |
| `MCP_PORT` | `8000` | Bind port. |
| `MCP_PATH` | `/mcp` or `/sse` | URL path mounted by the server. |
| `MCP_AUTH_TOKEN` | empty | Required bearer token; if empty, **no authentication is enforced**. |

CLI flags (`mcp-peering --help`) override the matching env vars.

## 7. Tools the LLM can call

(See `README.md` for full descriptions.)

- **PeeringDB**: `peeringdb_list_resources`, `peeringdb_search`,
  `peeringdb_get`, `peeringdb_get_network`, `peeringdb_network_presence`,
  `peeringdb_search_ix`, `peeringdb_ix_members`.
- **Peering Manager**: `pm_endpoints`, `pm_status`, `pm_list`, `pm_get`,
  `pm_create`, `pm_update`, `pm_delete`, `pm_find_autonomous_system`,
  `pm_sync_as_with_peeringdb`, `pm_router_configuration`,
  `pm_poll_internet_exchange_sessions`.
- **Cross-source**: `compare_as_with_peeringdb`.

Mutating tools (`pm_create`, `pm_update`, `pm_delete`, `pm_sync_*`,
`pm_poll_*`) act on the connected Peering Manager and are not reversible
automatically. An agent should ask the user for confirmation before
calling them unless explicit prior authorization exists.

## 8. Day-2 operations

### 8.1 Logs

| Mode | Command |
| ---- | ------- |
| systemd | `journalctl -u mcp-peering -f` |
| Docker | `docker compose logs -f mcp-peering` |
| Foreground | `mcp-peering -v` |

### 8.2 Rotate the bearer token

1. Generate a new token (§2).
2. Update `/etc/mcp-peering.env` (or `.env`) and reload:
   - systemd: `sudo systemctl restart mcp-peering`
   - Docker: `docker compose up -d`
3. Update the token in every client config and restart those clients.
4. Smoke-test (§4.5) with the new token.

### 8.3 Rotate the Peering Manager token

Same as above, but for `PEERING_MANAGER_TOKEN`. Clients do not need to be
restarted — only the server.

### 8.4 Upgrade

```bash
cd /opt/mcp-peering/src
sudo -u mcp-peering git pull
sudo -u mcp-peering /opt/mcp-peering/venv/bin/pip install -e .
sudo systemctl restart mcp-peering
```

Docker: `docker compose pull && docker compose up -d --build`.

### 8.5 Backup / state

The server is **stateless**. Nothing needs backing up beyond the
configuration files (`/etc/mcp-peering.env`, `docker-compose.yml`, `.env`).

## 9. Security checklist

Before exposing the server outside `127.0.0.1`, verify ALL of:

- [ ] `MCP_AUTH_TOKEN` is set and ≥ 32 random characters.
- [ ] The endpoint is only reachable via HTTPS (terminate TLS in a reverse
      proxy; never expose the raw HTTP port to the Internet).
- [ ] `PEERING_MANAGER_VERIFY_SSL=true` (or there is a documented reason
      to disable it).
- [ ] The Peering Manager API token is scoped to the minimum permissions
      the client actually needs. Prefer a read-only token unless mutating
      tools are intended to be used.
- [ ] The config file containing tokens is mode `0600`, owned by the
      service user.
- [ ] Logs do not contain tokens. (mcp-peering does not log them by
      default; do not enable `-v` permanently in production.)
- [ ] Outbound egress from the host can reach `www.peeringdb.com` and your
      Peering Manager host.

## 10. Troubleshooting matrix

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `mcp-peering: command not found` | venv not activated or not on `PATH`. | Use the absolute path or activate the venv. |
| Client shows "MCP server failed to start" (stdio) | Bad path to executable or missing env vars. | Re-check the JSON config; run the same command in a shell. |
| Network 401 on every request, token correct | Whitespace/quoting around `MCP_AUTH_TOKEN` in env file. | Re-set without quotes/spaces. |
| Network 502 from nginx | Service down or wrong upstream port. | `systemctl status mcp-peering`; check `proxy_pass` URL. |
| Tools hang on PM calls | Outbound to PM blocked or `PEERING_MANAGER_URL` wrong. | `curl -H "Authorization: Token …" $PEERING_MANAGER_URL/api/status/`. |
| `Peering Manager is not configured` error from a tool | `PEERING_MANAGER_URL` or `PEERING_MANAGER_TOKEN` missing. | Set them, restart the server. |
| PeeringDB tools return 429 | Rate-limited; add an API key. | Set `PEERINGDB_API_KEY`. |
| Streaming responses cut off after 60 s | Reverse-proxy buffering or short timeout. | Add `proxy_buffering off` and increase `proxy_read_timeout`. |

## 11. One-shot agent recipe (TL;DR)

```bash
# 1. Install (server host)
git clone https://github.com/tsw2k/mcp-peering.git && cd mcp-peering
python3 -m venv .venv && . .venv/bin/activate && pip install -e .

# 2. Generate secrets
export MCP_AUTH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')

# 3. Start (foreground; switch to systemd / docker for production)
MCP_TRANSPORT=streamable-http \
MCP_HOST=0.0.0.0 \
MCP_PORT=8000 \
PEERING_MANAGER_URL=https://peering-manager.example.com \
PEERING_MANAGER_TOKEN=REPLACE_ME \
mcp-peering

# 4. Smoke-test from another shell
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/mcp           # 401
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8000/mcp \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
# Expected: 200
```

When this returns `200`, the server is ready for clients. Hand the URL and
`MCP_AUTH_TOKEN` to the user (or write them into the client config) and the
deployment is done.
