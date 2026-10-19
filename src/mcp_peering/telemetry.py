"""Keep the MCP SDK from emitting OpenTelemetry data.

No telemetry leaves our deployments. mcp 2.x traces by default: a
middleware wraps every inbound message in a SERVER span, and its JSON-RPC
dispatcher wraps every outbound request in a CLIENT span. Both are inert
until an OTel exporter is installed; this module makes them inert even if
one is. ``OTEL_SDK_DISABLED`` in the deployment configs stays as a second,
process-wide layer that also covers other software on the host.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from mcp.server import MCPServer

logger = logging.getLogger(__name__)


def is_telemetry_middleware(middleware: Any) -> bool:
    """Recognise the OpenTelemetry middleware mcp 2.x installs by default.

    Matched by name instead of importing it from the SDK's private
    ``mcp.server._otel`` module, so an SDK refactor cannot break start-up.
    """
    cls = type(middleware)
    return "opentelemetry" in cls.__name__.lower() or cls.__module__.endswith("._otel")


def silence_sdk_tracer() -> bool:
    """Swap the SDK's module-level tracer for a no-op one.

    Every span the SDK creates — inbound middleware and outbound dispatcher
    alike — goes through ``mcp.shared._otel.otel_span``, which looks up the
    module global ``_tracer`` on each call. Replacing it switches all of them
    off regardless of the installed tracer provider. It is a private
    attribute, so when an SDK release renames it this returns False and logs
    a warning instead of failing; the test suite asserts True, so such a
    release turns CI red rather than silently re-enabling tracing.
    """
    try:
        import mcp.shared._otel as sdk_otel
    except ImportError:
        logger.warning("mcp.shared._otel not found; SDK tracing not silenced in code")
        return False
    if not hasattr(sdk_otel, "_tracer"):
        logger.warning("mcp.shared._otel._tracer not found; SDK tracing not silenced in code")
        return False
    sdk_otel._tracer = trace.NoOpTracer()
    return True


def disable_telemetry(server: MCPServer) -> None:
    """Remove the SDK's tracing middleware from ``server`` and silence its tracer."""
    server.middleware[:] = [m for m in server.middleware if not is_telemetry_middleware(m)]
    silence_sdk_tracer()
