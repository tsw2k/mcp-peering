from __future__ import annotations

import asyncio

import httpx
import pytest

from mcp_peering.config import PeeringDBConfig
from mcp_peering.peeringdb import PeeringDBClient, PeeringDBError
from mcp_peering.ratelimit import AsyncRateLimiter


def _config(
    api_key: str | None = None,
    cache_ttl: float = 0.0,
    cache_size: int = 0,
    rate_limit: float = 0.0,
) -> PeeringDBConfig:
    return PeeringDBConfig(
        base_url="https://www.peeringdb.com/api",
        api_key=api_key,
        username=None,
        password=None,
        cache_ttl=cache_ttl,
        cache_size=cache_size,
        rate_limit=rate_limit,
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
        return_value=httpx.Response(
            429, headers={"Retry-After": "0"}, json={"meta": {"error": "rate limited"}}
        )
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


@pytest.mark.asyncio
async def test_list_all_follows_pages(respx_mock):
    # max_results above the 250-row page cap forces a second request.
    route = respx_mock.get("https://www.peeringdb.com/api/netixlan").mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": i} for i in range(250)], "meta": {}}),
            httpx.Response(200, json={"data": [{"id": 250 + i} for i in range(50)], "meta": {}}),
        ]
    )
    async with PeeringDBClient(_config()) as client:
        rows = await client.list_all("netixlan", max_results=300)
    assert len(rows) == 300
    assert rows[0]["id"] == 0 and rows[-1]["id"] == 299
    # The second page was requested because the first came back full.
    assert len(route.calls) == 2
    params = route.calls.last.request.url.params
    assert params["skip"] == "250"
    assert params["limit"] == "250"


@pytest.mark.asyncio
async def test_list_all_stops_on_partial_page(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/ix").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 1}], "meta": {}})
    )
    async with PeeringDBClient(_config()) as client:
        rows = await client.list_all("ix", max_results=100)
    assert rows == [{"id": 1}]
    assert len(route.calls) == 1


@pytest.mark.asyncio
async def test_list_all_respects_skip_and_cap(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 1}, {"id": 2}, {"id": 3}], "meta": {}})
    )
    async with PeeringDBClient(_config()) as client:
        rows = await client.list_all("net", skip=5, max_results=5)
    assert [row["id"] for row in rows] == [1, 2, 3]
    params = route.calls.last.request.url.params
    assert params["skip"] == "5"
    assert params["limit"] == "5"


@pytest.mark.asyncio
async def test_list_all_zero_cap_short_circuits():
    async with PeeringDBClient(_config()) as client:
        assert await client.list_all("net", max_results=0) == []


@pytest.mark.asyncio
async def test_get_responses_are_cached(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 42}], "meta": {}})
    )
    async with PeeringDBClient(_config(cache_ttl=60, cache_size=16)) as client:
        first = await client.list("net")
        second = await client.list("net")
    assert first == second == [{"id": 42}]
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_cache_handles_list_filter_values(respx_mock):
    # A list value (e.g. asn__in) must not break the cache key hashing.
    route = respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 1}], "meta": {}})
    )
    async with PeeringDBClient(_config(cache_ttl=60, cache_size=16)) as client:
        first = await client.list("net", filters={"asn__in": [15169, 13335]})
        second = await client.list("net", filters={"asn__in": [15169, 13335]})
    assert first == second == [{"id": 1}]
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_cache_entries_expire(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 42}], "meta": {}})
    )
    async with PeeringDBClient(_config(cache_ttl=0.01, cache_size=16)) as client:
        await client.list("net")
        await asyncio.sleep(0.02)
        await client.list("net")
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_cache_disabled(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/net").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {}})
    )
    async with PeeringDBClient(_config()) as client:
        await client.list("net")
        await client.list("net")
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_retry_after_429(respx_mock):
    route = respx_mock.get("https://www.peeringdb.com/api/net").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={"meta": {"error": "slow down"}}),
            httpx.Response(200, json={"data": [{"id": 7}], "meta": {}}),
        ]
    )
    async with PeeringDBClient(_config()) as client:
        rows = await client.list("net")
    assert rows == [{"id": 7}]
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_rate_limiter_spaces_calls():
    limiter = AsyncRateLimiter(rate=25)  # one slot every 40 ms
    import time

    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    # Two waits of ~40 ms each must have happened before the last acquire.
    assert elapsed >= 0.07


@pytest.mark.asyncio
async def test_rate_limiter_disabled_is_free():
    limiter = AsyncRateLimiter(rate=0)
    await limiter.acquire()
    assert not limiter.enabled
