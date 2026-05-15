"""Console entry point: ``python -m mcp_peering`` or ``mcp-peering``.

Supports stdio (default), Streamable HTTP and SSE transports so the same
binary can be plugged into a local MCP client or deployed on a remote host.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace

from .config import VALID_TRANSPORTS, load_config
from .server import build_server


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcp-peering",
        description="MCP server for Peering Manager and PeeringDB.",
    )
    p.add_argument(
        "--transport",
        choices=VALID_TRANSPORTS,
        help="Transport to use (default: stdio, env: MCP_TRANSPORT).",
    )
    p.add_argument("--host", help="Bind host for HTTP/SSE transports (env: MCP_HOST).")
    p.add_argument("--port", type=int, help="Bind port for HTTP/SSE transports (env: MCP_PORT).")
    p.add_argument(
        "--path",
        help="URL path for the transport endpoint (env: MCP_PATH; defaults: /mcp, /sse).",
    )
    p.add_argument(
        "--auth-token",
        help="Bearer token required on incoming requests (env: MCP_AUTH_TOKEN).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    cfg = load_config()
    transport = replace(
        cfg.transport,
        transport=args.transport or cfg.transport.transport,
        host=args.host or cfg.transport.host,
        port=args.port if args.port is not None else cfg.transport.port,
        path=args.path if args.path is not None else cfg.transport.path,
        auth_token=args.auth_token if args.auth_token is not None else cfg.transport.auth_token,
    )
    cfg = replace(cfg, transport=transport)

    server = build_server(cfg)

    if transport.transport == "stdio":
        server.run()
        return

    from .transport import run_network

    run_network(server, transport)


if __name__ == "__main__":
    main()
