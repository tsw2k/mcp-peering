"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PeeringDBConfig:
    base_url: str
    api_key: str | None
    username: str | None
    password: str | None
    # GET responses are cached for this many seconds (0 disables caching).
    cache_ttl: float = 300.0
    # Maximum number of cached responses kept (LRU eviction).
    cache_size: int = 128
    # Outbound request cap, requests per second (0 disables the limiter).
    rate_limit: float = 2.0

    @property
    def is_authenticated(self) -> bool:
        return bool(self.api_key or (self.username and self.password))


@dataclass(frozen=True)
class PeeringManagerConfig:
    base_url: str | None
    token: str | None
    verify_ssl: bool
    # When true the server does not register mutating pm_* tools and the
    # client rejects write requests outright.
    readonly: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)


VALID_TRANSPORTS = ("stdio", "streamable-http", "sse")


@dataclass(frozen=True)
class TransportConfig:
    transport: str
    host: str
    port: int
    path: str | None
    auth_token: str | None

    def __post_init__(self) -> None:
        if self.transport not in VALID_TRANSPORTS:
            raise ValueError(
                f"Invalid transport '{self.transport}'. Use one of: {', '.join(VALID_TRANSPORTS)}"
            )

    @property
    def is_network(self) -> bool:
        return self.transport != "stdio"


@dataclass(frozen=True)
class Config:
    peeringdb: PeeringDBConfig
    peering_manager: PeeringManagerConfig
    transport: TransportConfig
    http_timeout: float


def load_config() -> Config:
    return Config(
        peeringdb=PeeringDBConfig(
            base_url=os.environ.get("PEERINGDB_URL", "https://www.peeringdb.com/api").rstrip("/"),
            api_key=os.environ.get("PEERINGDB_API_KEY") or None,
            username=os.environ.get("PEERINGDB_USERNAME") or None,
            password=os.environ.get("PEERINGDB_PASSWORD") or None,
            cache_ttl=max(0.0, float(os.environ.get("PEERINGDB_CACHE_TTL", "300"))),
            cache_size=max(0, int(os.environ.get("PEERINGDB_CACHE_SIZE", "128"))),
            rate_limit=max(0.0, float(os.environ.get("PEERINGDB_RATE_LIMIT", "2"))),
        ),
        peering_manager=PeeringManagerConfig(
            base_url=(os.environ.get("PEERING_MANAGER_URL") or "").rstrip("/") or None,
            token=os.environ.get("PEERING_MANAGER_TOKEN") or None,
            verify_ssl=_bool(os.environ.get("PEERING_MANAGER_VERIFY_SSL"), default=True),
            readonly=_bool(os.environ.get("PM_READONLY"), default=False),
        ),
        transport=TransportConfig(
            transport=os.environ.get("MCP_TRANSPORT", "stdio").strip().lower(),
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8000")),
            path=(os.environ.get("MCP_PATH") or None),
            auth_token=os.environ.get("MCP_AUTH_TOKEN") or None,
        ),
        http_timeout=float(os.environ.get("HTTP_TIMEOUT", "30")),
    )
