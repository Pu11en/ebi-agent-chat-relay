"""Sessions: find a session by title, summary or folder, then open, close, or start beside it.

The list is a view over durable session records, not over Discord history:
an archived or uncached thread is still a session, and a record is what
remembers the folder and conversation id that make it resumable. Discord only
decides visibility — each record is resolved to a thread the viewer can see
before it is shown, so a session that belongs to another computer's channels
or that this user may not read never appears.

The view holds no logic of its own. Open, New in same folder and Close each
call exactly one method on the :class:`SessionActions` the caller supplies,
which is how a button and a slash command stay the same operation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Protocol

import discord

from claude_code_core.session_repo import SessionRecord

#: Discord's select-menu ceiling; also how many records are shown at most.
SELECT_LIMIT = 25
#: How many records are examined (newest first) to fill the list.
SCAN_LIMIT = 60

ThreadResolver = Callable[[int], Awaitable[Any | None]]


@dataclass(frozen=True, slots=True)
class SessionEntry:
    """One session the viewer may see, with what the list needs to say about it."""

    thread_id: int
    title: str
    folder: str | None
    state: str
    last_used_at: str
    summary: str | None
    closed: bool

    def matches(self, query: str) -> bool:
        needle = query.casefold()
        haystacks = (self.title, self.summary or "", self.folder or "")
        return any(needle in text.casefold() for text in haystacks)

    @property
    def folder_name(self) -> str:
        return PurePath(self.folder).name if self.folder else "no folder"

    def option(self) -> discord.SelectOption:
        marker = "closed" if self.closed else "open"
        return discord.SelectOption(
            label=self.title[:100] or str(self.thread_id),
            description=f"{marker} · {self.folder_name} · {self.last_used_at[:16]}"[:100],
            value=str(self.thread_id),
        )


class SessionActions(Protocol):
    """What the Sessions view can do; each is one shared operation."""

    async def open(self, interaction: discord.Interaction, thread_id: int) -> None: ...

    async def new_in_same_folder(self, interaction: discord.Interaction, folder: str) -> None: ...

    async def close(self, interaction: discord.Interaction, thread_id: int) -> None: ...

    async def search(self, interaction: discord.Interaction, query: str) -> None: ...


class SessionBrowser:
    """Newest-first, searchable, visibility-filtered session entries."""

    def __init__(
        self,
        repo: Any,
        resolver: ThreadResolver,
        *,
        limit: int = SELECT_LIMIT,
        scan: int = SCAN_LIMIT,
    ) -> None:
        self.repo = repo
        self.resolver = resolver
        self.limit = limit
        self.scan = scan

    async def find(self, query: str | None) -> list[SessionEntry]:
        """The newest accessible sessions, narrowed by ``query`` when given."""
        needle = (query or "").strip()
        records: Sequence[SessionRecord] = await self.repo.list_all(limit=self.scan)
        found: list[SessionEntry] = []
        for record in records:
            thread = await self.resolver(record.thread_id)
            if thread is None:
                continue
            entry = _entry(record, thread)
            if needle and not entry.matches(needle):
                continue
            found.append(entry)
            if len(found) >= self.limit:
                break
        return found


def _entry(record: SessionRecord, thread: Any) -> SessionEntry:
    name = getattr(thread, "name", None)
    return SessionEntry(
        thread_id=record.thread_id,
        title=str(name) if name else f"Thread {record.thread_id}",
        folder=record.working_dir,
        state=record.lifecycle_state,
        last_used_at=record.last_used_at,
        summary=record.summary,
        closed=record.is_closed,
    )


class SearchModal(discord.ui.Modal, title="Search sessions"):
    query = discord.ui.TextInput(label="Word from a title, summary or folder", max_length=100)

    def __init__(self, actions: SessionActions) -> None:
        super().__init__()
        self.actions = actions

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.actions.search(interaction, str(self.query).strip())


class SessionBrowserView(discord.ui.View):
    """One select over the entries plus Open / New in same folder / Close / Search."""

    def __init__(
        self, entries: Sequence[SessionEntry], actions: SessionActions, *, user_id: int
    ) -> None:
        super().__init__(timeout=300)
        self.entries = {entry.thread_id: entry for entry in entries[:SELECT_LIMIT]}
        self.actions = actions
        self.user_id = user_id
        self.selected: SessionEntry | None = None

        if self.entries:
            picker = discord.ui.Select(
                placeholder="Choose a session (newest first)",
                options=[entry.option() for entry in self.entries.values()],
                row=0,
            )

            async def pick(interaction: discord.Interaction) -> None:
                self.selected = self.entries.get(int(picker.values[0]))
                if self.selected is None:
                    await interaction.response.send_message(
                        "That session is no longer listed. Open Sessions again.", ephemeral=True
                    )
                    return
                await interaction.response.edit_message(
                    content=_selected_text(self.selected), view=self
                )

            picker.callback = pick
            self.add_item(picker)

        self._button("Open", discord.ButtonStyle.primary, self._open)
        self._button("New in same folder", discord.ButtonStyle.secondary, self._new_in_same_folder)
        self._button("Close", discord.ButtonStyle.danger, self._close)
        self._button("Search", discord.ButtonStyle.secondary, self._search)

    def _button(
        self,
        label: str,
        style: discord.ButtonStyle,
        handler: Callable[[discord.Interaction], Coroutine[Any, Any, None]],
    ) -> None:
        button: discord.ui.Button[SessionBrowserView] = discord.ui.Button(
            label=label, style=style, row=1
        )

        async def callback(interaction: discord.Interaction) -> None:
            await handler(interaction)

        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own Sessions menu.", ephemeral=True)
            return False
        return True

    async def _need_selection(self, interaction: discord.Interaction) -> SessionEntry | None:
        if self.selected is None:
            await interaction.response.send_message(
                "Choose a session from the list first.", ephemeral=True
            )
        return self.selected

    async def _open(self, interaction: discord.Interaction) -> None:
        entry = await self._need_selection(interaction)
        if entry is not None:
            await self.actions.open(interaction, entry.thread_id)

    async def _new_in_same_folder(self, interaction: discord.Interaction) -> None:
        entry = await self._need_selection(interaction)
        if entry is None:
            return
        if not entry.folder:
            await interaction.response.send_message(
                "That session has no bound folder. Use **New session** instead.", ephemeral=True
            )
            return
        await self.actions.new_in_same_folder(interaction, entry.folder)

    async def _close(self, interaction: discord.Interaction) -> None:
        entry = await self._need_selection(interaction)
        if entry is not None:
            await self.actions.close(interaction, entry.thread_id)

    async def _search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SearchModal(self.actions))


def _selected_text(entry: SessionEntry) -> str:
    state = "closed — Open reopens it" if entry.closed else "open"
    folder = f"`{entry.folder}`" if entry.folder else "no bound folder"
    return f"Selected **{entry.title}** ({state})\n{folder}"


def browser_text(entries: Sequence[SessionEntry], query: str | None) -> str:
    """The message above the list."""
    if not entries:
        if query:
            return f"No accessible session matches `{query}`. Try another word or **Search**."
        return "No accessible sessions yet. Start one with **New session**."
    scope = f" matching `{query}`" if query else ""
    return (
        f"**Sessions**{scope} — newest first. Choose one, then **Open**, "
        "**New in same folder**, or **Close**."
    )
