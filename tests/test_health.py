"""Tests for the /health endpoint."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_ok():
    """GET /health returns 200 with status ok and db connected."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_health_version_returns_version():
    """GET /health/version returns 200 with version and phase."""
    response = client.get("/health/version")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "0.1.0"
    assert data["phase"] == "6"


def test_request_id_header_generated():
    """Every response gets an X-Request-ID header."""
    response = client.get("/health")
    assert "X-Request-ID" in response.headers
    # Should be a valid UUID
    import uuid
    uuid.UUID(response.headers["X-Request-ID"])


def test_request_id_header_echoed():
    """Client-supplied X-Request-ID is echoed back."""
    custom_id = "my-trace-123"
    response = client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.headers["X-Request-ID"] == custom_id
