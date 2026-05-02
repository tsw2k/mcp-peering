from __future__ import annotations

import httpx
import pytest

from mcp_peering.config import PeeringDBConfig
from mcp_peering.peeringdb import PeeringDBClient, PeeringDBError


def _config(api_key: str | None = None) -> PeeringDBConfig:
    return PeeringDBConfig(
        base_url="https://www.peeringdb.com/api",
        api_key=api_key,
        username=None,
        password=None,
    )


@pytest.mark.asyncio
async def test_get_network_by_asn(respx_mock):
    respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": 42, "asn": 15169, "name": "Google LLC"}], "meta": {}},
        )
    )
    async with PeeringDBClient(_config()) as client:
        net = await client.get_network_by_asn(15169)
    assert net is not None
    assert net["asn"] == 15169
    assert net["name"] == "Google LLC"


@pytest.mark.asyncio
async def test_search_with_filters(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/ix").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 1, "name": "DE-CIX"}], "meta": {}})
    )
    async with PeeringDBClient(_config()) as client:
        results = await client.list("ix", filters={"name__contains": "DE-CIX"}, limit=10)
    assert results == [{"id": 1, "name": "DE-CIX"}]
    assert route.called
    assert route.calls.last.request.url.params["name__contains"] == "DE-CIX"
    assert route.calls.last.request.url.params["limit"] == "10"


@pytest.mark.asyncio
async def test_invalid_resource():
    async with PeeringDBClient(_config()) as client:
        with pytest.raises(ValueError):
            await client.list("invalid")


@pytest.mark.asyncio
async def test_error_response(respx_mock):
    respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(429, json={"meta": {"error": "rate limited"}})
    )
    async with PeeringDBClient(_config()) as client:
        with pytest.raises(PeeringDBError) as exc:
            await client.list("net")
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_api_key_header(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {}})
    )
    async with PeeringDBClient(_config(api_key="secret")) as client:
        await client.list("net")
    assert route.calls.last.request.headers["Authorization"] == "Api-Key secret"
