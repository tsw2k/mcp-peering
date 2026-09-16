"""Network transport runner with optional bearer-token middleware."""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from .config import TransportConfig

if TYPE_CHECKING:
    from mcp.server import MCPServer

logger = logging.getLogger(__name__)

# Loopback hostnames used by mcp's automatic DNS-rebinding protection.
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _transport_security(cfg: TransportConfig):
    """Build the transport security settings for the network transports.

    mcp 2.x auto-enables DNS-rebinding protection when the app is built for a
    loopback host, rejecting any request whose ``Host`` header is not
    loopback. That would silently break every documented deployment here:
    bind to 127.0.0.1, put nginx/Caddy in front, and let it forward the
    public hostname (the SDK answers 421 to those requests).

    So the protection is applied only when the operator opts in by listing
    their proxy's hostname in ``MCP_ALLOWED_HOSTS`` (and optionally
    ``MCP_ALLOWED_ORIGINS``). Without it we keep the pre-2.x behaviour and
    say so loudly, because a missing allowlist then means "rejected", not
    "unprotected".
    """
    from mcp.server.transport_security import TransportSecuritySettings

    if not cfg.allowed_hosts and not cfg.allowed_origins:
        logger.warning(
            "DNS-rebinding protection is disabled (no MCP_ALLOWED_HOSTS set). "
            "The bearer token is the only request-level check in front of the "
            "service; set MCP_ALLOWED_HOSTS (comma-separated, ports allowed as "
            "':*') to enable host/origin validation."
        )
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    allowed_hosts = list(cfg.allowed_hosts)
    for local in _LOCAL_HOSTS:
        if f"{local}:*" not in allowed_hosts:
            allowed_hosts.append(f"{local}:*")
    allowed_origins = list(cfg.allowed_origins)
    for local in _LOCAL_HOSTS:
        if f"http://{local}:*" not in allowed_origins:
            allowed_origins.append(f"http://{local}:*")

    logger.info(
        "DNS-rebinding protection enabled: hosts=%s origins=%s",
        allowed_hosts,
        allowed_origins,
    )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


class BearerAuthMiddleware:
    """Minimal ASGI middleware enforcing ``Authorization: Bearer <token>``.

    Applied only when ``MCP_AUTH_TOKEN`` is set. For HTTP/SSE transports.
    Uses constant-time comparison to avoid timing leaks.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._expected = token.encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"")
        if auth.startswith(b"Bearer "):
            provided = auth[len(b"Bearer ") :].strip()
            if secrets.compare_digest(provided, self._expected):
                await self.app(scope, receive, send)
                return

        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="mcp-peering"'),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error":"unauthorized"}',
                "more_body": False,
            }
        )


def run_network(server: MCPServer, transport_cfg: TransportConfig) -> None:
    """Serve ``server`` over HTTP/SSE using uvicorn.

    For ``streamable-http`` we mount :meth:`MCPServer.streamable_http_app`;
    for ``sse`` we mount :meth:`MCPServer.sse_app`. Optional bearer auth wraps
    the resulting ASGI app.
    """
    import uvicorn

    security = _transport_security(transport_cfg)

    if transport_cfg.transport == "streamable-http":
        path = transport_cfg.path or "/mcp"
        app = server.streamable_http_app(
            streamable_http_path=path,
            transport_security=security,
        )
    elif transport_cfg.transport == "sse":
        path = transport_cfg.path or "/sse"
        app = server.sse_app(sse_path=path, transport_security=security)
    else:
        raise ValueError(f"run_network does not support transport '{transport_cfg.transport}'")

    if transport_cfg.auth_token:
        app = BearerAuthMiddleware(app, transport_cfg.auth_token)
        logger.info("bearer-token authentication enabled")
    else:
        logger.warning(
            "MCP_AUTH_TOKEN is not set: the %s endpoint is unauthenticated. "
            "Only expose it through a trusted reverse proxy or private network.",
            transport_cfg.transport,
        )

    logger.info(
        "starting mcp-peering on %s://%s:%s%s",
        transport_cfg.transport,
        transport_cfg.host,
        transport_cfg.port,
        path,
    )
    uvicorn.run(
        app,
        host=transport_cfg.host,
        port=transport_cfg.port,
        log_level="info",
    )
