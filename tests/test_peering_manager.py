from __future__ import annotations

import httpx
import pytest

from mcp_peering.config import PeeringManagerConfig
from mcp_peering.peering_manager import (
    PeeringManagerClient,
    PeeringManagerError,
    PeeringManagerNotConfigured,
    PeeringManagerReadOnlyError,
)


def _config(readonly: bool = False) -> PeeringManagerConfig:
    return PeeringManagerConfig(
        base_url="https://pm.example.com",
        token="abc123",
        verify_ssl=True,
        readonly=readonly,
    )


def test_not_configured_raises():
    with pytest.raises(PeeringManagerNotConfigured):
        PeeringManagerClient(PeeringManagerConfig(None, None, True))


def test_endpoint_resolution():
    assert PeeringManagerClient.resolve_endpoint("autonomous-systems") == "peering/autonomous-systems"
    assert PeeringManagerClient.resolve_endpoint("/peering/routers/") == "peering/routers"
    assert PeeringManagerClient.resolve_endpoint("custom/path") == "custom/path"


@pytest.mark.asyncio
async def test_list_uses_token_and_filters(respx_mock):
    route = respx_mock.get(
        "https://pm.example.com/api/peering/autonomous-systems/"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"count": 1, "results": [{"id": 1, "asn": 15169, "name": "Google"}]},
        )
    )
    async with PeeringManagerClient(_config()) as client:
        data = await client.list("autonomous-systems", filters={"asn": 15169}, limit=10)
    assert data["results"][0]["asn"] == 15169
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Token abc123"
    assert request.url.params["asn"] == "15169"
    assert request.url.params["limit"] == "10"


@pytest.mark.asyncio
async def test_create_and_action(respx_mock):
    create = respx_mock.post(
        "https://pm.example.com/api/peering/autonomous-systems/"
    ).mock(return_value=httpx.Response(201, json={"id": 7, "asn": 64500}))
    action = respx_mock.post(
        "https://pm.example.com/api/peering/autonomous-systems/7/sync_with_peeringdb/"
    ).mock(return_value=httpx.Response(200, json={"status": "ok"}))

    async with PeeringManagerClient(_config()) as client:
        created = await client.create("autonomous-systems", {"asn": 64500, "name": "Test"})
        synced = await client.action("autonomous-systems", 7, "sync_with_peeringdb")

    assert created == {"id": 7, "asn": 64500}
    assert synced == {"status": "ok"}
    assert create.called and action.called


@pytest.mark.asyncio
async def test_error_response(respx_mock):
    respx_mock.get("https://pm.example.com/api/peering/routers/99/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    async with PeeringManagerClient(_config()) as client:
        with pytest.raises(PeeringManagerError) as exc:
            await client.get("routers", 99)
    assert exc.value.status_code == 404
    assert "Not found" in exc.value.message


@pytest.mark.asyncio
async def test_list_all_follows_pages(respx_mock):
    route = respx_mock.get("https://pm.example.com/api/peering/routers/").mock(
        side_effect=[
            httpx.Response(200, json={"count": 3, "results": [{"id": 1}, {"id": 2}]}),
            httpx.Response(200, json={"count": 3, "results": [{"id": 3}]}),
        ]
    )
    async with PeeringManagerClient(_config()) as client:
        data = await client.list_all("routers", max_results=10)
    assert [row["id"] for row in data["results"]] == [1, 2, 3]
    assert data["count"] == 3
    assert route.calls.last.request.url.params["offset"] == "2"


@pytest.mark.asyncio
async def test_list_all_respects_offset_and_cap(respx_mock):
    route = respx_mock.get("https://pm.example.com/api/peering/routers/").mock(
        side_effect=[
            httpx.Response(200, json={"count": 9, "results": [{"id": 3}, {"id": 4}, {"id": 5}]}),
            httpx.Response(200, json={"count": 9, "results": [{"id": 6}, {"id": 7}]}),
        ]
    )
    async with PeeringManagerClient(_config()) as client:
        data = await client.list_all("routers", offset=2, max_results=5)
    assert [row["id"] for row in data["results"]] == [3, 4, 5, 6, 7]
    assert data["count"] == 9
    assert route.calls.last.request.url.params["offset"] == "5"


@pytest.mark.asyncio
async def test_readonly_rejects_mutations():
    async with PeeringManagerClient(_config(readonly=True)) as client:
        with pytest.raises(PeeringManagerReadOnlyError):
            await client.create("routers", {"name": "r1"})
        with pytest.raises(PeeringManagerReadOnlyError):
            await client.update("routers", 1, {"name": "r1"})
        with pytest.raises(PeeringManagerReadOnlyError):
            await client.delete("routers", 1)
        with pytest.raises(PeeringManagerReadOnlyError):
            await client.action("autonomous-systems", 1, "sync_with_peeringdb")


@pytest.mark.asyncio
async def test_readonly_allows_reads(respx_mock):
    route = respx_mock.get("https://pm.example.com/api/peering/routers/").mock(
        return_value=httpx.Response(200, json={"count": 1, "results": [{"id": 1}]})
    )
    async with PeeringManagerClient(_config(readonly=True)) as client:
        data = await client.list_all("routers", max_results=10)
    assert data["results"] == [{"id": 1}]
    assert route.called
