"""Sessions is a view over durable records, shown only where Discord still lets you look.

The repository is a fake that answers newest-first; the resolver stands in for
"can this user see that thread". Every button calls exactly one action.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.session_repo import LifecycleState, SessionRecord
from claude_discord.discord_ui.session_browser import (
    SessionBrowser,
    SessionBrowserView,
    SessionEntry,
)


def record(thread_id: int, *, summary: str | None = None, folder: str = "/p", state=None):
    return SessionRecord(
        thread_id=thread_id,
        session_id=f"s-{thread_id}",
        working_dir=folder,
        model=None,
        origin="discord",
        summary=summary,
        created_at="2026-09-01 00:00:00",
        last_used_at=f"2026-09-{thread_id:02d} 00:00:00",
        lifecycle_state=(state or LifecycleState.OPEN).value,
    )


def thread(thread_id: int, name: str, *, archived: bool = False):
    return SimpleNamespace(
        id=thread_id, name=name, archived=archived, guild=SimpleNamespace(id=10), mention="<#x>"
    )


class FakeRepo:
    def __init__(self, records):
        self.records = records

    async def list_all(self, limit: int = 50, **_):
        return self.records[:limit]


def resolver_for(threads: dict[int, object]):
    async def resolve(thread_id: int):
        return threads.get(thread_id)

    return resolve


@pytest.fixture
def browser():
    records = [
        record(4, summary="newest work", folder="/home/a/api"),
        record(3, summary="closed one", folder="/home/a/web", state=LifecycleState.CLOSED),
        record(2, summary="hidden from you", folder="/home/a/secret"),
        record(1, summary="oldest", folder="/home/a/api"),
    ]
    threads = {
        4: thread(4, "API refactor"),
        3: thread(3, "Website", archived=True),
        1: thread(1, "First thread"),
    }
    return SessionBrowser(FakeRepo(records), resolver_for(threads))


class TestFind:
    async def test_lists_accessible_sessions_newest_first_including_closed(self, browser):
        entries = await browser.find(None)
        assert [e.thread_id for e in entries] == [4, 3, 1]
        assert entries[1].closed is True
        assert entries[0].closed is False

    async def test_inaccessible_sessions_are_excluded(self, browser):
        entries = await browser.find(None)
        assert 2 not in {e.thread_id for e in entries}

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("website", [3]),  # title, case-insensitive
            ("oldest", [1]),  # summary
            ("api", [4, 1]),  # folder, newest first
            ("nothing-here", []),
        ],
    )
    async def test_search_matches_title_summary_or_folder(self, browser, query, expected):
        assert [e.thread_id for e in await browser.find(query)] == expected

    async def test_result_count_is_bounded_to_the_select_limit(self):
        records = [record(i) for i in range(40, 0, -1)]
        threads = {i: thread(i, f"t{i}") for i in range(1, 41)}
        browser = SessionBrowser(FakeRepo(records), resolver_for(threads), limit=25)
        assert len(await browser.find(None)) == 25


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
    item.edit_original_response = AsyncMock()
    return item


def entries():
    return [
        SessionEntry(4, "API refactor", "/home/a/api", "open", "2026-09-04", "newest", False),
        SessionEntry(3, "Website", "/home/a/web", "closed", "2026-09-03", None, True),
    ]


def make_view():
    actions = SimpleNamespace(
        open=AsyncMock(), new_in_same_folder=AsyncMock(), close=AsyncMock(), search=AsyncMock()
    )
    view = SessionBrowserView(entries(), actions, user_id=42)
    return view, actions


def buttons(view):
    return {c.label: c for c in view.children if isinstance(c, discord.ui.Button)}


def select(view):
    return next(c for c in view.children if isinstance(c, discord.ui.Select))


class TestView:
    def test_options_are_newest_first_and_mark_closed_sessions(self):
        view, _ = make_view()
        options = select(view).options
        assert [o.value for o in options] == ["4", "3"]
        assert "closed" in (options[1].description or "").lower()
        assert set(buttons(view)) == {"Open", "New in same folder", "Close", "Search"}

    async def test_buttons_need_a_selection_first(self):
        view, actions = make_view()
        event = interaction()
        await buttons(view)["Open"].callback(event)
        actions.open.assert_not_awaited()
        assert "Choose a session" in event.response.send_message.call_args.args[0]

    async def test_open_new_and_close_each_call_exactly_one_action(self):
        view, actions = make_view()
        event = interaction()
        picker = select(view)
        picker._values = ["3"]
        await picker.callback(event)
        await buttons(view)["Open"].callback(event)
        actions.open.assert_awaited_once_with(event, 3)
        await buttons(view)["New in same folder"].callback(event)
        actions.new_in_same_folder.assert_awaited_once_with(event, "/home/a/web")
        await buttons(view)["Close"].callback(event)
        actions.close.assert_awaited_once_with(event, 3)

    async def test_search_opens_a_modal_that_reruns_the_search(self):
        view, actions = make_view()
        event = interaction()
        await buttons(view)["Search"].callback(event)
        modal = event.response.send_modal.await_args.args[0]
        modal.query._value = "web"
        await modal.on_submit(event)
        actions.search.assert_awaited_once_with(event, "web")

    async def test_view_is_personal(self):
        view, _ = make_view()
        assert await view.interaction_check(interaction(42))
        assert not await view.interaction_check(interaction(7))
