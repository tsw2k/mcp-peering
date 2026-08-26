"""Asynchronous Peering Manager REST API client.

Auth: ``Authorization: Token <token>``. All endpoints under ``/api/``.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import PeeringManagerConfig

# Rows requested per page when auto-paginating; Peering Manager's default
# page size, safe to request explicitly.
PAGE_SIZE = 50

# Common endpoint paths (paginated list endpoints). Path is relative to /api/.
ENDPOINTS: dict[str, str] = {
    "autonomous-systems": "peering/autonomous-systems",
    "bgp-groups": "peering/bgp-groups",
    "communities": "peering/communities",
    "configurations": "peering/configurations",
    "direct-peering-sessions": "peering/direct-peering-sessions",
    "internet-exchanges": "peering/internet-exchanges",
    "internet-exchange-peering-sessions": "peering/internet-exchange-peering-sessions",
    "routers": "peering/routers",
    "routing-policies": "peering/routing-policies",
    "email-templates": "peering/email-templates",
    "connections": "net/connections",
    "platforms": "devices/platforms",
    "device-configurations": "devices/configurations",
    "contacts": "messaging/contacts",
    "contact-roles": "messaging/contact-roles",
    "contact-assignments": "messaging/contact-assignments",
    "emails": "messaging/emails",
    "tags": "extras/tags",
    "object-changes": "extras/object-changes",
    "webhooks": "extras/webhooks",
    "jobs": "extras/jobs",
}


class PeeringManagerError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Peering Manager error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class PeeringManagerReadOnlyError(PeeringManagerError):
    """Raised when a mutating request is attempted in read-only mode."""

    def __init__(self) -> None:
        super().__init__(
            403,
            "Peering Manager client is in read-only mode (PM_READONLY); "
            "mutating requests are rejected",
        )


class PeeringManagerNotConfigured(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Peering Manager is not configured. Set PEERING_MANAGER_URL and "
            "PEERING_MANAGER_TOKEN environment variables."
        )


class PeeringManagerClient:
    def __init__(self, config: PeeringManagerConfig, timeout: float = 30.0) -> None:
        if not config.is_configured:
            raise PeeringManagerNotConfigured()
        assert config.base_url and config.token
        self._readonly = config.readonly
        self._client = httpx.AsyncClient(
            base_url=f"{config.base_url}/api",
            headers={
                "Authorization": f"Token {config.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "mcp-peering/0.1",
            },
            verify=config.verify_ssl,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PeeringManagerClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    @staticmethod
    def resolve_endpoint(name_or_path: str) -> str:
        """Translate a friendly name into an API path.

        Accepts either a known short name from :data:`ENDPOINTS` or an explicit
        path like ``peering/autonomous-systems``. Leading and trailing slashes
        are normalised.
        """
        cleaned = name_or_path.strip().strip("/")
        if cleaned in ENDPOINTS:
            return ENDPOINTS[cleaned]
        return cleaned

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PeeringManagerError(0, f"network error: {exc}") from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
                message = payload.get("detail") or str(payload)
            except ValueError:
                message = response.text
            raise PeeringManagerError(response.status_code, message)
        if not response.content:
            return None
        return response.json()

    def _ensure_writable(self) -> None:
        if self._readonly:
            raise PeeringManagerReadOnlyError()

    async def list(
        self,
        endpoint: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List a single page of objects from an endpoint."""
        path = f"/{self.resolve_endpoint(endpoint)}/"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if filters:
            params.update({k: v for k, v in filters.items() if v is not None})
        data = await self._request("GET", path, params=params)
        return data if isinstance(data, dict) else {"results": []}

    async def list_all(
        self,
        endpoint: str,
        *,
        filters: dict[str, Any] | None = None,
        offset: int = 0,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """Collect ``results`` across DRF pages until exhausted or ``max_results``.

        Returns the same shape as :meth:`list`; ``count`` is the total
        reported by the API, ``results`` the aggregated rows.
        """
        if max_results is not None and max_results <= 0:
            return {"count": 0, "results": []}
        page_size = min(max_results, PAGE_SIZE) if max_results else PAGE_SIZE
        results: list[dict[str, Any]] = []
        total: int | None = None
        position = offset
        while True:
            data = await self.list(endpoint, filters=filters, limit=page_size, offset=position)
            page = data.get("results") or []
            results.extend(page)
            if isinstance(data.get("count"), int):
                total = data["count"]
            if not page:
                break
            if total is not None and offset + len(results) >= total:
                break
            if max_results is not None and len(results) >= max_results:
                break
            position += len(page)
        if max_results is not None:
            results = results[:max_results]
        return {"count": total if total is not None else len(results), "results": results}

    async def get(self, endpoint: str, object_id: int) -> dict[str, Any] | None:
        path = f"/{self.resolve_endpoint(endpoint)}/{object_id}/"
        return await self._request("GET", path)

    async def create(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_writable()
        path = f"/{self.resolve_endpoint(endpoint)}/"
        return await self._request("POST", path, json=payload)

    async def update(
        self,
        endpoint: str,
        object_id: int,
        payload: dict[str, Any],
        *,
        partial: bool = True,
    ) -> dict[str, Any]:
        self._ensure_writable()
        path = f"/{self.resolve_endpoint(endpoint)}/{object_id}/"
        method = "PATCH" if partial else "PUT"
        return await self._request(method, path, json=payload)

    async def delete(self, endpoint: str, object_id: int) -> None:
        self._ensure_writable()
        path = f"/{self.resolve_endpoint(endpoint)}/{object_id}/"
        await self._request("DELETE", path)

    async def action(
        self,
        endpoint: str,
        object_id: int,
        action: str,
        *,
        method: str = "POST",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke a custom detail action like ``sync_with_peeringdb``."""
        if method.upper() != "GET":
            self._ensure_writable()
        action = action.strip("/")
        path = f"/{self.resolve_endpoint(endpoint)}/{object_id}/{action}/"
        return await self._request(method, path, json=payload)

    async def router_configuration(self, router_id: int) -> dict[str, Any]:
        """Render the rendered configuration for a router."""
        path = f"/peering/routers/{router_id}/configuration/"
        data = await self._request("GET", path)
        return data if isinstance(data, dict) else {"configuration": data}

    async def status(self) -> dict[str, Any]:
        data = await self._request("GET", "/status/")
        return data if isinstance(data, dict) else {}
