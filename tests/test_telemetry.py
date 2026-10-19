"""No telemetry: the server must not emit OpenTelemetry spans of its own."""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.server import MCPServer
from opentelemetry import trace

from mcp_peering.server import _is_telemetry_middleware, build_server
from tests.test_server import _config


class _RecordingTracer(trace.NoOpTracer):
    """Records the kind of every span started; the spans themselves stay no-op."""

    def __init__(self, sink: list[trace.SpanKind]) -> None:
        self._sink = sink

    def start_span(self, name, context=None, kind=trace.SpanKind.INTERNAL, *args, **kwargs):
        self._sink.append(kind)
        return super().start_span(name, context, kind, *args, **kwargs)

    def start_as_current_span(self, name, context=None, kind=trace.SpanKind.INTERNAL, *args, **kwargs):
        self._sink.append(kind)
        return super().start_as_current_span(name, context, kind, *args, **kwargs)


class _RecordingProvider(trace.NoOpTracerProvider):
    def __init__(self) -> None:
        self.kinds: list[trace.SpanKind] = []

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


async def _server_spans(server: MCPServer, recorder: _RecordingProvider) -> int:
    recorder.kinds.clear()
    async with Client(server) as client:
        await client.list_tools()
        await client.call_tool("pm_endpoints", {})
    return recorder.kinds.count(trace.SpanKind.SERVER)


def test_build_server_drops_only_the_telemetry_middleware():
    middleware = build_server(_config()).middleware
    assert not any(_is_telemetry_middleware(m) for m in middleware)
    # Unrelated SDK middleware must survive the filter.
    assert any(type(m).__name__ == "RequestStateBoundary" for m in middleware)


@pytest.mark.asyncio
async def test_sdk_default_would_emit_server_spans(recorder):
    """Control: proves the recorder sees spans, so the next test cannot pass vacuously."""
    plain = MCPServer(name="control")

    @plain.tool()
    async def pm_endpoints() -> dict[str, str]:
        return {}

    assert await _server_spans(plain, recorder) > 0


@pytest.mark.asyncio
async def test_our_server_emits_no_server_spans(recorder):
    assert await _server_spans(build_server(_config()), recorder) == 0
