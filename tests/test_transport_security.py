from __future__ import annotations

import pytest

from mcp_peering.config import TransportConfig, parse_list
from mcp_peering.transport import _transport_security


def _transport(**overrides) -> TransportConfig:
    base = dict(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
        path=None,
        auth_token=None,
        allowed_hosts=[],
        allowed_origins=[],
    )
    base.update(overrides)
    return TransportConfig(**base)


def test_parse_list_handles_commas_and_blanks():
    assert parse_list("a, b ,c") == ["a", "b", "c"]
    assert parse_list("") == []
    assert parse_list(None) == []
    assert parse_list(" , ") == []


def test_security_disabled_without_allowlist():
    """Default must keep pre-2.x behaviour, otherwise every reverse-proxy setup breaks."""
    settings = _transport_security(_transport())
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is False


def test_security_enabled_when_hosts_configured():
    settings = _transport_security(_transport(allowed_hosts=["mcp.example.com"]))
    assert settings.enable_dns_rebinding_protection is True
    assert "mcp.example.com" in settings.allowed_hosts
    # loopback stays reachable for local health checks
    assert "127.0.0.1:*" in settings.allowed_hosts


def test_security_includes_configured_origins():
    settings = _transport_security(
        _transport(allowed_hosts=["mcp.example.com"], allowed_origins=["https://mcp.example.com"])
    )
    assert "https://mcp.example.com" in settings.allowed_origins
    assert "http://localhost:*" in settings.allowed_origins


def test_origin_alone_enables_protection():
    settings = _transport_security(_transport(allowed_origins=["https://mcp.example.com"]))
    assert settings.enable_dns_rebinding_protection is True


@pytest.mark.asyncio
async def test_default_security_lets_proxy_host_through():
    """The documented deployment (nginx forwards the public host) must not 421."""
    from starlette.testclient import TestClient

    from mcp_peering.server import build_server
    from tests.test_server import _config

    server = build_server(_config())
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=_transport_security(_transport()),
    )
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "mcp.example.com",
            },
        )
    assert response.status_code != 421, response.text


@pytest.mark.asyncio
async def test_configured_security_accepts_listed_proxy_host():
    from starlette.testclient import TestClient

    from mcp_peering.server import build_server
    from tests.test_server import _config

    server = build_server(_config())
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=_transport_security(_transport(allowed_hosts=["mcp.example.com"])),
    )
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "mcp.example.com",
            },
        )
    assert response.status_code == 200, response.text


def _initialize_status(security, host: str, origin: str | None = None) -> int:
    """POST an MCP initialize carrying ``host`` (and ``origin``); return the status code."""
    from starlette.testclient import TestClient

    from mcp_peering.server import build_server
    from tests.test_server import _config

    app = build_server(_config()).streamable_http_app(
        streamable_http_path="/mcp", transport_security=security
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": host,
    }
    if origin is not None:
        headers["Origin"] = origin
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
            headers=headers,
        )
    return response.status_code


def _protected():
    return _transport_security(
        _transport(
            allowed_hosts=["mcp.example.com"],
            allowed_origins=["https://mcp.example.com"],
        )
    )


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",  # unrelated host: the DNS-rebinding case itself
        "mcp.example.com.evil.example",  # lookalike that starts with the listed name
        "evil.example:8000",
    ],
)
def test_allowlist_rejects_unlisted_host(host):
    """With an allowlist the protection must actually reject, not only accept."""
    assert _initialize_status(_protected(), host) == 421


def test_allowlist_keeps_loopback_reachable():
    assert _initialize_status(_protected(), "127.0.0.1:8000") == 200
    assert _initialize_status(_protected(), "localhost:8000") == 200


def test_allowlist_rejects_unlisted_origin():
    assert _initialize_status(_protected(), "mcp.example.com", "https://evil.example") == 403


def test_allowlist_accepts_listed_and_loopback_origin():
    assert _initialize_status(_protected(), "mcp.example.com", "https://mcp.example.com") == 200
    assert _initialize_status(_protected(), "mcp.example.com", "http://localhost:3000") == 200
