"""No telemetry: the MCP SDK must not emit OpenTelemetry spans from our server."""

from __future__ import annotations

import mcp.shared._otel as sdk_otel
import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.shared import jsonrpc_dispatcher
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from mcp_peering.server import build_server
from mcp_peering.telemetry import is_telemetry_middleware, silence_sdk_tracer
from tests.test_server import _config


class _RecordingTracer(trace.NoOpTracer):
    """Records the kind of every span started; the spans themselves stay no-op."""

    def __init__(self, sink: list[SpanKind]) -> None:
        self._sink = sink

    def start_span(self, name, context=None, kind=SpanKind.INTERNAL, *args, **kwargs):
        self._sink.append(kind)
        return super().start_span(name, context, kind, *args, **kwargs)

    def start_as_current_span(self, name, context=None, kind=SpanKind.INTERNAL, *args, **kwargs):
        self._sink.append(kind)
        return super().start_as_current_span(name, context, kind, *args, **kwargs)


class _RecordingProvider(trace.NoOpTracerProvider):
    def __init__(self) -> None:
        self.kinds: list[SpanKind] = []

    def get_tracer(self, *args, **kwargs):
        return _RecordingTracer(self.kinds)


@pytest.fixture(scope="module")
def recorder() -> _RecordingProvider:
    """Install a span recorder as the global provider, as an exporter would."""
    provider = _RecordingProvider()
    trace.set_tracer_provider(provider)
    if trace.get_tracer_provider() is not provider:
        pytest.skip("a global tracer provider was already installed")
    return provider


@pytest.fixture
def live_sdk_tracer(monkeypatch):
    """Give the SDK its original, provider-backed tracer back for one test.

    Silencing is process-wide, so any build_server() earlier in the run has
    already swapped it out; without this the control would pass vacuously.
    """
    monkeypatch.setattr(sdk_otel, "_tracer", trace.get_tracer("mcp-python-sdk"))


async def _spans(server: MCPServer, recorder: _RecordingProvider) -> dict[SpanKind, int]:
    recorder.kinds.clear()
    async with Client(server) as client:  # inbound: SERVER spans
        await client.list_tools()
        await client.call_tool("pm_endpoints", {})
    # Outbound: what the dispatcher wraps a server->client request in.
    with jsonrpc_dispatcher.otel_span("MCP send sampling/createMessage", kind=SpanKind.CLIENT):
        pass
    return {k: recorder.kinds.count(k) for k in (SpanKind.SERVER, SpanKind.CLIENT)}


def test_sdk_tracer_hook_still_exists():
    """If an SDK release renames the private tracer, fail here instead of tracing silently."""
    assert silence_sdk_tracer() is True


def test_build_server_drops_only_the_telemetry_middleware():
    middleware = build_server(_config()).middleware
    assert not any(is_telemetry_middleware(m) for m in middleware)
    assert any(type(m).__name__ == "RequestStateBoundary" for m in middleware)


@pytest.mark.asyncio
async def test_sdk_default_emits_spans(recorder, live_sdk_tracer):
    """Control: proves the recorder sees both span kinds, so the next test cannot pass vacuously."""
    plain = MCPServer(name="control")

    @plain.tool()
    async def pm_endpoints() -> dict[str, str]:
        return {}

    counts = await _spans(plain, recorder)
    assert counts[SpanKind.SERVER] > 0
    assert counts[SpanKind.CLIENT] > 0


@pytest.mark.asyncio
async def test_our_server_emits_no_spans_inbound_or_outbound(recorder, live_sdk_tracer):
    # Starts from a live SDK tracer, so it is build_server() that silences it.
    counts = await _spans(build_server(_config()), recorder)
    assert counts == {SpanKind.SERVER: 0, SpanKind.CLIENT: 0}
