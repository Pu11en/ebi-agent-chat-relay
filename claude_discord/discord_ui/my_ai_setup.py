"""My AI Setup: one ephemeral, owner-bound view over the inventory queries.

The view renders what :mod:`claude_discord.ai_setup_queries` returns and does
nothing else — it never collects, never spawns a model, never touches a
configuration file.  Its state is only the selected tab, the filter, the
page and (in the detail view) the selected item's identity key, so a button
press is a pure re-render of the same :class:`SetupViewContext`.

Browse by kind is the first tab; Where it lives and Compare computers are its
siblings; Recent changes is the fourth.  Every tab is a list of rows paged
to :data:`PAGE_SIZE`, the item select on each page holds only that page's
items (Discord allows 25 options), and the text is bounded to
:data:`MAX_MESSAGE_CHARS` so a large inventory turns into more pages, never
into a failed message or silently dropped items.

The detail view (:func:`render_item_detail`) lists the facts from
:func:`~claude_discord.ai_setup_queries.detail_facts` — source, user-facing
scope, availability evidence, prerequisites, measurements, last change — and
how each other computer compares.  Nothing here reads the item's ownership
class.  **Ask Setup Agent** is the only button that leaves the view, and it
does so through the ``agent`` callback the Cog supplies (task 4.4); without
one the button is disabled rather than pretending.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

import discord

from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_inventory import (
    Freshness,
    InventoryItem,
    InventorySnapshot,
    SetupKind,
)
from claude_discord.ai_setup_queries import (
    InventoryFilter,
    browse_by_kind,
    compare_computers,
    detail_facts,
    format_time,
    paginate,
    recent_changes,
    where_it_lives,
)
from claude_discord.ai_setup_remote import ComputerComparison
from claude_discord.database.ai_setup_repo import ObservedChange

#: Below Discord's 2,000-character cap, leaving room for the framework's prefixes.
MAX_MESSAGE_CHARS = 1900
#: Rows per page; headings count, so a page is never longer than this.
PAGE_SIZE = 10
_ROW_CHARS = 110

AskSetupAgent = Callable[[discord.Interaction, InventoryItem, str], Awaitable[None]]
Refresh = Callable[[discord.Interaction], Awaitable[None]]


class Tab(StrEnum):
    BROWSE = "browse"
    WHERE = "where"
    COMPARE = "compare"
    RECENT = "recent"

    @property
    def label(self) -> str:
        return _TAB_LABELS[self]


_TAB_LABELS: Mapping[Tab, str] = {
    Tab.BROWSE: "Browse by kind",
    Tab.WHERE: "Where it lives",
    Tab.COMPARE: "Compare computers",
    Tab.RECENT: "Recent changes",
}


@dataclass(frozen=True, slots=True)
class ViewState:
    """Everything a re-render needs: tab, filter, page. Nothing about the world."""

    tab: Tab = Tab.BROWSE
    inventory_filter: InventoryFilter = field(default_factory=InventoryFilter)
    page: int = 0

    def switch(self, tab: Tab) -> ViewState:
        return replace(self, tab=tab, page=0)

    def at_page(self, page: int) -> ViewState:
        return replace(self, page=max(0, page))

    def with_filter(self, inventory_filter: InventoryFilter) -> ViewState:
        return replace(self, inventory_filter=inventory_filter, page=0)

    def with_query(self, query: str) -> ViewState:
        return self.with_filter(replace(self.inventory_filter, query=query))

    def with_kinds(self, kinds: frozenset[SetupKind]) -> ViewState:
        return self.with_filter(replace(self.inventory_filter, kinds=kinds))

    def toggle_builtins(self) -> ViewState:
        current = self.inventory_filter
        return self.with_filter(replace(current, include_builtins=not current.include_builtins))


@dataclass(slots=True)
class SetupViewContext:
    """What one opening of My AI Setup has to show; collected once by the Cog."""

    computer_name: str
    local: InventorySnapshot
    now: datetime
    remotes: Mapping[str, InventorySnapshot | None] = field(default_factory=dict)
    exceptions: Mapping[str, tuple[DeliberateException, ...]] = field(default_factory=dict)
    observed: tuple[ObservedChange, ...] = ()
    agent: AskSetupAgent | None = None
    refresh: Refresh | None = None

    def item(self, key: str) -> InventoryItem | None:
        for entry in self.local.items:
            if entry.identity.key == key:
                return entry
        return None

    def comparisons(self, inventory_filter: InventoryFilter) -> tuple[ComputerComparison, ...]:
        if not self.remotes:
            return ()
        return compare_computers(
            self.local,
            self.remotes,
            now=self.now,
            exceptions=self.exceptions,
            inventory_filter=inventory_filter,
        )


# ---------------------------------------------------------------------------
# Rows: every tab is a list of these, paged the same way
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Row:
    text: str
    item: InventoryItem | None = None
    heading: str | None = None  # the heading this row sits under, for continuation


def _clip(text: str, limit: int = _ROW_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _state_summary(item: InventoryItem) -> str:
    if not item.availability:
        return "availability unknown"
    return ", ".join(f"{entry.harness} {entry.label.lower()}" for entry in item.availability)


def _item_row(item: InventoryItem, heading: str, *, extra: str | None = None) -> Row:
    marker = " *(built-in)*" if item.is_builtin else ""
    marker = " *(override)*" if item.classification.value == "overridden_builtin" else marker
    tail = extra if extra is not None else f"{item.scope.label} · {_state_summary(item)}"
    return Row(_clip(f"• **{item.display_name}**{marker} — {tail}"), item=item, heading=heading)


def _browse_rows(ctx: SetupViewContext, state: ViewState) -> list[Row]:
    rows: list[Row] = []
    for group in browse_by_kind(ctx.local, state.inventory_filter):
        heading = f"**{group.label}** ({group.count_label})"
        rows.append(Row(heading, heading=heading))
        rows.extend(_item_row(item, heading) for item in group.items)
    return rows


def _where_rows(ctx: SetupViewContext, state: ViewState) -> list[Row]:
    rows: list[Row] = []
    for group in where_it_lives(ctx.local, state.inventory_filter):
        heading = f"**{group.label}** ({group.count})"
        rows.append(Row(heading, heading=heading))
        for source in group.sources:
            sub = f"{heading} · _{source.label}_"
            rows.append(Row(f"_{source.label}_", heading=sub))
            rows.extend(
                _item_row(item, sub, extra=f"{item.kind.label} · {_state_summary(item)}")
                for item in source.items
            )
    return rows


def _freshness_line(comparison: ComputerComparison) -> str:
    match comparison.freshness:
        case Freshness.LIVE:
            return f"live (verified {format_time(comparison.verified_at)})"
        case Freshness.STALE:
            return f"stale (last verified {format_time(comparison.verified_at)})"
        case _:
            return "unreachable (no verified snapshot)"


def _compare_rows(ctx: SetupViewContext, state: ViewState) -> list[Row]:
    comparisons = ctx.comparisons(state.inventory_filter)
    if not comparisons:
        return [Row("No other computer has sent a snapshot yet.")]
    rows: list[Row] = []
    for comparison in comparisons:
        heading = f"**{comparison.computer}** — {_freshness_line(comparison)}"
        rows.append(Row(heading, heading=heading))
        if comparison.freshness is Freshness.UNREACHABLE:
            count = len(comparison.results)
            rows.append(
                Row(f"• {count} local item{'s' * (count != 1)}: Unreachable", heading=heading)
            )
            continue
        counts = comparison.counts
        summary = ", ".join(
            f"{count} {status.label.lower()}" for status, count in counts.items() if count
        )
        rows.append(Row(f"-# {summary or 'nothing to compare'}", heading=heading))
        for result in comparison.results:
            local = result.local
            label = result.label
            if result.detail and result.status.value in ("deliberate", "different"):
                label = f"{label}: {result.detail}"
            rows.append(
                Row(
                    _clip(f"• **{result.display_name}** — {label}"),
                    item=local,
                    heading=heading,
                )
            )
    return rows


def _recent_rows(ctx: SetupViewContext, state: ViewState) -> list[Row]:
    result = recent_changes(ctx.local, state.inventory_filter, observed=ctx.observed)
    rows: list[Row] = []
    if result.observed:
        heading = "**Observed by this inventory** (between snapshots)"
        rows.append(Row(heading, heading=heading))
        for change in result.observed:
            item = ctx.item(change.identity.key)
            rows.append(
                Row(
                    _clip(
                        f"• **{change.identity.name}** — {change.kind} "
                        f"({format_time(change.observed_at)})"
                    ),
                    item=item,
                    heading=heading,
                )
            )
    heading = "**Changed** (by source modification time, newest first)"
    rows.append(Row(heading, heading=heading))
    if not result.dated:
        rows.append(Row("• none with a known time", heading=heading))
    rows.extend(
        _item_row(item, heading, extra=f"{format_time(item.last_changed_at)} · {item.kind.label}")
        for item in result.dated
    )
    if result.undated:
        heading = "**Unknown time** (no source modification time)"
        rows.append(Row(heading, heading=heading))
        rows.extend(
            _item_row(item, heading, extra=f"unknown · {item.kind.label}")
            for item in result.undated
        )
    return rows


_ROWS: Mapping[Tab, Callable[[SetupViewContext, ViewState], list[Row]]] = {
    Tab.BROWSE: _browse_rows,
    Tab.WHERE: _where_rows,
    Tab.COMPARE: _compare_rows,
    Tab.RECENT: _recent_rows,
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _header(ctx: SetupViewContext, state: ViewState) -> str:
    notes = len(ctx.local.diagnostics)
    note_text = f" · {notes} note{'s' * (notes != 1)}" if notes else ""
    return (
        f"**{ctx.computer_name} · My AI Setup — {state.tab.label}**\n"
        f"-# {state.inventory_filter.describe()} · collected "
        f"{format_time(ctx.local.collected_at)}{note_text}"
    )


def _bounded(lines: Sequence[str], limit: int) -> str:
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > limit - 40:
            kept.append("-# … more on the next page")
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def render(ctx: SetupViewContext, state: ViewState, *, user_id: int) -> tuple[str, MyAISetupView]:
    """The message text and view for one tab, page and filter."""
    rows = _ROWS[state.tab](ctx, state)
    page = paginate(rows, page=state.page, page_size=PAGE_SIZE)
    state = state.at_page(page.page)
    lines = [_header(ctx, state)]
    if not rows:
        lines.append("Nothing matches. Clear the search or widen the filter.")
    else:
        first = page.items[0] if page.items else None
        if first is not None and first.heading and first.text != first.heading:
            lines.append(f"{first.heading} *(continued)*")
        lines.extend(row.text for row in page.items)
        footer = page.label.replace("items", "rows")
        if any(row.item is not None for row in page.items):
            footer += " — choose an item below for its details"
        lines.append(f"-# {footer}")
    text = _bounded(lines, MAX_MESSAGE_CHARS)
    view = MyAISetupView(ctx, state, page.items, user_id=user_id, has_next=page.has_next)
    return text, view


def _comparison_lines(ctx: SetupViewContext, item: InventoryItem) -> list[str]:
    lines: list[str] = []
    for comparison in ctx.comparisons(InventoryFilter(include_builtins=True)):
        match = next(
            (entry for entry in comparison.results if entry.identity == item.identity), None
        )
        if match is None:
            continue
        label = match.label
        if match.detail and match.status.value in ("deliberate", "different"):
            label = f"{label} — {match.detail}"
        lines.append(_clip(f"• {comparison.computer}: {label}", 160))
    return lines


def render_item_detail(
    ctx: SetupViewContext, state: ViewState, item: InventoryItem, *, user_id: int
) -> tuple[str, ItemDetailView]:
    """One item's safe facts and how the other computers compare."""
    lines = [f"**{ctx.computer_name} · My AI Setup — {item.display_name}**"]
    lines.extend(f"**{label}:** {_clip(value, 300)}" for label, value in detail_facts(item))
    comparisons = _comparison_lines(ctx, item)
    if comparisons:
        lines.append("**Other computers:**")
        lines.extend(comparisons)
    if ctx.agent is None:
        lines.append("-# Ask Setup Agent is unavailable here (no session backend wired).")
    else:
        lines.append(
            "-# Ask Setup Agent opens a normal session with these facts attached; "
            "browsing changes nothing."
        )
    text = _bounded(lines, MAX_MESSAGE_CHARS)
    return text, ItemDetailView(ctx, state, item, user_id=user_id)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


