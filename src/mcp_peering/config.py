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

    @property
    def is_authenticated(self) -> bool:
        return bool(self.api_key or (self.username and self.password))


@dataclass(frozen=True)
class PeeringManagerConfig:
    base_url: str | None
    token: str | None
    verify_ssl: bool

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)


@dataclass(frozen=True)
class Config:
    peeringdb: PeeringDBConfig
    peering_manager: PeeringManagerConfig
    http_timeout: float


def load_config() -> Config:
    return Config(
        peeringdb=PeeringDBConfig(
            base_url=os.environ.get("PEERINGDB_URL", "https://www.peeringdb.com/api").rstrip("/"),
            api_key=os.environ.get("PEERINGDB_API_KEY") or None,
            username=os.environ.get("PEERINGDB_USERNAME") or None,
            password=os.environ.get("PEERINGDB_PASSWORD") or None,
        ),
        peering_manager=PeeringManagerConfig(
            base_url=(os.environ.get("PEERING_MANAGER_URL") or "").rstrip("/") or None,
            token=os.environ.get("PEERING_MANAGER_TOKEN") or None,
            verify_ssl=_bool(os.environ.get("PEERING_MANAGER_VERIFY_SSL"), default=True),
        ),
        http_timeout=float(os.environ.get("HTTP_TIMEOUT", "30")),
    )
