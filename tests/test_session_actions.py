"""/session is one view; every button calls exactly one shared service method.

Actions that discard conversation state (Clear) confirm first; the read-only
Context does not. Rewind's own turn picker is its confirmation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.discord_ui.session_actions import (
    ACTION_LABELS,
    ConfirmView,
    GoalModal,
    SessionActionsView,
)


def interaction(user: int = 42):
    item = MagicMock(spec=discord.Interaction)
    item.user = SimpleNamespace(id=user)
    item.response = MagicMock()
    item.response.send_message = AsyncMock()
    item.response.defer = AsyncMock()
    item.response.send_modal = AsyncMock()
    item.response.edit_message = AsyncMock()
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    return item


def service():
    return SimpleNamespace(
        fork=AsyncMock(),
        rewind=AsyncMock(),
        compact=AsyncMock(),
        clear=AsyncMock(),
        context=AsyncMock(),
        goal=AsyncMock(),
    )


def buttons(view):
    return {c.label: c for c in view.children if isinstance(c, discord.ui.Button)}


def test_the_six_actions_are_offered_in_order():
    view = SessionActionsView(service(), user_id=42)
    assert [b.label for b in view.children] == list(ACTION_LABELS)
    assert list(ACTION_LABELS) == ["Fork", "Rewind", "Compact", "Clear", "Context", "Goal"]


@pytest.mark.parametrize("label", ["Fork", "Rewind", "Compact", "Context"])
async def test_direct_actions_call_exactly_one_service_method(label):
    svc = service()
    view = SessionActionsView(svc, user_id=42)
    event = interaction()
    await buttons(view)[label].callback(event)
    calls = {name: getattr(svc, name).await_count for name in vars(svc)}
    assert calls == {**dict.fromkeys(calls, 0), label.lower(): 1}
    getattr(svc, label.lower()).assert_awaited_once_with(event)


async def test_clear_asks_for_confirmation_and_cancel_changes_nothing():
    svc = service()
    view = SessionActionsView(svc, user_id=42)
    event = interaction()
    await buttons(view)["Clear"].callback(event)
    svc.clear.assert_not_awaited()
    kwargs = event.response.send_message.call_args.kwargs
    confirm = kwargs["view"]
    assert isinstance(confirm, ConfirmView)
    assert kwargs["ephemeral"] is True
    assert "conversation" in event.response.send_message.call_args.args[0].lower()
    await buttons(confirm)["Cancel"].callback(interaction())
    svc.clear.assert_not_awaited()


async def test_clear_runs_after_confirmation_exactly_once():
    svc = service()
    view = SessionActionsView(svc, user_id=42)
    event = interaction()
    await buttons(view)["Clear"].callback(event)
    confirm = event.response.send_message.call_args.kwargs["view"]
    go = interaction()
    await buttons(confirm)["Clear this session"].callback(go)
    await buttons(confirm)["Clear this session"].callback(go)
    svc.clear.assert_awaited_once_with(go)


async def test_goal_opens_a_modal_and_passes_the_condition():
    svc = service()
    view = SessionActionsView(svc, user_id=42)
    event = interaction()
    await buttons(view)["Goal"].callback(event)
    modal = event.response.send_modal.await_args.args[0]
    assert isinstance(modal, GoalModal)
    modal.condition._value = "all tests pass"
    await modal.on_submit(event)
    svc.goal.assert_awaited_once_with(event, "all tests pass")
    modal.condition._value = ""
    await modal.on_submit(event)
    assert svc.goal.await_args.args[1] is None


async def test_view_and_confirmation_are_personal():
    view = SessionActionsView(service(), user_id=42)
    assert await view.interaction_check(interaction(42))
    assert not await view.interaction_check(interaction(7))
    confirm = ConfirmView(AsyncMock(), user_id=42, label="Clear this session")
    assert not await confirm.interaction_check(interaction(7))
