"""Tests for the AccessLogMiddleware."""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.middleware import RequestIDMiddleware
from app.middleware.access_log import AccessLogMiddleware


@pytest.fixture()
def test_app() -> FastAPI:
    """Standalone FastAPI app (no DB) with both middleware layers."""
    app = FastAPI()

    # Middleware ordering: AccessLogMiddleware innermost (added first),
    # RequestIDMiddleware outermost (added second — runs first).
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)

    @app.get("/api/ok")
    async def _ok():
        return {"status": "ok"}

    @app.get("/api/fail")
    async def _fail():
        raise HTTPException(400, detail="bad input")

    @app.get("/api/server_error")
    async def _server_error():
        raise RuntimeError("boom")

    @app.get("/health")
    async def _health():
        return {"status": "healthy"}

    return app


@pytest.fixture()
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app, raise_server_exceptions=False)


class TestAccessLogMiddleware:
    """Access log middleware emits structured METRIC lines."""

    def test_successful_request_logged(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/ok")
        metric_lines = [r.message for r in caplog.records if "METRIC" in r.message]
        assert len(metric_lines) >= 1
        line = metric_lines[0]
        assert "type=http_request" in line
        assert "method=GET" in line
        assert "path=/api/ok" in line
        assert "status=200" in line
        assert "wall_ms=" in line

    def test_error_request_logged_with_detail(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/fail")
        metric_lines = [r.message for r in caplog.records if "METRIC" in r.message]
        assert len(metric_lines) >= 1
        line = metric_lines[0]
        assert "status=400" in line
        assert "error=bad input" in line

    def test_server_error_logged(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/server_error")
        metric_lines = [r.message for r in caplog.records if "METRIC" in r.message]
        assert len(metric_lines) >= 1
        line = metric_lines[0]
        assert "status=500" in line

    def test_health_check_not_logged(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/health")
        metric_lines = [r.message for r in caplog.records if "METRIC" in r.message]
        assert len(metric_lines) == 0

    def test_request_id_present(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/ok")
        metric_lines = [r.message for r in caplog.records if "METRIC" in r.message]
        assert len(metric_lines) >= 1
        assert "req_id=" in metric_lines[0]
        # Request ID should not be "-" because RequestIDMiddleware generates one
        assert "req_id=-" not in metric_lines[0]

    def test_user_dash_when_no_auth(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/ok")
        metric_lines = [r.message for r in caplog.records if "METRIC" in r.message]
        assert len(metric_lines) >= 1
        assert "user=-" in metric_lines[0]
        assert "login=-" in metric_lines[0]

    def test_log_level_info_for_success(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/ok")
        records = [r for r in caplog.records if "METRIC" in r.message]
        assert records[0].levelno == logging.INFO

    def test_log_level_warning_for_4xx(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/fail")
        records = [r for r in caplog.records if "METRIC" in r.message]
        assert records[0].levelno == logging.WARNING

    def test_log_level_error_for_5xx(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/server_error")
        records = [r for r in caplog.records if "METRIC" in r.message]
        assert records[0].levelno == logging.ERROR

    def test_wall_ms_is_numeric(self, client: TestClient, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="forgeguard.access"):
            client.get("/api/ok")
        metric_lines = [r.message for r in caplog.records if "METRIC" in r.message]
        line = metric_lines[0]
        # Extract wall_ms value and verify it's a number
        for part in line.split(" | "):
            if part.startswith("wall_ms="):
                val = part.split("=")[1]
                assert float(val) >= 0
