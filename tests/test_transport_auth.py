from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_peering.transport import BearerAuthMiddleware


def _ok(_request):
    return JSONResponse({"ok": True})


@pytest.fixture
def app():
    inner = Starlette(routes=[Route("/mcp", _ok)])
    return BearerAuthMiddleware(inner, token="secret")


def test_missing_authorization_returns_401(app):
    with TestClient(app) as client:
        response = client.get("/mcp")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_wrong_token_returns_401(app):
    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_correct_token_passes_through(app):
    with TestClient(app) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_non_http_scope_passes_through():
    captured: list[dict] = []

    async def inner(scope, receive, send):
        captured.append(scope)

    middleware = BearerAuthMiddleware(inner, token="secret")

    import asyncio

    asyncio.run(middleware({"type": "lifespan"}, None, None))
    assert captured == [{"type": "lifespan"}]
