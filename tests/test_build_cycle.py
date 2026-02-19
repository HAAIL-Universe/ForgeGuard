"""Phase 58.4 tests — Build cycle repo and service layer."""

from unittest.mock import AsyncMock, patch, MagicMock
from uuid import UUID, uuid4

import pytest


PROJ_ID = UUID("44444444-4444-4444-4444-444444444444")
REPO_ID = UUID("33333333-3333-3333-3333-333333333333")
USER_ID = UUID("22222222-2222-2222-2222-222222222222")
DOSSIER_RUN = UUID("55555555-5555-5555-5555-555555555555")
CYCLE_ID = UUID("66666666-6666-6666-6666-666666666666")
SEAL_ID = UUID("77777777-7777-7777-7777-777777777777")


def _make_cycle(**overrides):
    base = {
        "id": CYCLE_ID,
        "project_id": PROJ_ID,
        "repo_id": REPO_ID,
        "user_id": USER_ID,
        "dossier_run_id": DOSSIER_RUN,
        "branch_name": None,
        "baseline_sha": "abc1234",
        "seal_id": None,
        "status": "active",
        "created_at": "2025-01-01T00:00:00Z",
        "sealed_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# build_cycle_repo — unit tests
# ---------------------------------------------------------------------------


class TestBuildCycleRepo:
    """Tests for build_cycle_repo CRUD functions."""

    @pytest.mark.asyncio
    async def test_create_build_cycle(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_cycle())

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import create_build_cycle

            cycle = await create_build_cycle(
                project_id=PROJ_ID,
                repo_id=REPO_ID,
                user_id=USER_ID,
                dossier_run_id=DOSSIER_RUN,
                baseline_sha="abc1234",
            )
            assert cycle["status"] == "active"
            assert cycle["project_id"] == PROJ_ID
            mock_pool.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_build_cycle(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_cycle())

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import get_build_cycle

            cycle = await get_build_cycle(CYCLE_ID)
            assert cycle is not None
            assert cycle["id"] == CYCLE_ID

    @pytest.mark.asyncio
    async def test_get_build_cycle_not_found(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import get_build_cycle

            assert await get_build_cycle(CYCLE_ID) is None

    @pytest.mark.asyncio
    async def test_get_active_cycle(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_cycle())

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import get_active_cycle

            cycle = await get_active_cycle(PROJ_ID)
            assert cycle is not None
            assert cycle["status"] == "active"

    @pytest.mark.asyncio
    async def test_seal_cycle_success(self):
        sealed = _make_cycle(status="sealed", seal_id=SEAL_ID, sealed_at="2025-01-01T12:00:00Z")
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=sealed)

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import seal_cycle

            cycle = await seal_cycle(CYCLE_ID, SEAL_ID)
            assert cycle["status"] == "sealed"
            assert cycle["seal_id"] == SEAL_ID

    @pytest.mark.asyncio
    async def test_seal_cycle_not_active_raises(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import seal_cycle

            with pytest.raises(ValueError, match="not active"):
                await seal_cycle(CYCLE_ID, SEAL_ID)

    @pytest.mark.asyncio
    async def test_abandon_cycle_success(self):
        abandoned = _make_cycle(status="abandoned")
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=abandoned)

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import abandon_cycle

            cycle = await abandon_cycle(CYCLE_ID)
            assert cycle["status"] == "abandoned"

    @pytest.mark.asyncio
    async def test_abandon_cycle_not_active_raises(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import abandon_cycle

            with pytest.raises(ValueError, match="not active"):
                await abandon_cycle(CYCLE_ID)

    @pytest.mark.asyncio
    async def test_get_cycles_for_project(self):
        rows = [_make_cycle(), _make_cycle(id=uuid4(), status="sealed")]
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=rows)

        with patch("app.repos.build_cycle_repo.get_pool", return_value=mock_pool):
            from app.repos.build_cycle_repo import get_cycles_for_project

            cycles = await get_cycles_for_project(PROJ_ID, limit=10)
            assert len(cycles) == 2


# ---------------------------------------------------------------------------
# build_cycle_service — unit tests
# ---------------------------------------------------------------------------


class TestBuildCycleService:
    """Tests for build_cycle_service orchestration."""

    @pytest.mark.asyncio
    async def test_start_build_cycle_creates_and_links(self):
        """start_build_cycle should create cycle, set branch, link dossier."""
        cycle = _make_cycle()

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None), \
             patch("app.services.build_cycle_service.is_dossier_locked", new_callable=AsyncMock, return_value=False), \
             patch("app.services.build_cycle_service.lock_dossier", new_callable=AsyncMock), \
             patch("app.services.build_cycle_service.create_build_cycle", new_callable=AsyncMock, return_value=cycle), \
             patch("app.services.build_cycle_service.link_dossier_to_cycle", new_callable=AsyncMock) as mock_link, \
             patch("app.repos.db.get_pool") as mock_get_pool:

            mock_pool = AsyncMock()
            mock_pool.execute = AsyncMock()
            mock_get_pool.return_value = mock_pool

            from app.services.build_cycle_service import start_build_cycle

            result = await start_build_cycle(
                project_id=PROJ_ID,
                repo_id=REPO_ID,
                user_id=USER_ID,
                dossier_run_id=DOSSIER_RUN,
                baseline_sha="abc1234",
            )
            assert result["branch_name"].startswith("forge/build-")
            mock_link.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_build_cycle_rejects_duplicate(self):
        """Cannot start a second cycle for the same project."""
        existing = _make_cycle()

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=existing):
            from app.services.build_cycle_service import start_build_cycle

            with pytest.raises(ValueError, match="already has an active"):
                await start_build_cycle(
                    project_id=PROJ_ID,
                    repo_id=REPO_ID,
                    user_id=USER_ID,
                )

    @pytest.mark.asyncio
    async def test_finish_build_cycle(self):
        sealed = _make_cycle(status="sealed", seal_id=SEAL_ID)
        with patch("app.services.build_cycle_service.seal_cycle", new_callable=AsyncMock, return_value=sealed):
            from app.services.build_cycle_service import finish_build_cycle

            result = await finish_build_cycle(CYCLE_ID, SEAL_ID)
            assert result["status"] == "sealed"

    @pytest.mark.asyncio
    async def test_abandon_build_cycle(self):
        abandoned = _make_cycle(status="abandoned")
        with patch("app.services.build_cycle_service.abandon_cycle", new_callable=AsyncMock, return_value=abandoned):
            from app.services.build_cycle_service import abandon_build_cycle

            result = await abandon_build_cycle(CYCLE_ID)
            assert result["status"] == "abandoned"

    @pytest.mark.asyncio
    async def test_get_current_cycle_delegates(self):
        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None):
            from app.services.build_cycle_service import get_current_cycle

            assert await get_current_cycle(PROJ_ID) is None

    @pytest.mark.asyncio
    async def test_list_project_cycles(self):
        cycles = [_make_cycle(), _make_cycle(id=uuid4())]
        with patch("app.services.build_cycle_service.get_cycles_for_project", new_callable=AsyncMock, return_value=cycles):
            from app.services.build_cycle_service import list_project_cycles

            result = await list_project_cycles(PROJ_ID)
            assert len(result) == 2
