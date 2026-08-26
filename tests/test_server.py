from __future__ import annotations

import httpx
import pytest

from mcp_peering.config import (
    Config,
    PeeringDBConfig,
    PeeringManagerConfig,
    TransportConfig,
)
from mcp_peering.server import build_server

MUTATING_TOOLS = {
    "pm_create",
    "pm_update",
    "pm_delete",
    "pm_sync_as_with_peeringdb",
    "pm_poll_internet_exchange_sessions",
}


def _config(pm_readonly: bool = False, pm_url: str | None = "https://pm.example.com") -> Config:
    return Config(
        peeringdb=PeeringDBConfig(
            base_url="https://www.peeringdb.com/api",
            api_key=None,
            username=None,
            password=None,
            cache_ttl=60,
            cache_size=16,
            rate_limit=0,
        ),
        peering_manager=PeeringManagerConfig(
            base_url=pm_url,
            token="abc123" if pm_url else None,
            verify_ssl=True,
            readonly=pm_readonly,
        ),
        transport=TransportConfig(
            transport="stdio", host="127.0.0.1", port=8000, path=None, auth_token=None
        ),
        http_timeout=5.0,
    )


async def _tool_names(config: Config) -> set[str]:
    return {tool.name for tool in await build_server(config).list_tools()}


@pytest.mark.asyncio
async def test_default_registers_mutating_tools():
    tools = await _tool_names(_config())
    assert tools >= MUTATING_TOOLS
    assert {"pm_list", "pm_get", "pm_status", "peeringdb_search"} <= tools


@pytest.mark.asyncio
async def test_readonly_omits_mutating_tools():
    tools = await _tool_names(_config(pm_readonly=True))
    assert not (tools & MUTATING_TOOLS)
    assert {"pm_list", "pm_get", "pm_status", "pm_endpoints"} <= tools


@pytest.mark.asyncio
async def test_unconfigured_pm_still_lists_read_tools():
    tools = await _tool_names(_config(pm_url=None))
    assert tools >= MUTATING_TOOLS  # gating is by flag, not by configuration
    assert "pm_status" in tools


@pytest.mark.asyncio
async def test_tool_call_reuses_cached_client(respx_mock):
    """Two identical tool calls must hit PeeringDB once (shared client + TTL cache)."""
    route = respx_mock.get(url__regex=r"https://www\.peeringdb\.com/api/net").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 1, "asn": 15169}], "meta": {}})
    )
    mcp = build_server(_config())
    await mcp.call_tool("peeringdb_search", {"resource": "net", "filters": {"asn": 15169}})
    await mcp.call_tool("peeringdb_search", {"resource": "net", "filters": {"asn": 15169}})
    assert route.call_count == 1
