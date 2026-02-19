"""Tests for build_errors repo functions and error tracking."""

import hashlib
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.repos.build_repo import (
    _error_fingerprint,
    _extract_file_path,
    upsert_build_error,
    resolve_build_error,
    resolve_errors_for_phase,
    get_build_errors,
    append_build_log,
)


# ---------------------------------------------------------------------------
# _error_fingerprint
# ---------------------------------------------------------------------------

class TestErrorFingerprint:
    def test_basic(self):
        fp = _error_fingerprint("system", "error", "ImportError in module")
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_deterministic(self):
        fp1 = _error_fingerprint("system", "error", "some error")
        fp2 = _error_fingerprint("system", "error", "some error")
        assert fp1 == fp2

    def test_different_source(self):
        fp1 = _error_fingerprint("system", "error", "some error")
        fp2 = _error_fingerprint("audit", "error", "some error")
        assert fp1 != fp2

    def test_normalizes_line_numbers(self):
        fp1 = _error_fingerprint("system", "error", "Error at line 42 of file.py")
        fp2 = _error_fingerprint("system", "error", "Error at line 99 of file.py")
        assert fp1 == fp2

    def test_normalizes_hex_addresses(self):
        fp1 = _error_fingerprint("system", "error", "Object at 0xDEADBEEF")
        fp2 = _error_fingerprint("system", "error", "Object at 0x12345678")
        assert fp1 == fp2

    def test_normalizes_uuids(self):
        fp1 = _error_fingerprint("system", "error", "Build 550e8400-e29b-41d4-a716-446655440000 failed")
        fp2 = _error_fingerprint("system", "error", "Build aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee failed")
        assert fp1 == fp2

    def test_different_severity(self):
        fp1 = _error_fingerprint("system", "error", "msg")
        fp2 = _error_fingerprint("system", "fatal", "msg")
        assert fp1 != fp2


# ---------------------------------------------------------------------------
# _extract_file_path
# ---------------------------------------------------------------------------

class TestExtractFilePath:
    def test_python_traceback(self):
        msg = 'File "app/services/auth.py", line 42, in login'
        assert _extract_file_path(msg) == "app/services/auth.py"

    def test_from_module(self):
        msg = "ImportError: cannot import 'Router' from fastapi/routing.py"
        assert _extract_file_path(msg) == "fastapi/routing.py"

    def test_typescript_file(self):
        msg = "Error in src/components/App.tsx"
        assert _extract_file_path(msg) == "src/components/App.tsx"

    def test_no_file_path(self):
        msg = "Something went wrong"
        assert _extract_file_path(msg) is None

    def test_json_file(self):
        msg = 'Error in package.json at position 42'
        assert _extract_file_path(msg) == "package.json"


# ---------------------------------------------------------------------------
# upsert_build_error
# ---------------------------------------------------------------------------

class TestUpsertBuildError:
    @pytest.mark.asyncio
    async def test_insert_new_error(self):
        build_id = uuid4()
        fake_row = {
            "id": uuid4(), "build_id": build_id, "fingerprint": "abc123",
            "first_seen": datetime.now(timezone.utc), "last_seen": datetime.now(timezone.utc),
            "occurrence_count": 1, "phase": "Phase 1", "file_path": "app/main.py",
            "source": "system", "severity": "error", "message": "ImportError",
            "resolved": False, "resolved_at": None, "resolution_method": None,
            "resolution_summary": None, "created_at": datetime.now(timezone.utc),
        }

        mock_pool = AsyncMock()
        # First call (UPDATE) returns None — no duplicate
        # Second call (INSERT) returns a Record-like dict
        mock_pool.fetchrow = AsyncMock(side_effect=[None, fake_row])

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await upsert_build_error(
                build_id, "ImportError",
                source="system", severity="error",
                phase="Phase 1", file_path="app/main.py",
            )
            assert result["occurrence_count"] == 1
            assert mock_pool.fetchrow.call_count == 2  # UPDATE then INSERT

    @pytest.mark.asyncio
    async def test_increment_existing_error(self):
        build_id = uuid4()
        existing = {
            "id": uuid4(), "build_id": build_id, "fingerprint": "abc",
            "first_seen": datetime.now(timezone.utc), "last_seen": datetime.now(timezone.utc),
            "occurrence_count": 3, "phase": None, "file_path": None,
            "source": "system", "severity": "error", "message": "test",
            "resolved": False, "resolved_at": None, "resolution_method": None,
            "resolution_summary": None, "created_at": datetime.now(timezone.utc),
        }

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=existing)

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await upsert_build_error(build_id, "test", source="system")
            assert result["occurrence_count"] == 3
            # Only the UPDATE call — no INSERT needed
            assert mock_pool.fetchrow.call_count == 1


# ---------------------------------------------------------------------------
# resolve_build_error
# ---------------------------------------------------------------------------

