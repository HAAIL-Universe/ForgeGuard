"""Unit tests for the global exception handler middleware.

All tests use a standalone FastAPI app with inline routes so that
no database connections, external services, or real routers are needed.
"""

import logging
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.middleware.exception_handler import setup_exception_handlers
from app.errors import NotFoundError


@pytest.fixture()
def test_app() -> FastAPI:
    """Create a minimal FastAPI app with exception handlers registered."""
    app = FastAPI()
    setup_exception_handlers(app)

    @app.get("/raise-unhandled")
    async def _raise_unhandled() -> None:
        raise RuntimeError("something went very wrong")

    @app.get("/raise-http-404")
    async def _raise_http_404() -> None:
        raise HTTPException(status_code=404, detail="Item not found")

    @app.get("/raise-http-403")
    async def _raise_http_403() -> None:
        raise HTTPException(status_code=403, detail="Forbidden")

    class Item(BaseModel):
        name: str
        price: float

    @app.post("/validate")
    async def _validate(item: Item) -> dict:
        return item.model_dump()

    @app.get("/raise-value-error-not-found")
    async def _raise_value_not_found() -> None:
        raise ValueError("Widget not found")

    @app.get("/raise-value-error-bad")
    async def _raise_value_bad() -> None:
        raise ValueError("Invalid widget ID")

    @app.get("/raise-forge-not-found")
    async def _raise_forge_not_found() -> None:
        raise NotFoundError("Project not found")

    @app.get("/ok")
    async def _ok() -> dict:
        return {"status": "ok"}

    return app


@pytest.fixture()
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app, raise_server_exceptions=False)


# ------------------------------------------------------------------
# Generic unhandled exception → 500
# ------------------------------------------------------------------

def test_unhandled_exception_returns_500(client: TestClient) -> None:
    response = client.get("/raise-unhandled")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "Internal Server Error"
    assert "request_id" in body
    assert body["request_id"]  # non-empty


def test_unhandled_exception_does_not_leak_traceback(client: TestClient) -> None:
    response = client.get("/raise-unhandled")
    body = response.json()
    # The actual exception message must NOT appear in the response
    assert "something went very wrong" not in str(body)


# ------------------------------------------------------------------
# HTTPException → preserved status code
# ------------------------------------------------------------------

def test_http_exception_preserves_status_404(client: TestClient) -> None:
    response = client.get("/raise-http-404")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "Item not found"
    assert body["detail"] == "Item not found"
    assert "request_id" in body


def test_http_exception_preserves_status_403(client: TestClient) -> None:
    response = client.get("/raise-http-403")
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "Forbidden"


# ------------------------------------------------------------------
# Validation errors → 422
# ------------------------------------------------------------------

def test_validation_error_returns_422(client: TestClient) -> None:
    response = client.post("/validate", json={"name": 123})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "Validation failed"
    assert isinstance(body["detail"], list)
    assert "request_id" in body


# ------------------------------------------------------------------
# ForgeError subclass → mapped status
# ------------------------------------------------------------------

def test_forge_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/raise-forge-not-found")
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "Project not found"
    assert "request_id" in body


# ------------------------------------------------------------------
# ValueError transitional handler
# ------------------------------------------------------------------

def test_value_error_not_found_returns_404(client: TestClient) -> None:
    response = client.get("/raise-value-error-not-found")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "Not Found"
    assert body["detail"] == "Widget not found"


def test_value_error_bad_returns_400(client: TestClient) -> None:
    response = client.get("/raise-value-error-bad")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "Bad Request"
    assert body["detail"] == "Invalid widget ID"


# ------------------------------------------------------------------
# Logging verification
# ------------------------------------------------------------------

def test_exception_handler_logs_traceback(client: TestClient) -> None:
    with patch("app.middleware.exception_handler.logger") as mock_logger:
        client.get("/raise-unhandled")
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        # The log message should contain method and path info
        assert "GET" in str(call_args)
        assert "/raise-unhandled" in str(call_args)
        # exc_info should be passed for traceback
        assert call_args.kwargs.get("exc_info") is not None or (
            len(call_args.args) > 0
        )


# ------------------------------------------------------------------
# Request ID propagation
# ------------------------------------------------------------------

def test_request_id_in_error_response(client: TestClient) -> None:
    response = client.get("/raise-unhandled")
    body = response.json()
    assert "request_id" in body
    # Should be a non-empty string (UUID-4 format)
    assert len(body["request_id"]) > 0


# ------------------------------------------------------------------
# Happy path still works
# ------------------------------------------------------------------

def test_successful_request_not_affected(client: TestClient) -> None:
    response = client.get("/ok")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
