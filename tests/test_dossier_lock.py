"""Phase 58.1 tests — Dossier lock functions and lock-status API endpoint."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from tests.conftest import USER_ID, auth_header, REPO_ID


# ---------------------------------------------------------------------------
# scout_repo lock helpers — unit tests
# ---------------------------------------------------------------------------


class TestLockDossier:
    """Tests for ``lock_dossier`` repo function."""

    @pytest.mark.asyncio
    async def test_lock_dossier_success(self):
        """Locking an unlocked run succeeds and sets timestamp."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value={"dossier_locked_at": "2025-01-01T00:00:00Z"})

        with patch("app.repos.scout_repo.get_pool", return_value=mock_pool):
            from app.repos.scout_repo import lock_dossier

            result = await lock_dossier(UUID("11111111-1111-1111-1111-111111111111"))
            assert result["dossier_locked_at"] is not None
            mock_pool.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_dossier_already_locked_raises(self):
        """Attempting to lock an already-locked dossier raises ValueError."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)  # no rows updated

        with patch("app.repos.scout_repo.get_pool", return_value=mock_pool):
            from app.repos.scout_repo import lock_dossier

            with pytest.raises(ValueError, match="already locked|not found"):
                await lock_dossier(UUID("11111111-1111-1111-1111-111111111111"))


class TestIsDossierLocked:
    """Tests for ``is_dossier_locked`` repo function."""

    @pytest.mark.asyncio
    async def test_locked_returns_true(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value="2025-01-01T00:00:00Z")

        with patch("app.repos.scout_repo.get_pool", return_value=mock_pool):
            from app.repos.scout_repo import is_dossier_locked

            assert await is_dossier_locked(UUID("11111111-1111-1111-1111-111111111111")) is True

    @pytest.mark.asyncio
    async def test_unlocked_returns_false(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)

        with patch("app.repos.scout_repo.get_pool", return_value=mock_pool):
            from app.repos.scout_repo import is_dossier_locked

            assert await is_dossier_locked(UUID("11111111-1111-1111-1111-111111111111")) is False


class TestLinkDossierToCycle:
    """Tests for ``link_dossier_to_cycle`` repo function."""

    @pytest.mark.asyncio
    async def test_link_success(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        with patch("app.repos.scout_repo.get_pool", return_value=mock_pool):
            from app.repos.scout_repo import link_dossier_to_cycle

            await link_dossier_to_cycle(
                UUID("11111111-1111-1111-1111-111111111111"),
                UUID("22222222-2222-2222-2222-222222222222"),
            )
            mock_pool.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# API endpoint test — GET /scout/runs/{run_id}/dossier/locked
# ---------------------------------------------------------------------------


class TestDossierLockEndpoint:
    """Test the lock-status endpoint."""

    def test_dossier_locked_true(self, test_client):
        run_id = "11111111-1111-1111-1111-111111111111"
        from tests.conftest import MOCK_USER
        with patch(
            "app.api.routers.scout._is_locked",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.deps.get_user_by_id",
            new_callable=AsyncMock,
            return_value=MOCK_USER,
        ):
            res = test_client.get(
                f"/scout/runs/{run_id}/dossier/locked",
                headers=auth_header(),
            )
            assert res.status_code == 200
            body = res.json()
            assert body["locked"] is True
            assert body["run_id"] == run_id

    def test_dossier_locked_false(self, test_client):
        run_id = "11111111-1111-1111-1111-111111111111"
        from tests.conftest import MOCK_USER
        with patch(
            "app.api.routers.scout._is_locked",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "app.api.deps.get_user_by_id",
            new_callable=AsyncMock,
            return_value=MOCK_USER,
        ):
            res = test_client.get(
                f"/scout/runs/{run_id}/dossier/locked",
                headers=auth_header(),
            )
            assert res.status_code == 200
            body = res.json()
            assert body["locked"] is False

    def test_dossier_lock_requires_auth(self, test_client):
        """Endpoint requires authentication."""
        run_id = "11111111-1111-1111-1111-111111111111"
        res = test_client.get(f"/scout/runs/{run_id}/dossier/locked")
        assert res.status_code in (401, 403)
