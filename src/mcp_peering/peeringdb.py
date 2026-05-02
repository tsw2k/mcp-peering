"""Asynchronous PeeringDB REST API client.

API reference: https://www.peeringdb.com/apidocs/
Auth: API key via ``Authorization: Api-Key <key>`` or HTTP Basic.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import PeeringDBConfig

# Resources exposed by PeeringDB. Kept here for validation and discoverability.
RESOURCES: tuple[str, ...] = (
    "net",
    "ix",
    "fac",
    "org",
    "netixlan",
    "netfac",
    "ixlan",
    "ixpfx",
    "poc",
    "as_set",
    "campus",
    "carrier",
    "carrierfac",
)


class PeeringDBError(RuntimeError):
    """Raised when PeeringDB returns a non-success response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"PeeringDB error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class PeeringDBClient:
    def __init__(self, config: PeeringDBConfig, timeout: float = 30.0) -> None:
        self._config = config
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "mcp-peering/0.1",
        }
        auth: httpx.Auth | None = None
        if config.api_key:
            headers["Authorization"] = f"Api-Key {config.api_key}"
        elif config.username and config.password:
            auth = httpx.BasicAuth(config.username, config.password)

        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers=headers,
            auth=auth,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PeeringDBClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    @staticmethod
    def _validate_resource(resource: str) -> None:
        if resource not in RESOURCES:
            raise ValueError(
                f"Unknown PeeringDB resource '{resource}'. Valid: {', '.join(RESOURCES)}"
            )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PeeringDBError(0, f"network error: {exc}") from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
                message = payload.get("meta", {}).get("error") or payload.get("detail") or str(payload)
            except ValueError:
                message = response.text
            raise PeeringDBError(response.status_code, message)
        if not response.content:
            return None
        return response.json()

    async def list(
        self,
        resource: str,
        *,
        filters: dict[str, Any] | None = None,
        depth: int = 0,
        limit: int | None = None,
        skip: int | None = None,
    ) -> list[dict[str, Any]]:
        """List objects of ``resource`` with optional Django-style filters."""
        self._validate_resource(resource)
        params: dict[str, Any] = {}
        if filters:
            params.update({k: v for k, v in filters.items() if v is not None})
        if depth:
            params["depth"] = depth
        if limit is not None:
            params["limit"] = limit
        if skip is not None:
            params["skip"] = skip
        data = await self._request("GET", f"/{resource}", params=params)
        return data.get("data", []) if isinstance(data, dict) else []

    async def get(self, resource: str, object_id: int, *, depth: int = 0) -> dict[str, Any] | None:
        self._validate_resource(resource)
        params = {"depth": depth} if depth else None
        data = await self._request("GET", f"/{resource}/{object_id}", params=params)
        if not isinstance(data, dict):
            return None
        items = data.get("data") or []
        return items[0] if items else None

    async def get_network_by_asn(self, asn: int, *, depth: int = 0) -> dict[str, Any] | None:
        results = await self.list("net", filters={"asn": asn}, depth=depth, limit=1)
        return results[0] if results else None
