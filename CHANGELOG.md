# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

Versions up to and including 0.3.0 were never tagged; their dates are when
the version landed on `main`. 0.3.1 is the first version to be tagged.

## [0.3.1] — 2026-11-23

### Security
- **No telemetry.** mcp 2.x traces by default — a SERVER span per inbound
  message and a CLIENT span per outbound request. At start-up the server now
  removes the SDK's tracing middleware and swaps its tracer for a no-op one
  (`src/mcp_peering/telemetry.py`), so no spans are emitted even with an
  OpenTelemetry exporter installed. The SDK hook is private: if a future mcp
  release renames it, start-up logs a warning and a dedicated test fails CI
  instead of tracing silently coming back.
- `DO_NOT_TRACK=1` and `OTEL_SDK_DISABLED=true` are set in the `Dockerfile`,
  passed through by `docker-compose.yml` and documented in `.env.example`,
  `README.md` and `DEPLOY.md` as a second, process-wide layer.

### Changed
- Dependency capped at `mcp>=2.0.0,<3`. The test suite passes on every
  published 2.x release (2.0.0–2.3.0).

### Tests
- End-to-end rejection tests for the Host/Origin allowlist: an unlisted
  `Host` gets 421 (including lookalikes such as
  `mcp.example.com.evil.example`), an unlisted `Origin` gets 403, loopback
  stays reachable. Previously only the accepting side was tested.
- Span-recording tests covering both the inbound and outbound tracing paths,
  with a control case proving the recorder sees SDK spans.

## [0.3.0] — 2026-09-16

### Changed
- **Requires mcp 2.x** (`mcp>=2.0.0`). `FastMCP` became `MCPServer`; host,
  port and path are now passed to `streamable_http_app` / `sse_app`.

### Added
- `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` and the `--allowed-hosts` /
  `--allowed-origins` flags.

### Security
- mcp 2.x enables DNS-rebinding protection for loopback binds and answers
  **421** to any non-loopback `Host`, which would break every reverse-proxy
  deployment in `DEPLOY.md`. The protection is therefore opt-in: list the
  proxy's public hostname in `MCP_ALLOWED_HOSTS` to enable it. Loopback is
  always kept allowed for health checks. Without an allowlist the previous
  behaviour is kept and a warning is logged.

## [0.2.0] — 2026-09-08

### Added
- Automatic pagination: `peeringdb_search`, `peeringdb_ix_members`,
  `peeringdb_network_presence` and `pm_list` follow pages up to `limit`
  instead of truncating at one page.
- PeeringDB protection: TTL cache for GET responses, an outbound rate
  limiter, and one retry on 429 honouring `Retry-After`
  (`PEERINGDB_CACHE_TTL`, `PEERINGDB_CACHE_SIZE`, `PEERINGDB_RATE_LIMIT`).
- Read-only mode (`PM_READONLY=true` / `--pm-readonly`): the five mutating
  `pm_*` tools are not registered, and the client rejects writes as a second
  layer.
- `pm_action` tool for custom per-object actions (`shared-ixps`,
  `available-peers`, `as-set-prefixes`, `test-napalm-connection`, …).
- `scripts/live_smoke.py` to check the server against real PeeringDB and
  Peering Manager instances.
- CI: GitHub Actions running ruff and pytest on Python 3.10, 3.11 and 3.12.

### Changed
- Peering Manager endpoint names follow the 1.9+ URL layout (verified
  against 1.10): routers and configurations under `devices/`, communities
  under `bgp/`, jobs and object changes under `core/`. Older installations
  can still pass explicit API paths.
- Action URLs use dashes (`sync-with-peeringdb`, `poll-bgp-sessions`);
  underscores are normalised.
- `compare_as_with_peeringdb` returns `field_differences` (each differing
  field mapped to both values, including fields set on one side only) in
  place of `differing_fields`, and a structured `peering_manager_error`.
- HTTP clients are shared across tool calls for the server's lifetime.
- `User-Agent` is derived from the package version.
- `DEPLOY.md` and `docker-compose.yml` document and pass through the new
  variables.

### Fixed
- `pm_router_configuration` and `pm_poll_internet_exchange_sessions` called
  URLs that do not exist in current Peering Manager.
- A `TypeError` on list-valued PeeringDB filters such as `asn__in`.
- Concurrent first tool calls could create a duplicate HTTP client.

## [0.1.0] — 2026-05-15

### Added
- MCP server for Peering Manager and PeeringDB: PeeringDB search and lookup
  tools, Peering Manager CRUD and custom actions, AS comparison between the
  two.
- stdio, Streamable HTTP and SSE transports, with optional bearer-token
  authentication for the network ones.
- `Dockerfile` and `docker-compose.yml`.
- `DEPLOY.md`, an operator guide written for automation agents.