Handler = Callable[[discord.Interaction], Coroutine[Any, Any, None]]


class _OwnedView(discord.ui.View):
    def __init__(self, *, user_id: int) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own My AI Setup from Settings.", ephemeral=True
            )
            return False
        return True

    def _button(
        self,
        label: str,
        handler: Handler,
        *,
        row: int,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> discord.ui.Button[Any]:
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=label, style=style, row=row, disabled=disabled
        )

        async def callback(interaction: discord.Interaction) -> None:
            await handler(interaction)

        button.callback = callback
        self.add_item(button)
        return button


class MyAISetupView(_OwnedView):
    """Tabs, this page's item select, a kind filter, paging, search, built-ins, refresh."""

    def __init__(
        self,
        ctx: SetupViewContext,
        state: ViewState,
        rows: Sequence[Row],
        *,
        user_id: int,
        has_next: bool,
    ) -> None:
        super().__init__(user_id=user_id)
        self.ctx = ctx
        self.state = state
        for tab in Tab:
            style = (
                discord.ButtonStyle.primary if tab is state.tab else discord.ButtonStyle.secondary
            )
            self._button(tab.label, self._switcher(tab), row=0, style=style)

        items = [row.item for row in rows if row.item is not None]
        unique: dict[str, InventoryItem] = {}
        for entry in items:
            unique.setdefault(entry.identity.key, entry)
        if unique:
            picker: discord.ui.Select[Any] = discord.ui.Select(
                placeholder="Item details…",
                options=[
                    discord.SelectOption(
                        label=entry.display_name[:100],
                        description=f"{entry.kind.label} · {entry.scope.label}"[:100],
                        value=key[:100],
                    )
                    for key, entry in list(unique.items())[:25]
                ],
                row=1,
            )
            self._picker_keys = {key[:100]: key for key in unique}

            async def pick(interaction: discord.Interaction) -> None:
                key = self._picker_keys.get(picker.values[0], picker.values[0])
                item = self.ctx.item(key)
                if item is None:
                    await interaction.response.send_message(
                        "That item is no longer in the snapshot. Refresh My AI Setup.",
                        ephemeral=True,
                    )
                    return
                text, view = render_item_detail(self.ctx, self.state, item, user_id=self.user_id)
                await interaction.response.edit_message(content=text, view=view)

            picker.callback = pick
            self.add_item(picker)

        kinds: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Filter by kind",
            options=[
                discord.SelectOption(
                    label="All kinds", value="all", default=not state.inventory_filter.kinds
                ),
                *(
                    discord.SelectOption(
                        label=kind.label,
                        value=kind.value,
                        default=kind in state.inventory_filter.kinds,
                    )
                    for kind in SetupKind
                ),
            ],
            row=2,
        )

        async def choose_kind(interaction: discord.Interaction) -> None:
            chosen = kinds.values[0]
            selected = frozenset() if chosen == "all" else frozenset({SetupKind(chosen)})
            await self._show(interaction, self.state.with_kinds(selected))

        kinds.callback = choose_kind
        self.add_item(kinds)

        self._button("Prev", self._previous, row=3, disabled=state.page == 0)
        self._button("Next", self._next, row=3, disabled=not has_next)
        self._button("Search", self._search, row=3)
        builtins = state.inventory_filter.include_builtins
        self._button(
            "Hide built-ins" if builtins else "Show built-ins", self._toggle_builtins, row=3
        )
        self._button("Refresh", self._refresh, row=3, disabled=ctx.refresh is None)

    # -- re-rendering ------------------------------------------------------------

    async def _show(self, interaction: discord.Interaction, state: ViewState) -> None:
        text, view = render(self.ctx, state, user_id=self.user_id)
        await interaction.response.edit_message(content=text, view=view)

    def _switcher(self, tab: Tab) -> Handler:
        async def switch(interaction: discord.Interaction) -> None:
            await self._show(interaction, self.state.switch(tab))

        return switch

    async def _previous(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, self.state.at_page(self.state.page - 1))

    async def _next(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, self.state.at_page(self.state.page + 1))

    async def _toggle_builtins(self, interaction: discord.Interaction) -> None:
        await self._show(interaction, self.state.toggle_builtins())

    async def _search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SearchModal(self))

    async def _refresh(self, interaction: discord.Interaction) -> None:
        if self.ctx.refresh is not None:
            await self.ctx.refresh(interaction)

    async def apply_query(self, interaction: discord.Interaction, query: str) -> None:
        await self._show(interaction, self.state.with_query(query))


