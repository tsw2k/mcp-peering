"""MCP server exposing PeeringDB and Peering Manager tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config, load_config
from .peering_manager import ENDPOINTS as PM_ENDPOINTS
from .peering_manager import PAGE_SIZE as PM_PAGE_SIZE
from .peering_manager import (
    PeeringManagerClient,
    PeeringManagerError,
    PeeringManagerNotConfigured,
)
from .peeringdb import RESOURCES as PDB_RESOURCES
from .peeringdb import PeeringDBClient

# Fields compared by compare_as_with_peeringdb.
_COMPARISON_FIELDS = ("name", "irr_as_set", "info_prefixes4", "info_prefixes6")


def _unset(value: Any) -> Any:
    """Treat ``None`` and empty strings as equivalent "not set" markers."""
    return None if value in (None, "") else value


def build_server(config: Config | None = None) -> FastMCP:
    cfg = config or load_config()

    # HTTP clients are created lazily on first use and shared across tool
    # calls for the lifetime of the server, so connections and TLS sessions
    # are reused instead of being re-established per call.
    pdb_client: PeeringDBClient | None = None
    pm_client: PeeringManagerClient | None = None

    async def get_pdb() -> PeeringDBClient:
        nonlocal pdb_client
        if pdb_client is None:
            pdb_client = PeeringDBClient(cfg.peeringdb, timeout=cfg.http_timeout)
        return pdb_client

    async def get_pm() -> PeeringManagerClient:
        nonlocal pm_client
        if pm_client is None:
            pm_client = PeeringManagerClient(cfg.peering_manager, timeout=cfg.http_timeout)
        return pm_client

    @asynccontextmanager
    async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if pdb_client is not None:
                await pdb_client.aclose()
            if pm_client is not None:
                await pm_client.aclose()

    mcp = FastMCP(
        name="mcp-peering",
        instructions=(
            "Tools for interrogating PeeringDB and a local Peering Manager instance. "
            "Use peeringdb_* tools for public peering data (ASNs, IXPs, facilities) and "
            "pm_* tools for objects managed in your Peering Manager (autonomous systems, "
            "BGP sessions, routers, generated configurations). Set PEERING_MANAGER_URL and "
            "PEERING_MANAGER_TOKEN to enable pm_* tools. When PM_READONLY is set, the "
            "mutating pm_* tools (create/update/delete, AS sync, session polling) are not "
            "registered at all."
        ),
        lifespan=_lifespan,
    )

    # ---------------------------------------------------------------------
    # PeeringDB tools
    # ---------------------------------------------------------------------

    @mcp.tool()
    async def peeringdb_list_resources() -> list[str]:
        """List the PeeringDB resource types that can be queried."""
        return list(PDB_RESOURCES)

    @mcp.tool()
    async def peeringdb_search(
        resource: str,
        filters: dict[str, Any] | None = None,
        depth: int = 0,
        limit: int = 25,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        """Search any PeeringDB resource with Django-style filters.

        Additional pages are fetched automatically when ``limit`` exceeds
        what a single API page returns (PeeringDB caps pages at 250 rows),
        and GET responses are served from a short-lived cache, so repeating
        an identical query is cheap.

        Args:
            resource: One of net, ix, fac, org, netixlan, netfac, ixlan, ixpfx,
                poc, as_set, campus, carrier, carrierfac.
            filters: Django-style field lookups, ANDed together. Commonly
                used: ``asn`` (net only), ``name__contains``, ``city``,
                ``country`` (ISO code like ``NL``), ``region_continent``
                (``Europe``), ``net_id``, ``ix_id``. Invalid field names or
                lookups make the API return an error — retry with fewer or
                simpler filters.
            depth: Expand related objects to N levels (0-3; larger values
                are clamped by the API).
            limit: Maximum number of rows to return (pagination is
                automatic).
            skip: Number of rows to skip before collecting results.
        """
        client = await get_pdb()
        return await client.list_all(
            resource, filters=filters, depth=depth, skip=skip, max_results=limit
        )

    @mcp.tool()
    async def peeringdb_get(resource: str, object_id: int, depth: int = 0) -> dict[str, Any] | None:
        """Fetch a single PeeringDB object by numeric id."""
        client = await get_pdb()
        return await client.get(resource, object_id, depth=depth)

    @mcp.tool()
    async def peeringdb_get_network(asn: int, depth: int = 1) -> dict[str, Any] | None:
        """Fetch the PeeringDB network record for a given ASN.

        With ``depth>=1`` related objects (netixlan, netfac, poc) are inlined.
        """
        client = await get_pdb()
        return await client.get_network_by_asn(asn, depth=depth)

    @mcp.tool()
    async def peeringdb_network_presence(asn: int, limit: int = 500) -> dict[str, Any]:
        """Return where an ASN is present: IXPs (netixlan) and facilities (netfac).

        Pages are followed automatically until ``limit`` rows per category
        have been collected.
        """
        client = await get_pdb()
        net = await client.get_network_by_asn(asn)
        if net is None:
            return {"asn": asn, "found": False}
        net_id = net["id"]
        ix_presence = await client.list_all("netixlan", filters={"net_id": net_id}, max_results=limit)
        fac_presence = await client.list_all("netfac", filters={"net_id": net_id}, max_results=limit)
        return {
            "asn": asn,
            "found": True,
            "name": net.get("name"),
            "info_type": net.get("info_type"),
            "policy_general": net.get("policy_general"),
            "ix_presence": ix_presence,
            "facility_presence": fac_presence,
        }

    @mcp.tool()
    async def peeringdb_search_ix(
        name: str | None = None,
        country: str | None = None,
        city: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Search Internet Exchanges by partial name, country code, or city."""
        filters: dict[str, Any] = {}
        if name:
            filters["name__contains"] = name
        if country:
            filters["country"] = country
        if city:
            filters["city"] = city
        client = await get_pdb()
        return await client.list_all("ix", filters=filters or None, max_results=limit)

    @mcp.tool()
    async def peeringdb_ix_members(ix_id: int, limit: int = 500) -> list[dict[str, Any]]:
        """List network members of an IX (netixlan rows for the given IX id).

        Pages are followed automatically until ``limit`` members have been
        collected, so large exchanges are handled without manual paging.
        """
        client = await get_pdb()
        return await client.list_all("netixlan", filters={"ix_id": ix_id}, max_results=limit)

    # ---------------------------------------------------------------------
    # Peering Manager tools
    # ---------------------------------------------------------------------

    @mcp.tool()
    async def pm_endpoints() -> dict[str, str]:
        """List the Peering Manager endpoint short-names that pm_* tools accept."""
        return dict(PM_ENDPOINTS)

    @mcp.tool()
    async def pm_status() -> dict[str, Any]:
        """Return Peering Manager status (version, plugins, RQ workers, etc.)."""
        client = await get_pm()
        return await client.status()

    @mcp.tool()
    async def pm_list(
        endpoint: str,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List objects from a Peering Manager endpoint.

        When ``limit`` exceeds the API page size, further pages are fetched
        automatically until ``limit`` rows are collected or the data is
        exhausted — you never see a silently truncated list.

        Args:
            endpoint: A short name from ``pm_endpoints`` (e.g.
                ``autonomous-systems``) or an explicit API path
                (``peering/autonomous-systems``).
            filters: Query parameters supported by the underlying endpoint
                (e.g. ``{"asn": 15169}``, ``{"q": "google"}``).
            limit: Maximum number of objects to return (auto-paginated).
            offset: Pagination offset of the first row to return.
        """
        client = await get_pm()
        if limit <= PM_PAGE_SIZE:
            return await client.list(endpoint, filters=filters, limit=limit, offset=offset)
        return await client.list_all(endpoint, filters=filters, offset=offset, max_results=limit)

    @mcp.tool()
    async def pm_get(endpoint: str, object_id: int) -> dict[str, Any] | None:
        """Fetch a single Peering Manager object by numeric id."""
        client = await get_pm()
        return await client.get(endpoint, object_id)

    @mcp.tool()
    async def pm_find_autonomous_system(asn: int) -> dict[str, Any] | None:
        """Look up an AS by ASN inside Peering Manager (not PeeringDB)."""
        client = await get_pm()
        data = await client.list("autonomous-systems", filters={"asn": asn}, limit=1)
        results = data.get("results") or []
        return results[0] if results else None

    @mcp.tool()
    async def pm_router_configuration(router_id: int) -> dict[str, Any]:
        """Render the configuration produced by Peering Manager for a router."""
        client = await get_pm()
        return await client.router_configuration(router_id)

    # Mutating tools (writes, syncs, polling) are only registered when the
    # server is not in read-only mode (PM_READONLY). With the flag set they
    # disappear from the tool list entirely, so a client cannot even attempt
    # them; the client additionally rejects writes as a belt-and-braces guard.
    if not cfg.peering_manager.readonly:

        @mcp.tool()
        async def pm_create(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
            """Create an object on a Peering Manager endpoint.

            Always confirm payload fields against the endpoint schema. For example,
            creating an autonomous system minimally requires ``asn`` and ``name``.
            """
            client = await get_pm()
            return await client.create(endpoint, payload)

        @mcp.tool()
        async def pm_update(
            endpoint: str,
            object_id: int,
            payload: dict[str, Any],
            partial: bool = True,
        ) -> dict[str, Any]:
            """Update a Peering Manager object (PATCH by default, PUT if partial=False)."""
            client = await get_pm()
            return await client.update(endpoint, object_id, payload, partial=partial)

        @mcp.tool()
        async def pm_delete(endpoint: str, object_id: int) -> dict[str, Any]:
            """Delete an object. Returns ``{"deleted": true}`` on success."""
            client = await get_pm()
            await client.delete(endpoint, object_id)
            return {"deleted": True, "endpoint": endpoint, "id": object_id}

        @mcp.tool()
        async def pm_sync_as_with_peeringdb(autonomous_system_id: int) -> dict[str, Any]:
            """Trigger ``sync_with_peeringdb`` on an autonomous system in Peering Manager."""
            client = await get_pm()
            result = await client.action(
                "autonomous-systems", autonomous_system_id, "sync_with_peeringdb"
            )
            return result if isinstance(result, dict) else {"result": result}

        @mcp.tool()
        async def pm_poll_internet_exchange_sessions(internet_exchange_id: int) -> dict[str, Any]:
            """Trigger BGP session polling for an Internet Exchange."""
            client = await get_pm()
            result = await client.action(
                "internet-exchanges", internet_exchange_id, "poll_peering_sessions"
            )
            return result if isinstance(result, dict) else {"result": result}

    # ---------------------------------------------------------------------
    # Cross-tool helpers
    # ---------------------------------------------------------------------

    @mcp.tool()
    async def compare_as_with_peeringdb(asn: int) -> dict[str, Any]:
        """Compare AS info between Peering Manager and PeeringDB.

        Returns the ``peeringdb`` and ``peering_manager`` views plus
        ``field_differences``, a mapping of each differing field to both
        values (fields present on only one side are reported too, so a
        missing ``irr_as_set`` is visible before triggering a sync).
        ``peering_manager_error`` is ``{"error": ..., "message": ...}`` when
        the Peering Manager side failed, otherwise ``None``.
        """
        pdb = await get_pdb()
        pdb_net = await pdb.get_network_by_asn(asn)

        pm_obj: dict[str, Any] | None = None
        pm_error: dict[str, str] | None = None
        try:
            client = await get_pm()
            data = await client.list("autonomous-systems", filters={"asn": asn}, limit=1)
            results = data.get("results") or []
            pm_obj = results[0] if results else None
        except PeeringManagerNotConfigured as exc:
            pm_error = {"error": "not_configured", "message": str(exc)}
        except PeeringManagerError as exc:
            pm_error = {"error": "request_failed", "message": exc.message}

        differences: dict[str, dict[str, Any]] = {}
        if pdb_net and pm_obj:
            for field in _COMPARISON_FIELDS:
                pdb_value = _unset(pdb_net.get(field))
                pm_value = _unset(pm_obj.get(field))
                if pdb_value != pm_value:
                    differences[field] = {"peeringdb": pdb_value, "peering_manager": pm_value}

        return {
            "asn": asn,
            "peeringdb": pdb_net,
            "peering_manager": pm_obj,
            "peering_manager_error": pm_error,
            "field_differences": differences,
        }

    return mcp


def run() -> None:
    """Run the MCP server over stdio (default transport for local installs)."""
    build_server().run()


if __name__ == "__main__":
    run()