class TestResolveBuildError:
    @pytest.mark.asyncio
    async def test_resolve(self):
        error_id = uuid4()
        resolved_row = {
            "id": error_id, "build_id": uuid4(), "fingerprint": "x",
            "first_seen": datetime.now(timezone.utc), "last_seen": datetime.now(timezone.utc),
            "occurrence_count": 1, "phase": "Phase 1", "file_path": None,
            "source": "system", "severity": "error", "message": "test",
            "resolved": True, "resolved_at": datetime.now(timezone.utc),
            "resolution_method": "auto-fix", "resolution_summary": "Fixed import",
            "created_at": datetime.now(timezone.utc),
        }

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=resolved_row)

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await resolve_build_error(error_id, "auto-fix", "Fixed import")
            assert result is not None
            assert result["resolved"] is True
            assert result["resolution_method"] == "auto-fix"

    @pytest.mark.asyncio
    async def test_resolve_not_found(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await resolve_build_error(uuid4(), "dismissed")
            assert result is None


# ---------------------------------------------------------------------------
# resolve_errors_for_phase
# ---------------------------------------------------------------------------

class TestResolveErrorsForPhase:
    @pytest.mark.asyncio
    async def test_resolves_matching(self):
        build_id = uuid4()
        resolved = [
            {"id": uuid4(), "build_id": build_id, "fingerprint": "a",
             "first_seen": datetime.now(timezone.utc), "last_seen": datetime.now(timezone.utc),
             "occurrence_count": 1, "phase": "Phase 1", "file_path": None,
             "source": "system", "severity": "error", "message": "err1",
             "resolved": True, "resolved_at": datetime.now(timezone.utc),
             "resolution_method": "phase-complete",
             "resolution_summary": "Phase 1 completed — errors cleared",
             "created_at": datetime.now(timezone.utc)},
        ]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=resolved)

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await resolve_errors_for_phase(build_id, "Phase 1")
            assert len(result) == 1
            assert result[0]["resolution_method"] == "phase-complete"

    @pytest.mark.asyncio
    async def test_custom_summary(self):
        build_id = uuid4()
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await resolve_errors_for_phase(
                build_id, "Phase 2", summary="All tests pass",
            )
            assert result == []
            # Verify custom summary was passed
            call_args = mock_pool.fetch.call_args
            assert "All tests pass" in call_args[0][0] or call_args[0][-1] == "All tests pass"


# ---------------------------------------------------------------------------
# get_build_errors
# ---------------------------------------------------------------------------

class TestGetBuildErrors:
    @pytest.mark.asyncio
    async def test_fetch_all(self):
        build_id = uuid4()
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await get_build_errors(build_id)
            assert result == []
            # Verify no resolved filter in query
            query = mock_pool.fetch.call_args[0][0]
            assert "resolved = " not in query

    @pytest.mark.asyncio
    async def test_filter_unresolved(self):
        build_id = uuid4()
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool):
            result = await get_build_errors(build_id, resolved_filter=False)
            assert result == []
            query = mock_pool.fetch.call_args[0][0]
            assert "resolved = " in query


# ---------------------------------------------------------------------------
# append_build_log auto-tracks errors
# ---------------------------------------------------------------------------

class TestAppendBuildLogErrorTracking:
    @pytest.mark.asyncio
    async def test_info_level_no_error_tracking(self):
        build_id = uuid4()
        mock_pool = AsyncMock()
        log_row = MagicMock()
        log_row.__iter__ = MagicMock(return_value=iter([("id", uuid4())]))
        mock_pool.fetchrow = AsyncMock(return_value=log_row)

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool), \
             patch("app.repos.build_repo.upsert_build_error") as mock_upsert:
            await append_build_log(build_id, "Build started", level="info")
            mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_level_triggers_tracking(self):
        build_id = uuid4()
        mock_pool = AsyncMock()
        log_row = MagicMock()
        log_row.__iter__ = MagicMock(return_value=iter([("id", uuid4())]))
        mock_pool.fetchrow = AsyncMock(return_value=log_row)

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool), \
             patch("app.repos.build_repo.upsert_build_error", new_callable=AsyncMock) as mock_upsert:
            await append_build_log(
                build_id, "ImportError: no module named foo",
                source="system", level="error",
            )
            mock_upsert.assert_awaited_once()
            call_kwargs = mock_upsert.call_args
            assert call_kwargs[0][0] == build_id
            assert "ImportError" in call_kwargs[0][1]

    @pytest.mark.asyncio
    async def test_error_tracking_failure_doesnt_break_logging(self):
        """If upsert_build_error raises, append_build_log should still succeed."""
        build_id = uuid4()
        mock_pool = AsyncMock()
        log_row = {"id": uuid4(), "build_id": build_id, "timestamp": datetime.now(timezone.utc),
                    "source": "system", "level": "error", "message": "test",
                    "created_at": datetime.now(timezone.utc)}
        mock_pool.fetchrow = AsyncMock(return_value=MagicMock(**log_row))

        with patch("app.repos.build_repo.get_pool", return_value=mock_pool), \
             patch("app.repos.build_repo.upsert_build_error",
                   new_callable=AsyncMock,
                   side_effect=Exception("DB connection failed")) as mock_upsert:
            # Should NOT raise
            result = await append_build_log(
                build_id, "some error", source="system", level="error",
            )
            assert result is not None
            mock_upsert.assert_awaited_once()


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestBuildErrorsEndpoints:
    @pytest.mark.asyncio
    async def test_get_errors_endpoint_exists(self):
        """Verify the errors endpoint is registered."""
        from app.api.routers.builds import router
        paths = [r.path for r in router.routes]
        assert "/projects/{project_id}/build/errors" in paths

    @pytest.mark.asyncio
    async def test_dismiss_endpoint_exists(self):
        """Verify the dismiss endpoint is registered."""
        from app.api.routers.builds import router
        paths = [r.path for r in router.routes]
        assert "/projects/{project_id}/build/errors/dismiss" in paths
