"""Tests for the forge_ask_clarification feature (Plan 2).

Covers:
- _state.py helper functions
- build_service._handle_clarification
- build_service.resume_clarification
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

_BUILD_ID = UUID("55555555-5555-5555-5555-555555555555")
_USER_ID  = UUID("22222222-2222-2222-2222-222222222222")
_PROJ_ID  = UUID("44444444-4444-4444-4444-444444444444")


# ---------------------------------------------------------------------------
# _state.py helpers
# ---------------------------------------------------------------------------


def test_register_clarification_creates_event():
    """register_clarification returns a new asyncio.Event and stores it."""
    from app.services.build._state import (
        _clarification_events,
        register_clarification,
    )
    bid = "test-build-reg"
    _clarification_events.pop(bid, None)  # clean up any prior state

    event = register_clarification(bid)

    assert isinstance(event, asyncio.Event)
    assert not event.is_set()
    assert _clarification_events[bid] is event
    _clarification_events.pop(bid, None)  # clean up


def test_resolve_clarification_sets_event_and_stores_answer():
    """resolve_clarification stores the answer and fires the event."""
    from app.services.build._state import (
        _clarification_answers,
        _clarification_events,
        register_clarification,
        resolve_clarification,
    )
    bid = "test-build-resolve"
    register_clarification(bid)

    result = resolve_clarification(bid, "JWT tokens")

    assert result is True
    assert _clarification_answers[bid] == "JWT tokens"
    assert _clarification_events[bid].is_set()

    # clean up
    _clarification_events.pop(bid, None)
    _clarification_answers.pop(bid, None)


def test_resolve_clarification_returns_false_when_no_pending():
    """resolve_clarification returns False when no event is registered."""
    from app.services.build._state import resolve_clarification

    result = resolve_clarification("no-such-build", "answer")

    assert result is False


def test_pop_clarification_answer_clears_state():
    """pop_clarification_answer consumes the answer and clears event state."""
    from app.services.build._state import (
        _clarification_answers,
        _clarification_events,
        pop_clarification_answer,
        register_clarification,
        resolve_clarification,
    )
    bid = "test-build-pop"
    register_clarification(bid)
    resolve_clarification(bid, "Server-side sessions")

    answer = pop_clarification_answer(bid)

    assert answer == "Server-side sessions"
    assert bid not in _clarification_events
    assert bid not in _clarification_answers


def test_increment_clarification_count_returns_correct_counts():
    """increment_clarification_count increments monotonically from 0."""
    from app.services.build._state import (
        _clarification_counts,
        increment_clarification_count,
    )
    bid = "test-build-count"
    _clarification_counts.pop(bid, None)

    assert increment_clarification_count(bid) == 1
    assert increment_clarification_count(bid) == 2
    assert increment_clarification_count(bid) == 3

    _clarification_counts.pop(bid, None)


# ---------------------------------------------------------------------------
# build_service._handle_clarification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_clarification_returns_answer():
    """_handle_clarification waits for the event and returns the answer."""
    event = asyncio.Event()
    event.set()  # pre-set so asyncio.wait_for returns immediately

    with (
        patch("app.services.build._state.increment_clarification_count", return_value=1),
        patch("app.services.build._state.register_clarification", return_value=event),
        patch("app.services.build._state.pop_clarification_answer", return_value="JWT"),
        patch("app.services.build_service.build_repo") as mock_build_repo,
        patch("app.services.build_service._broadcast_build_event", new_callable=AsyncMock),
        patch("app.services.build_service.settings") as mock_settings,
    ):
        mock_settings.MAX_CLARIFICATIONS_PER_BUILD = 10
        mock_settings.CLARIFICATION_TIMEOUT_MINUTES = 10
        mock_build_repo.append_build_log = AsyncMock()

        from app.services.build_service import _handle_clarification

        result = await _handle_clarification(
            _BUILD_ID,
            _USER_ID,
            {"question": "JWT or sessions?"},
        )

    assert result == "JWT"


@pytest.mark.asyncio
async def test_handle_clarification_returns_fallback_on_timeout():
    """_handle_clarification returns a fallback message when the wait times out."""
    event = asyncio.Event()  # never set

    with (
        patch("app.services.build._state.increment_clarification_count", return_value=1),
        patch("app.services.build._state.register_clarification", return_value=event),
        patch("app.services.build._state.pop_clarification_answer", return_value=None),
        patch("app.services.build_service.build_repo") as mock_build_repo,
        patch("app.services.build_service._broadcast_build_event", new_callable=AsyncMock),
        patch("app.services.build_service.settings") as mock_settings,
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_settings.MAX_CLARIFICATIONS_PER_BUILD = 10
        mock_settings.CLARIFICATION_TIMEOUT_MINUTES = 10
        mock_build_repo.append_build_log = AsyncMock()

        from app.services.build_service import _handle_clarification

        result = await _handle_clarification(
            _BUILD_ID,
            _USER_ID,
            {"question": "JWT or sessions?"},
        )

    assert "timeout" in result.lower() or "best decision" in result.lower()


@pytest.mark.asyncio
async def test_handle_clarification_returns_limit_message():
    """_handle_clarification short-circuits when the per-build limit is exceeded."""
    with (
        patch("app.services.build._state.increment_clarification_count", return_value=11),
        patch("app.services.build_service.settings") as mock_settings,
    ):
        mock_settings.MAX_CLARIFICATIONS_PER_BUILD = 10

        from app.services.build_service import _handle_clarification

        result = await _handle_clarification(
            _BUILD_ID,
            _USER_ID,
            {"question": "Yet another question?"},
        )

    assert "limit" in result.lower() or "maximum" in result.lower()


# ---------------------------------------------------------------------------
# build_service.resume_clarification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_clarification_happy_path():
    """resume_clarification resolves a pending clarification and returns ok."""
    build_row = {"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}

    with (
        patch(
            "app.services.build_service.build_repo.get_latest_build_for_project",
            new_callable=AsyncMock,
            return_value=build_row,
        ),
        patch(
            "app.services.build._state.resolve_clarification",
            return_value=True,
        ),
    ):
        from app.services.build_service import resume_clarification

        result = await resume_clarification(
            _PROJ_ID, _USER_ID, "q-id-123", "JWT"
        )

    assert result["ok"] is True
    assert result["build_id"] == str(_BUILD_ID)


@pytest.mark.asyncio
async def test_resume_clarification_no_active_build():
    """resume_clarification raises ValueError when no build exists for the project."""
    with (
        patch(
            "app.services.build_service.build_repo.get_latest_build_for_project",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.build_service.project_repo.get_project_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        from app.services.build_service import resume_clarification

        with pytest.raises(ValueError, match="No active build"):
            await resume_clarification(_PROJ_ID, _USER_ID, "q-id", "answer")


@pytest.mark.asyncio
async def test_resume_clarification_no_pending_clarification():
    """resume_clarification raises ValueError when no event is waiting."""
    build_row = {"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}

    with (
        patch(
            "app.services.build_service.build_repo.get_latest_build_for_project",
            new_callable=AsyncMock,
            return_value=build_row,
        ),
        patch(
            "app.services.build._state.resolve_clarification",
            return_value=False,
        ),
    ):
        from app.services.build_service import resume_clarification

        with pytest.raises(ValueError, match="No pending clarification"):
            await resume_clarification(_PROJ_ID, _USER_ID, "q-id", "answer")
