"""Network transport runner with optional bearer-token middleware."""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from .config import TransportConfig

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


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


def run_network(server: FastMCP, transport_cfg: TransportConfig) -> None:
    """Serve ``server`` over HTTP/SSE using uvicorn.

    For ``streamable-http`` we mount :meth:`FastMCP.streamable_http_app`;
    for ``sse`` we mount :meth:`FastMCP.sse_app`. Optional bearer auth wraps
    the resulting ASGI app.
    """
    import uvicorn

    server.settings.host = transport_cfg.host
    server.settings.port = transport_cfg.port

    if transport_cfg.transport == "streamable-http":
        if transport_cfg.path:
            server.settings.streamable_http_path = transport_cfg.path
        app = server.streamable_http_app()
        path = server.settings.streamable_http_path
    elif transport_cfg.transport == "sse":
        if transport_cfg.path:
            server.settings.sse_path = transport_cfg.path
        app = server.sse_app()
        path = server.settings.sse_path
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
