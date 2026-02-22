"""Tests for app/services/build_cycle_service.py.

Covers the dossier → branch → seal lifecycle orchestration:
  - start_build_cycle: conflict guard, dossier locking, branch name stamping
  - finish_build_cycle: seal delegation
  - abandon_build_cycle: abandon delegation
  - get_current_cycle / get_cycle_detail / list_project_cycles: thin repo wrappers
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

_PROJECT_ID = uuid.uuid4()
_REPO_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_CYCLE_ID = uuid.uuid4()
_DOSSIER_ID = uuid.uuid4()
_SEAL_ID = uuid.uuid4()


def _mock_cycle(cycle_id=None) -> dict:
    cid = cycle_id or _CYCLE_ID
    return {
        "id": cid,
        "project_id": _PROJECT_ID,
        "repo_id": _REPO_ID,
        "user_id": _USER_ID,
        "status": "active",
        "branch_name": None,
    }


# ---------------------------------------------------------------------------
# start_build_cycle
# ---------------------------------------------------------------------------

class TestStartBuildCycle:
    @pytest.mark.asyncio
    async def test_creates_cycle_and_sets_branch_name(self):
        from app.services.build_cycle_service import start_build_cycle

        cycle = _mock_cycle()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None), \
             patch("app.services.build_cycle_service.create_build_cycle", new_callable=AsyncMock, return_value=cycle), \
             patch("app.repos.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):

            result = await start_build_cycle(_PROJECT_ID, _REPO_ID, _USER_ID)

        assert result["branch_name"] == f"forge/build-{_CYCLE_ID}"
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_if_active_cycle_exists(self):
        from app.services.build_cycle_service import start_build_cycle

        existing = _mock_cycle()

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=existing):
            with pytest.raises(ValueError, match="already has an active build cycle"):
                await start_build_cycle(_PROJECT_ID, _REPO_ID, _USER_ID)

    @pytest.mark.asyncio
    async def test_locks_dossier_when_provided(self):
        from app.services.build_cycle_service import start_build_cycle

        cycle = _mock_cycle()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        mock_lock = AsyncMock()
        mock_link = AsyncMock()

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None), \
             patch("app.services.build_cycle_service.create_build_cycle", new_callable=AsyncMock, return_value=cycle), \
             patch("app.services.build_cycle_service.is_dossier_locked", new_callable=AsyncMock, return_value=False), \
             patch("app.services.build_cycle_service.lock_dossier", mock_lock), \
             patch("app.services.build_cycle_service.link_dossier_to_cycle", mock_link), \
             patch("app.repos.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):

            await start_build_cycle(_PROJECT_ID, _REPO_ID, _USER_ID, dossier_run_id=_DOSSIER_ID)

        mock_lock.assert_awaited_once_with(_DOSSIER_ID)
        mock_link.assert_awaited_once_with(_DOSSIER_ID, _CYCLE_ID)

    @pytest.mark.asyncio
    async def test_skips_lock_if_already_locked(self):
        from app.services.build_cycle_service import start_build_cycle

        cycle = _mock_cycle()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        mock_lock = AsyncMock()

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None), \
             patch("app.services.build_cycle_service.create_build_cycle", new_callable=AsyncMock, return_value=cycle), \
             patch("app.services.build_cycle_service.is_dossier_locked", new_callable=AsyncMock, return_value=True), \
             patch("app.services.build_cycle_service.lock_dossier", mock_lock), \
             patch("app.services.build_cycle_service.link_dossier_to_cycle", AsyncMock()), \
             patch("app.repos.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):

            await start_build_cycle(_PROJECT_ID, _REPO_ID, _USER_ID, dossier_run_id=_DOSSIER_ID)

        mock_lock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dossier_skips_lock(self):
        from app.services.build_cycle_service import start_build_cycle

        cycle = _mock_cycle()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        mock_is_locked = AsyncMock()

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None), \
             patch("app.services.build_cycle_service.create_build_cycle", new_callable=AsyncMock, return_value=cycle), \
             patch("app.services.build_cycle_service.is_dossier_locked", mock_is_locked), \
             patch("app.repos.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):

            await start_build_cycle(_PROJECT_ID, _REPO_ID, _USER_ID)

        mock_is_locked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_baseline_sha_to_repo(self):
        from app.services.build_cycle_service import start_build_cycle

        cycle = _mock_cycle()
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        mock_create = AsyncMock(return_value=cycle)

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None), \
             patch("app.services.build_cycle_service.create_build_cycle", mock_create), \
             patch("app.repos.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):

            await start_build_cycle(_PROJECT_ID, _REPO_ID, _USER_ID, baseline_sha="abc123")

        mock_create.assert_awaited_once()
        _, kwargs = mock_create.call_args
        assert kwargs["baseline_sha"] == "abc123"


# ---------------------------------------------------------------------------
# finish_build_cycle
# ---------------------------------------------------------------------------

class TestFinishBuildCycle:
    @pytest.mark.asyncio
    async def test_seals_cycle(self):
        from app.services.build_cycle_service import finish_build_cycle

        sealed = {**_mock_cycle(), "status": "sealed", "seal_id": str(_SEAL_ID)}
        mock_seal = AsyncMock(return_value=sealed)

        with patch("app.services.build_cycle_service.seal_cycle", mock_seal):
            result = await finish_build_cycle(_CYCLE_ID, _SEAL_ID)

        mock_seal.assert_awaited_once_with(_CYCLE_ID, _SEAL_ID)
        assert result["status"] == "sealed"

    @pytest.mark.asyncio
    async def test_propagates_value_error_from_repo(self):
        from app.services.build_cycle_service import finish_build_cycle

        with patch("app.services.build_cycle_service.seal_cycle", new_callable=AsyncMock, side_effect=ValueError("not active")):
            with pytest.raises(ValueError, match="not active"):
                await finish_build_cycle(_CYCLE_ID, _SEAL_ID)


# ---------------------------------------------------------------------------
# abandon_build_cycle
# ---------------------------------------------------------------------------

class TestAbandonBuildCycle:
    @pytest.mark.asyncio
    async def test_abandons_cycle(self):
        from app.services.build_cycle_service import abandon_build_cycle

        abandoned = {**_mock_cycle(), "status": "abandoned"}
        mock_abandon = AsyncMock(return_value=abandoned)

        with patch("app.services.build_cycle_service.abandon_cycle", mock_abandon):
            result = await abandon_build_cycle(_CYCLE_ID)

        mock_abandon.assert_awaited_once_with(_CYCLE_ID)
        assert result["status"] == "abandoned"

    @pytest.mark.asyncio
    async def test_propagates_value_error_from_repo(self):
        from app.services.build_cycle_service import abandon_build_cycle

        with patch("app.services.build_cycle_service.abandon_cycle", new_callable=AsyncMock, side_effect=ValueError("not active")):
            with pytest.raises(ValueError):
                await abandon_build_cycle(_CYCLE_ID)


# ---------------------------------------------------------------------------
# get_current_cycle
# ---------------------------------------------------------------------------

class TestGetCurrentCycle:
    @pytest.mark.asyncio
    async def test_returns_active_cycle(self):
        from app.services.build_cycle_service import get_current_cycle

        cycle = _mock_cycle()
        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=cycle):
            result = await get_current_cycle(_PROJECT_ID)

        assert result["id"] == _CYCLE_ID

    @pytest.mark.asyncio
    async def test_returns_none_when_no_active_cycle(self):
        from app.services.build_cycle_service import get_current_cycle

        with patch("app.services.build_cycle_service.get_active_cycle", new_callable=AsyncMock, return_value=None):
            result = await get_current_cycle(_PROJECT_ID)

        assert result is None


# ---------------------------------------------------------------------------
# get_cycle_detail
# ---------------------------------------------------------------------------

class TestGetCycleDetail:
    @pytest.mark.asyncio
    async def test_returns_cycle_by_id(self):
        from app.services.build_cycle_service import get_cycle_detail

        cycle = _mock_cycle()
        with patch("app.services.build_cycle_service.get_build_cycle", new_callable=AsyncMock, return_value=cycle):
            result = await get_cycle_detail(_CYCLE_ID)

        assert result["id"] == _CYCLE_ID

    @pytest.mark.asyncio
    async def test_returns_none_if_not_found(self):
        from app.services.build_cycle_service import get_cycle_detail

        with patch("app.services.build_cycle_service.get_build_cycle", new_callable=AsyncMock, return_value=None):
            result = await get_cycle_detail(_CYCLE_ID)

        assert result is None


# ---------------------------------------------------------------------------
# list_project_cycles
# ---------------------------------------------------------------------------

class TestListProjectCycles:
    @pytest.mark.asyncio
    async def test_returns_cycle_list(self):
        from app.services.build_cycle_service import list_project_cycles

        cycles = [_mock_cycle(uuid.uuid4()) for _ in range(3)]
        with patch("app.services.build_cycle_service.get_cycles_for_project", new_callable=AsyncMock, return_value=cycles):
            result = await list_project_cycles(_PROJECT_ID)

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_passes_limit_to_repo(self):
        from app.services.build_cycle_service import list_project_cycles

        mock_get = AsyncMock(return_value=[])
        with patch("app.services.build_cycle_service.get_cycles_for_project", mock_get):
            await list_project_cycles(_PROJECT_ID, limit=5)

        mock_get.assert_awaited_once_with(_PROJECT_ID, 5)

    @pytest.mark.asyncio
    async def test_default_limit_is_20(self):
        from app.services.build_cycle_service import list_project_cycles

        mock_get = AsyncMock(return_value=[])
        with patch("app.services.build_cycle_service.get_cycles_for_project", mock_get):
            await list_project_cycles(_PROJECT_ID)

        _, kwargs = mock_get.call_args
        # Called as positional: (project_id, limit)
        args, _ = mock_get.call_args
        assert args[1] == 20
