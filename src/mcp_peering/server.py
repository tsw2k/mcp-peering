"""MCP server exposing PeeringDB and Peering Manager tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config, load_config
from .peering_manager import (
    ENDPOINTS as PM_ENDPOINTS,
)
from .peering_manager import (
    PeeringManagerClient,
    PeeringManagerNotConfigured,
)
from .peeringdb import RESOURCES as PDB_RESOURCES
from .peeringdb import PeeringDBClient


def build_server(config: Config | None = None) -> FastMCP:
    cfg = config or load_config()
    mcp = FastMCP(
        name="mcp-peering",
        instructions=(
            "Tools for interrogating PeeringDB and a local Peering Manager instance. "
            "Use peeringdb_* tools for public peering data (ASNs, IXPs, facilities) and "
            "pm_* tools for objects managed in your Peering Manager (autonomous systems, "
            "BGP sessions, routers, generated configurations). Set PEERING_MANAGER_URL and "
            "PEERING_MANAGER_TOKEN to enable pm_* tools."
        ),
    )

    def pdb_client() -> PeeringDBClient:
        return PeeringDBClient(cfg.peeringdb, timeout=cfg.http_timeout)

    def pm_client() -> PeeringManagerClient:
        return PeeringManagerClient(cfg.peering_manager, timeout=cfg.http_timeout)

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

        Args:
            resource: One of net, ix, fac, org, netixlan, netfac, ixlan, ixpfx,
                poc, as_set, campus, carrier, carrierfac.
            filters: Field lookups. Examples: ``{"asn": 15169}``,
                ``{"name__contains": "Cloudflare"}``, ``{"city": "Frankfurt"}``.
            depth: Expand related objects to N levels (0-3).
            limit: Maximum number of rows to return.
            skip: Number of rows to skip (pagination).
        """
        async with pdb_client() as client:
            return await client.list(
                resource, filters=filters, depth=depth, limit=limit, skip=skip
            )

    @mcp.tool()
    async def peeringdb_get(resource: str, object_id: int, depth: int = 0) -> dict[str, Any] | None:
        """Fetch a single PeeringDB object by numeric id."""
        async with pdb_client() as client:
            return await client.get(resource, object_id, depth=depth)

    @mcp.tool()
    async def peeringdb_get_network(asn: int, depth: int = 1) -> dict[str, Any] | None:
        """Fetch the PeeringDB network record for a given ASN.

        With ``depth>=1`` related objects (netixlan, netfac, poc) are inlined.
        """
        async with pdb_client() as client:
            return await client.get_network_by_asn(asn, depth=depth)

    @mcp.tool()
    async def peeringdb_network_presence(asn: int) -> dict[str, Any]:
        """Return where an ASN is present: IXPs (netixlan) and facilities (netfac)."""
        async with pdb_client() as client:
            net = await client.get_network_by_asn(asn)
            if net is None:
                return {"asn": asn, "found": False}
            net_id = net["id"]
            ix_presence = await client.list("netixlan", filters={"net_id": net_id}, limit=500)
            fac_presence = await client.list("netfac", filters={"net_id": net_id}, limit=500)
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
        async with pdb_client() as client:
            return await client.list("ix", filters=filters, limit=limit)

    @mcp.tool()
    async def peeringdb_ix_members(ix_id: int, limit: int = 500) -> list[dict[str, Any]]:
        """List network members of an IX (netixlan rows for the given IX id)."""
        async with pdb_client() as client:
            return await client.list("netixlan", filters={"ix_id": ix_id}, limit=limit)

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
        async with pm_client() as client:
            return await client.status()

    @mcp.tool()
    async def pm_list(
        endpoint: str,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List objects from a Peering Manager endpoint.

        Args:
            endpoint: A short name from ``pm_endpoints`` (e.g.
                ``autonomous-systems``) or an explicit API path
                (``peering/autonomous-systems``).
            filters: Query parameters supported by the underlying endpoint
                (e.g. ``{"asn": 15169}``, ``{"q": "google"}``).
            limit: Page size.
            offset: Pagination offset.
        """
        async with pm_client() as client:
            return await client.list(endpoint, filters=filters, limit=limit, offset=offset)

    @mcp.tool()
    async def pm_get(endpoint: str, object_id: int) -> dict[str, Any] | None:
        """Fetch a single Peering Manager object by numeric id."""
        async with pm_client() as client:
            return await client.get(endpoint, object_id)

    @mcp.tool()
    async def pm_create(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an object on a Peering Manager endpoint.

        Always confirm payload fields against the endpoint schema. For example,
        creating an autonomous system minimally requires ``asn`` and ``name``.
        """
        async with pm_client() as client:
            return await client.create(endpoint, payload)

    @mcp.tool()
    async def pm_update(
        endpoint: str,
        object_id: int,
        payload: dict[str, Any],
        partial: bool = True,
    ) -> dict[str, Any]:
        """Update a Peering Manager object (PATCH by default, PUT if partial=False)."""
        async with pm_client() as client:
            return await client.update(endpoint, object_id, payload, partial=partial)

    @mcp.tool()
    async def pm_delete(endpoint: str, object_id: int) -> dict[str, Any]:
        """Delete an object. Returns ``{"deleted": true}`` on success."""
        async with pm_client() as client:
            await client.delete(endpoint, object_id)
            return {"deleted": True, "endpoint": endpoint, "id": object_id}

    @mcp.tool()
    async def pm_find_autonomous_system(asn: int) -> dict[str, Any] | None:
        """Look up an AS by ASN inside Peering Manager (not PeeringDB)."""
        async with pm_client() as client:
            data = await client.list("autonomous-systems", filters={"asn": asn}, limit=1)
            results = data.get("results") or []
            return results[0] if results else None

    @mcp.tool()
    async def pm_sync_as_with_peeringdb(autonomous_system_id: int) -> dict[str, Any]:
        """Trigger ``sync_with_peeringdb`` on an autonomous system in Peering Manager."""
        async with pm_client() as client:
            result = await client.action(
                "autonomous-systems", autonomous_system_id, "sync_with_peeringdb"
            )
            return result if isinstance(result, dict) else {"result": result}

    @mcp.tool()
    async def pm_router_configuration(router_id: int) -> dict[str, Any]:
        """Render the configuration produced by Peering Manager for a router."""
        async with pm_client() as client:
            return await client.router_configuration(router_id)

    @mcp.tool()
    async def pm_poll_internet_exchange_sessions(internet_exchange_id: int) -> dict[str, Any]:
        """Trigger BGP session polling for an Internet Exchange."""
        async with pm_client() as client:
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

        Returns a dict with ``peeringdb`` and ``peering_manager`` views plus a
        list of differing top-level fields. Useful before triggering a sync.
        """
        async with pdb_client() as pdb:
            pdb_net = await pdb.get_network_by_asn(asn)

        pm_obj: dict[str, Any] | None = None
        pm_error: str | None = None
        try:
            async with pm_client() as pm:
                data = await pm.list("autonomous-systems", filters={"asn": asn}, limit=1)
                results = data.get("results") or []
                pm_obj = results[0] if results else None
        except PeeringManagerNotConfigured as exc:
            pm_error = str(exc)

        diff: list[str] = []
        if pdb_net and pm_obj:
            for field in ("name", "irr_as_set", "info_prefixes4", "info_prefixes6"):
                pdb_value = pdb_net.get(field)
                pm_value = pm_obj.get(field)
                if pdb_value and pm_value and pdb_value != pm_value:
                    diff.append(field)

        return {
            "asn": asn,
            "peeringdb": pdb_net,
            "peering_manager": pm_obj,
            "peering_manager_error": pm_error,
            "differing_fields": diff,
        }

    return mcp


def run() -> None:
    """Run the MCP server over stdio (default transport for local installs)."""
    build_server().run()


if __name__ == "__main__":
    run()
