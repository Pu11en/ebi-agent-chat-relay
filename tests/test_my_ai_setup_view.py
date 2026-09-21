"""My AI Setup view tests (tasks 4.1 and 4.2): owner-bound, paged, within Discord's limits.

The view renders what the queries return and never collects, spawns or
edits anything. Browse by kind is the first tab, Where it lives and Compare
computers are siblings, Recent changes is one more. Every page fits one
message and one component set; every button is the owner's alone; and the
detail view lists facts without the internal ownership vocabulary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    ContentFingerprint,
    DiagnosticSeverity,
    EffectiveScope,
    HarnessAvailability,
    InventoryDiagnostic,
    InventoryItem,
    InventorySnapshot,
    InventorySource,
    ItemIdentity,
    Measurement,
    OwnershipClass,
    SetupKind,
)
from claude_discord.ai_setup_queries import InventoryFilter
from claude_discord.discord_ui.my_ai_setup import (
    MAX_MESSAGE_CHARS,
    ItemDetailView,
    MyAISetupView,
    SetupViewContext,
    Tab,
    ViewState,
    render,
    render_item_detail,
)

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)


def item(
    name: str,
    kind: SetupKind = SetupKind.SKILL,
    *,
    computer: str = "drewai",
    scope: EffectiveScope | None = None,
    classification: Classification = Classification.CUSTOM,
    changed: datetime | None = None,
    digest: str = "ab" * 32,
    state: AvailabilityState = AvailabilityState.DISCOVERED,
) -> InventoryItem:
    return InventoryItem(
        identity=ItemIdentity(kind=kind, source_key="claude-home", name=name),
        display_name=name,
        source=InventorySource(
            key="claude-home",
            computer=computer,
            label="Claude home",
            locator=f"~/.claude/{kind.value}/{name}",
            harness="claude",
            modified_at=changed,
        ),
        scope=scope or EffectiveScope.shared_profile("drew"),
        classification=classification,
        ownership=OwnershipClass.MEGA_GLOBAL,
        availability=(HarnessAvailability(harness="claude", state=state, computer=computer),),
        fingerprint=ContentFingerprint(digest=digest),
        last_changed_at=changed,
    )


def context(*, items: int = 3, remotes: bool = True) -> SetupViewContext:
    local_items = [
        item(f"skill-{index:02d}", changed=NOW - timedelta(days=index)) for index in range(items)
    ]
    local_items.append(item("CLAUDE.md", SetupKind.INSTRUCTION, scope=EffectiveScope.everywhere()))
    local_items.append(item("stock", SetupKind.PLUGIN, classification=Classification.BUILTIN))
    local_items.append(
        item("verify", SetupKind.COMMAND, scope=EffectiveScope.one_computer("drewai"))
    )
    local = InventorySnapshot(
        computer="drewai",
        owner="drew",
        collected_at=NOW,
        items=tuple(local_items),
        diagnostics=(
            InventoryDiagnostic(
                source_key="codex-home",
                severity=DiagnosticSeverity.WARNING,
                message="The Codex home root is not present on this computer (~/.codex)",
                computer="drewai",
                occurred_at=NOW,
            ),
        ),
    )
    remote_snapshots: dict[str, InventorySnapshot | None] = {}
    if remotes:
        remote_snapshots["imac"] = InventorySnapshot(
            computer="imac",
            owner="drew",
            collected_at=NOW - timedelta(days=2),
            items=(
                item("skill-00", computer="imac"),
                item("skill-01", computer="imac", digest="cd" * 32),
            ),
        )
        remote_snapshots["david"] = None
    return SetupViewContext(
        computer_name="DrewAI",
        local=local,
        remotes=remote_snapshots,
        exceptions={"imac": (DeliberateException("command:", "No Cogs on the iMac"),)},
        now=NOW,
    )


def interaction(user: int = 42):
    event = MagicMock(spec=discord.Interaction)
    event.user = SimpleNamespace(id=user)
    event.response = MagicMock()
    event.response.send_message = AsyncMock()
    event.response.defer = AsyncMock()
    event.response.send_modal = AsyncMock()
    event.response.edit_message = AsyncMock()
    event.followup = MagicMock()
    event.followup.send = AsyncMock()
    return event


def buttons(view: discord.ui.View) -> dict[str, discord.ui.Button]:
    return {c.label or "": c for c in view.children if isinstance(c, discord.ui.Button)}


def selects(view: discord.ui.View) -> list[discord.ui.Select]:
    return [c for c in view.children if isinstance(c, discord.ui.Select)]


class TestBrowseFirst:
    def test_opens_on_browse_by_kind_with_custom_items_only(self):
        text, view = render(context(), ViewState(), user_id=42)
        assert "My AI Setup" in text and "Browse by kind" in text
        assert "Instructions" in text and "Skills" in text and "Commands" in text
        assert "skill-00" in text and "CLAUDE.md" in text
        assert "stock" not in text  # a built-in stays hidden by default
        assert "Mega Global" not in text and "mega_global" not in text
        assert "1 note" in text  # the diagnostic is counted, not hidden
        assert isinstance(view, MyAISetupView)
        assert view.state.tab is Tab.BROWSE

    def test_the_built_in_filter_shows_built_ins_labelled(self):
        state = ViewState(inventory_filter=InventoryFilter(include_builtins=True))
        text, _ = render(context(), state, user_id=42)
        assert "stock" in text and "built-in" in text.lower()

    def test_tabs_are_siblings_and_switching_keeps_the_filter(self):
        state = ViewState(inventory_filter=InventoryFilter(query="skill"))
        _, view = render(context(), state, user_id=42)
        labels = set(buttons(view))
        assert {"Browse by kind", "Where it lives", "Compare computers", "Recent changes"} <= labels
        moved = view.state.switch(Tab.WHERE)
        assert moved.tab is Tab.WHERE and moved.page == 0
        assert moved.inventory_filter.query == "skill"


class TestSiblingTabs:
    def test_where_it_lives_groups_by_user_facing_scope(self):
        text, _ = render(context(), ViewState(tab=Tab.WHERE), user_id=42)
        assert "Where it lives" in text
        assert "Everywhere" in text
        assert "Shared Drew profile" in text
        assert "One computer (drewai)" in text
        assert "Claude home" in text
        assert "Mega" not in text

    def test_compare_computers_labels_stale_and_unreachable(self):
        text, _ = render(context(), ViewState(tab=Tab.COMPARE), user_id=42)
        assert "Compare computers" in text
        assert "imac" in text and "stale" in text.lower()
        assert "david" in text and "unreachable" in text.lower()
        assert "Aligned" in text and "Different content" in text
        assert "Deliberate difference" in text  # the iMac declared no Cogs
        assert "2026-09-19 09:00 UTC" in text  # last verification time, shown not hidden

    def test_recent_changes_orders_dated_and_separates_undated(self):
        text, _ = render(context(), ViewState(tab=Tab.RECENT), user_id=42)
        assert "Recent changes" in text
        assert text.index("skill-00") < text.index("skill-01") < text.index("skill-02")
        assert "unknown" in text.lower()  # CLAUDE.md, verify have no modification time
        assert "source modification time" in text


class TestLimits:
    def test_a_page_never_exceeds_the_message_or_component_limits(self):
        ctx = context(items=120)
        for tab in Tab:
            text, view = render(ctx, ViewState(tab=tab), user_id=42)
            assert len(text) <= MAX_MESSAGE_CHARS, tab
            assert len(view.children) <= 25
            rows = {child.row for child in view.children}
            assert all(row is None or row < 5 for row in rows)
            for select in selects(view):
                assert 1 <= len(select.options) <= 25

    def test_pages_are_offered_and_nothing_is_silently_dropped(self):
        ctx = context(items=120)
        first_text, first = render(ctx, ViewState(), user_id=42)
        assert "Page 1 of" in first_text
        last_state = first.state
        seen: set[str] = set()
        page = 0
        while True:
            text, view = render(ctx, last_state.at_page(page), user_id=42)
            seen |= {option.value for option in selects(view)[0].options}
            if buttons(view)["Next"].disabled:
                break
            page += 1
        custom = {entry.identity.key for entry in ctx.local.custom_items}
        assert seen == custom

    async def test_next_and_previous_edit_the_same_message(self):
        ctx = context(items=30)
        _, view = render(ctx, ViewState(), user_id=42)
        event = interaction()
        await buttons(view)["Next"].callback(event)
        event.response.edit_message.assert_awaited_once()
        new_view = event.response.edit_message.await_args.kwargs["view"]
        assert new_view.state.page == 1
        assert "Page 2 of" in event.response.edit_message.await_args.kwargs["content"]


class TestOwnership:
    async def test_the_view_is_the_owners_alone(self):
        _, view = render(context(), ViewState(), user_id=42)
        assert await view.interaction_check(interaction(42))
        other = interaction(7)
        assert not await view.interaction_check(other)
        assert "your own" in other.response.send_message.call_args.args[0].lower()

    async def test_switching_tabs_and_searching_run_no_model_and_change_nothing(self):
        ctx = context()
        _, view = render(ctx, ViewState(), user_id=42)
        event = interaction()
        await buttons(view)["Compare computers"].callback(event)
        content = event.response.edit_message.await_args.kwargs["content"]
        assert "Compare computers" in content
        await buttons(view)["Search"].callback(event)
        modal = event.response.send_modal.await_args.args[0]
        modal.query._value = "skill-01"
        event2 = interaction()
        await modal.on_submit(event2)
        searched = event2.response.edit_message.await_args.kwargs["content"]
        assert "skill-01" in searched and "skill-00" not in searched
        assert ctx.agent is None  # nothing to spawn with, and nothing tried


class TestDetail:
    def test_the_detail_lists_facts_without_ownership_or_secrets(self):
        ctx = context()
        selected = ctx.local.custom_items[0]
        text, view = render_item_detail(ctx, ViewState(), selected, user_id=42)
        assert selected.display_name in text
        assert "Claude home — ~/.claude/skill/skill-00" in text
        assert "Shared Drew profile" in text
        assert "claude: Discovered" in text
        assert "unknown" in text  # size and tokens are unmeasured
        assert "source modification time" in text
        assert "Mega" not in text and "mega_global" not in text and "ownership" not in text.lower()
        assert isinstance(view, ItemDetailView)
        labels = set(buttons(view))
        assert "Back" in labels and "Ask Setup Agent" in labels
        assert buttons(view)["Ask Setup Agent"].disabled  # no agent wired in this context

    def test_the_detail_shows_how_other_computers_compare(self):
        ctx = context()
        selected = next(
            entry for entry in ctx.local.custom_items if entry.display_name == "skill-01"
        )
        text, _ = render_item_detail(ctx, ViewState(), selected, user_id=42)
        assert "imac: Different content (stale)" in text
        assert "david: Unreachable" in text

    async def test_selecting_an_item_opens_its_detail_and_back_returns(self):
        ctx = context()
        _, view = render(ctx, ViewState(), user_id=42)
        picker = selects(view)[0]
        picker._values = [picker.options[0].value]
        event = interaction()
        await picker.callback(event)
        detail_view = event.response.edit_message.await_args.kwargs["view"]
        assert isinstance(detail_view, ItemDetailView)
        back = interaction()
        await buttons(detail_view)["Back"].callback(back)
        assert isinstance(back.response.edit_message.await_args.kwargs["view"], MyAISetupView)
        assert "Browse by kind" in back.response.edit_message.await_args.kwargs["content"]

    def test_an_item_with_measurements_shows_them_labelled(self):
        ctx = context()
        measured = InventoryItem(
            identity=ItemIdentity(kind=SetupKind.INSTRUCTION, source_key="claude-home", name="big"),
            display_name="big",
            source=ctx.local.custom_items[0].source,
            scope=EffectiveScope.everywhere(),
            measurement=Measurement.estimated(
                byte_size=4000, character_count=3900, token_count=975
            ),
            last_changed_at=NOW - timedelta(hours=2),
        )
        text, _ = render_item_detail(ctx, ViewState(), measured, user_id=42)
        assert "4,000 bytes" in text
        assert "~975 tokens (estimated)" in text
        assert "2026-09-21 07:00 UTC (source modification time)" in text


class TestDetailSnapshots:
    """Task 4.2: details of items collected from secret-bearing sources stay safe."""

    LEAKED = "sk-ant-api03-ZZaabbccddeeff112233"  # noqa: S105 — a fake
    HOOK_TOKEN = "ghp_abcdefghijklmnopqrstuv0123456789"  # noqa: S105 — a fake

    def _collected(self, tmp_path):
        import json

        from claude_discord.ai_setup_adapters import ClaudeHarnessAdapter, claude_home_root
        from claude_discord.ai_setup_collector import CollectionContext, InventoryCollector

        home = tmp_path / "home" / ".claude"
        home.mkdir(parents=True)
        (home / "settings.json").write_text(
            json.dumps(
                {
                    "model": "opus",
                    "env": {"ANTHROPIC_API_KEY": self.LEAKED},
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"guard --token={self.HOOK_TOKEN}",
                                    }
                                ],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "home" / ".claude.json").write_text(
            json.dumps(
                {
                    "oauthAccount": {"accessToken": self.LEAKED},
                    "mcpServers": {
                        "docs": {
                            "command": "npx",
                            "args": [f"--key={self.LEAKED}"],
                            "env": {"DOCS_TOKEN": self.LEAKED},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        collector = InventoryCollector()
        collector.register(
            ClaudeHarnessAdapter(claude_home_root(home, owner="drew", home=tmp_path / "home"))
        )
        result = collector.collect(
            CollectionContext(
                computer="drewai", owner="drew", collected_at=NOW, harnesses=("claude",)
            )
        )
        assert result.failed_adapters == ()
        return result.snapshot

    def test_every_detail_hides_secrets_and_the_ownership_label(self, tmp_path):
        snapshot = self._collected(tmp_path)
        ctx = SetupViewContext(computer_name="DrewAI", local=snapshot, now=NOW)
        assert snapshot.items, "the fixture home produced no items"
        for entry in snapshot.items:
            text, _ = render_item_detail(ctx, ViewState(), entry, user_id=42)
            lowered = text.lower()
            assert self.LEAKED not in text
            assert self.HOOK_TOKEN not in text
            assert "anthropic_api_key" not in lowered
            assert "oauthaccount" not in lowered
            assert "mega global" not in lowered and "mega_global" not in lowered
            assert "ownership" not in lowered
            assert len(text) <= MAX_MESSAGE_CHARS
        docs = next(entry for entry in snapshot.items if entry.display_name == "docs")
        text, _ = render_item_detail(ctx, ViewState(), docs, user_id=42)
        assert "DOCS_TOKEN: satisfied (credential)" in text
        assert "~/.claude.json" in text
        hook = next(entry for entry in snapshot.items if entry.kind is SetupKind.HOOK)
        text, _ = render_item_detail(ctx, ViewState(), hook, user_id=42)
        assert "guard" in text and "[redacted]" in text

    def test_browse_and_where_text_hide_the_same(self, tmp_path):
        snapshot = self._collected(tmp_path)
        ctx = SetupViewContext(computer_name="DrewAI", local=snapshot, now=NOW)
        for tab in Tab:
            text, _ = render(ctx, ViewState(tab=tab), user_id=42)
            assert self.LEAKED not in text and self.HOOK_TOKEN not in text
            assert "mega" not in text.lower()
