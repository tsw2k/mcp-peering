"""Asynchronous PeeringDB REST API client.

API reference: https://www.peeringdb.com/apidocs/
Auth: API key via ``Authorization: Api-Key <key>`` or HTTP Basic.

GET responses are memoised in a TTL cache and outbound requests are spaced
out by a rate limiter (both configured via ``PEERINGDB_*`` environment
variables) so that agent loops do not hammer the public API. Use
:meth:`list_all` to follow pagination automatically.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .cache import TTLCache
from .config import PeeringDBConfig
from .ratelimit import AsyncRateLimiter

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

# PeeringDB never returns more than 250 rows per page.
PAGE_SIZE = 250


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
        self._cache = TTLCache(maxsize=config.cache_size, ttl=config.cache_ttl)
        self._limiter = AsyncRateLimiter(rate=config.rate_limit)

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

    @staticmethod
    def _cache_key(path: str, params: Any) -> tuple[Any, ...]:
        if not params:
            return (path,)
        # repr() keeps the key hashable even when filter values are lists
        # (e.g. {"asn__in": [1, 2]}); sorting makes it order-independent.
        return (path, tuple((k, repr(v)) for k, v in sorted(params.items())))

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        cacheable = method == "GET"
        key = self._cache_key(path, kwargs.get("params"))
        if cacheable:
            cached, hit = await self._cache.get(key)
            if hit:
                return cached
        data = await self._request_with_retry(method, path, **kwargs)
        if cacheable:
            await self._cache.set(key, data)
        return data

    async def _request_with_retry(self, method: str, path: str, **kwargs: Any) -> Any:
        response: httpx.Response | None = None
        for attempt in (1, 2):
            await self._limiter.acquire()
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise PeeringDBError(0, f"network error: {exc}") from exc
            # A single retry when the API throttles us; the rate limiter
            # should keep this from happening in the first place.
            if response.status_code == 429 and attempt == 1:
                await asyncio.sleep(self._retry_delay(response))
                continue
            break
        assert response is not None
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

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        retry_after = response.headers.get("Retry-After", "")
        try:
            return min(max(float(retry_after), 0.0), 5.0)
        except ValueError:
            return 1.0

    async def list(
        self,
        resource: str,
        *,
        filters: dict[str, Any] | None = None,
        depth: int = 0,
        limit: int | None = None,
        skip: int | None = None,
    ) -> list[dict[str, Any]]:
        """List a single page of ``resource`` with optional Django-style filters."""
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

    async def list_all(
        self,
        resource: str,
        *,
        filters: dict[str, Any] | None = None,
        depth: int = 0,
        skip: int = 0,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """Collect every row of ``resource``, following pages automatically.

        Pages are fetched with the largest page size PeeringDB allows
        (capped by ``max_results``) until a partial page comes back or
        ``max_results`` rows have been collected (no cap when ``None``).
        """
        self._validate_resource(resource)
        if max_results is not None and max_results <= 0:
            return []
        page_size = min(max_results, PAGE_SIZE) if max_results else PAGE_SIZE
        collected: list[dict[str, Any]] = []
        position = skip
        while True:
            page = await self.list(
                resource, filters=filters, depth=depth, limit=page_size, skip=position
            )
            collected.extend(page)
            if len(page) < page_size:
                break
            if max_results is not None and len(collected) >= max_results:
                break
            position += len(page)
        return collected[:max_results] if max_results is not None else collected

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