class SearchModal(discord.ui.Modal, title="Search My AI Setup"):
    query = discord.ui.TextInput(
        label="Name, kind, harness, computer or scope (blank clears)",
        max_length=100,
        required=False,
    )

    def __init__(self, view: MyAISetupView) -> None:
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.view.apply_query(interaction, str(self.query).strip())


class ItemDetailView(_OwnedView):
    """Back to the list, or hand the item's safe facts to a Setup Agent session."""

    def __init__(
        self, ctx: SetupViewContext, state: ViewState, item: InventoryItem, *, user_id: int
    ) -> None:
        super().__init__(user_id=user_id)
        self.ctx = ctx
        self.state = state
        self.item = item
        self._button("Back", self._back, row=0)
        self._button(
            "Ask Setup Agent",
            self._ask,
            row=0,
            style=discord.ButtonStyle.primary,
            disabled=ctx.agent is None,
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        text, view = render(self.ctx, self.state, user_id=self.user_id)
        await interaction.response.edit_message(content=text, view=view)

    async def _ask(self, interaction: discord.Interaction) -> None:
        if self.ctx.agent is None:
            await interaction.response.send_message(
                "Ask Setup Agent is not available here.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AskSetupAgentModal(self))


class AskSetupAgentModal(discord.ui.Modal, title="Ask Setup Agent"):
    question = discord.ui.TextInput(
        label="What do you want to know or change?",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, view: ItemDetailView) -> None:
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        agent = self.view.ctx.agent
        if agent is None:
            await interaction.response.send_message(
                "Ask Setup Agent is not available here.", ephemeral=True
            )
            return
        await agent(interaction, self.view.item, str(self.question).strip())


__all__ = [
    "MAX_MESSAGE_CHARS",
    "PAGE_SIZE",
    "AskSetupAgent",
    "AskSetupAgentModal",
    "ItemDetailView",
    "MyAISetupView",
    "Row",
    "SearchModal",
    "SetupViewContext",
    "Tab",
    "ViewState",
    "render",
    "render_item_detail",
]
