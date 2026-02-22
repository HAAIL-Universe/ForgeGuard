"""Tests for app/services/scout/quick_scan.py.

Covers the public API and ownership guards:
  - start_scout_run: ownership validation, run creation, background task fire
  - _complete_with_no_changes: DB update + WS broadcast
  - get_scout_history: repo routing (by-repo vs by-user)
  - get_scout_detail: ownership check, not-found guard
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


_USER_ID = uuid.uuid4()
_REPO_ID = uuid.uuid4()
_RUN_ID = uuid.uuid4()


def _mock_repo(**overrides) -> dict:
    base = {
        "id": str(_REPO_ID),
        "user_id": str(_USER_ID),
        "full_name": "myuser/myrepo",
        "default_branch": "main",
    }
    return {**base, **overrides}


def _mock_run(**overrides) -> dict:
    base = {
        "id": _RUN_ID,
        "user_id": str(_USER_ID),
        "repo_id": str(_REPO_ID),
        "status": "running",
        "hypothesis": None,
        "results": None,
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# start_scout_run
# ---------------------------------------------------------------------------

class TestStartScoutRun:
    @pytest.mark.asyncio
    async def test_raises_if_repo_not_found(self):
        from app.services.scout.quick_scan import start_scout_run

        with patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="Repo not found"):
                await start_scout_run(_USER_ID, _REPO_ID)

    @pytest.mark.asyncio
    async def test_raises_if_repo_belongs_to_other_user(self):
        from app.services.scout.quick_scan import start_scout_run

        repo = _mock_repo(user_id=str(uuid.uuid4()))
        with patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock, return_value=repo):
            with pytest.raises(ValueError, match="Repo not found"):
                await start_scout_run(_USER_ID, _REPO_ID)

    @pytest.mark.asyncio
    async def test_creates_run_and_returns_running_status(self):
        from app.services.scout.quick_scan import start_scout_run

        repo = _mock_repo()
        run = _mock_run()

        with patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock, return_value=repo), \
             patch("app.services.scout.quick_scan.create_scout_run", new_callable=AsyncMock, return_value=run), \
             patch("app.services.scout.quick_scan.asyncio.create_task"):

            result = await start_scout_run(_USER_ID, _REPO_ID)

        assert result["status"] == "running"
        assert result["repo_name"] == "myuser/myrepo"
        assert "id" in result

    @pytest.mark.asyncio
    async def test_fires_background_task(self):
        from app.services.scout.quick_scan import start_scout_run

        repo = _mock_repo()
        run = _mock_run()
        mock_create_task = MagicMock()

        with patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock, return_value=repo), \
             patch("app.services.scout.quick_scan.create_scout_run", new_callable=AsyncMock, return_value=run), \
             patch("app.services.scout.quick_scan.asyncio.create_task", mock_create_task):

            await start_scout_run(_USER_ID, _REPO_ID)

        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_hypothesis_to_create_run(self):
        from app.services.scout.quick_scan import start_scout_run

        repo = _mock_repo()
        run = _mock_run(hypothesis="Check for secrets")
        mock_create = AsyncMock(return_value=run)

        with patch("app.services.scout.quick_scan.get_repo_by_id", new_callable=AsyncMock, return_value=repo), \
             patch("app.services.scout.quick_scan.create_scout_run", mock_create), \
             patch("app.services.scout.quick_scan.asyncio.create_task"):

            await start_scout_run(_USER_ID, _REPO_ID, hypothesis="Check for secrets")

        mock_create.assert_awaited_once_with(_REPO_ID, _USER_ID, "Check for secrets")


# ---------------------------------------------------------------------------
# _complete_with_no_changes
# ---------------------------------------------------------------------------

class TestCompleteWithNoChanges:
    @pytest.mark.asyncio
    async def test_updates_run_to_completed(self):
        from app.services.scout.quick_scan import _complete_with_no_changes

        mock_update = AsyncMock()
        mock_ws = AsyncMock()

        with patch("app.services.scout.quick_scan.update_scout_run", mock_update), \
             patch("app.services.scout.quick_scan.ws_manager") as mock_manager:
            mock_manager.send_to_user = mock_ws
            await _complete_with_no_changes(_RUN_ID, str(_USER_ID))

        mock_update.assert_awaited_once()
        # update_scout_run(run_id, status=..., results=...)
        call_args, call_kwargs = mock_update.call_args
        assert call_args[0] == _RUN_ID
        assert call_kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_broadcasts_scout_complete_event(self):
        from app.services.scout.quick_scan import _complete_with_no_changes

        mock_ws = AsyncMock()

        with patch("app.services.scout.quick_scan.update_scout_run", AsyncMock()), \
             patch("app.services.scout.quick_scan.ws_manager") as mock_manager:
            mock_manager.send_to_user = mock_ws
            await _complete_with_no_changes(_RUN_ID, str(_USER_ID))

        mock_ws.assert_awaited_once()
        _, event = mock_ws.call_args[0]
        assert event["type"] == "scout_complete"
        assert event["payload"]["status"] == "completed"


# ---------------------------------------------------------------------------
# get_scout_history
# ---------------------------------------------------------------------------

class TestGetScoutHistory:
    @pytest.mark.asyncio
    async def test_fetches_by_repo_when_repo_id_provided(self):
        from app.services.scout.quick_scan import get_scout_history

        runs = [_mock_run()]
        mock_by_repo = AsyncMock(return_value=runs)
        mock_by_user = AsyncMock(return_value=[])

        with patch("app.services.scout.quick_scan.get_scout_runs_by_repo", mock_by_repo), \
             patch("app.services.scout.quick_scan.get_scout_runs_by_user", mock_by_user), \
             patch("app.services.scout.quick_scan._serialize_run", side_effect=lambda r: r):

            result = await get_scout_history(_USER_ID, repo_id=_REPO_ID)

        mock_by_repo.assert_awaited_once_with(_REPO_ID, _USER_ID)
        mock_by_user.assert_not_awaited()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetches_by_user_when_no_repo_id(self):
        from app.services.scout.quick_scan import get_scout_history

        runs = [_mock_run(), _mock_run()]
        mock_by_repo = AsyncMock(return_value=[])
        mock_by_user = AsyncMock(return_value=runs)

        with patch("app.services.scout.quick_scan.get_scout_runs_by_repo", mock_by_repo), \
             patch("app.services.scout.quick_scan.get_scout_runs_by_user", mock_by_user), \
             patch("app.services.scout.quick_scan._serialize_run", side_effect=lambda r: r):

            result = await get_scout_history(_USER_ID)

        mock_by_user.assert_awaited_once_with(_USER_ID)
        mock_by_repo.assert_not_awaited()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_serializes_each_run(self):
        from app.services.scout.quick_scan import get_scout_history

        runs = [_mock_run(), _mock_run()]
        mock_serialize = MagicMock(side_effect=lambda r: {"serialized": True})

        with patch("app.services.scout.quick_scan.get_scout_runs_by_user", new_callable=AsyncMock, return_value=runs), \
             patch("app.services.scout.quick_scan._serialize_run", mock_serialize):

            result = await get_scout_history(_USER_ID)

        assert mock_serialize.call_count == 2
        assert all(r["serialized"] for r in result)


# ---------------------------------------------------------------------------
# get_scout_detail
# ---------------------------------------------------------------------------

class TestGetScoutDetail:
    @pytest.mark.asyncio
    async def test_raises_if_run_not_found(self):
        from app.services.scout.quick_scan import get_scout_detail

        with patch("app.services.scout.quick_scan.get_scout_run", new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="Scout run not found"):
                await get_scout_detail(_USER_ID, _RUN_ID)

    @pytest.mark.asyncio
    async def test_raises_if_wrong_user(self):
        from app.services.scout.quick_scan import get_scout_detail

        run = _mock_run(user_id=str(uuid.uuid4()))
        with patch("app.services.scout.quick_scan.get_scout_run", new_callable=AsyncMock, return_value=run):
            with pytest.raises(ValueError, match="Scout run not found"):
                await get_scout_detail(_USER_ID, _RUN_ID)

    @pytest.mark.asyncio
    async def test_returns_serialized_run_for_valid_user(self):
        from app.services.scout.quick_scan import get_scout_detail

        run = _mock_run()
        serialized = {"id": str(_RUN_ID), "status": "completed"}

        # get_deep_scan_progress is a local import — only triggered for deep+running scans
        # Our mock run has no scan_type, so it won't be called
        with patch("app.services.scout.quick_scan.get_scout_run", new_callable=AsyncMock, return_value=run), \
             patch("app.services.scout.quick_scan._serialize_run", return_value=serialized):

            result = await get_scout_detail(_USER_ID, _RUN_ID)

        assert result["id"] == str(_RUN_ID)
