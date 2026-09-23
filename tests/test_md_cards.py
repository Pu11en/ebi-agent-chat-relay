"""Markdown → Discord card rendering."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from claude_discord.attachment_outbox import deliver_pending
from claude_discord.discord_ui.md_cards import (
    MAX_CARD_CHARS,
    MAX_CARDS,
    SEPARATOR,
    build_views,
    markdown_to_cards,
)


def _text(card):
    return "\n".join(p for p in card.parts if p)


def test_one_card_per_section_with_title_on_first():
    cards = markdown_to_cards("# Title\nIntro.\n\n## One\nA\n\n## Two\nB")
    assert len(cards) == 3
    assert "# Title" in _text(cards[0])
    assert _text(cards[1]).startswith("## One")
    assert cards[2].parts[-1] == "-# Card 3 of 3"


def test_single_card_has_no_counter():
    (card,) = markdown_to_cards("## Only\nhello")
    assert "Card 1 of 1" not in _text(card)


def test_subheadings_and_rules_become_separators():
    (card,) = markdown_to_cards("## S\nA\n### Sub\nB\n\n---\n\nC")
    assert card.parts == ["## S\nA", SEPARATOR, "### Sub\nB", SEPARATOR, "C"]


def test_tables_become_bullets():
    (card,) = markdown_to_cards("## S\n| Name | Age |\n|---|---|\n| Ann | 3 |")
    assert "• **Ann** · Age: 3" in _text(card)
    assert "|" not in _text(card)


def test_code_block_untouched():
    (card,) = markdown_to_cards("## S\n```\n| not a table |\n### nope\n```")
    assert "| not a table |" in _text(card)
    assert SEPARATOR not in card.parts


def test_long_section_splits_under_limit():
    body = "\n\n".join("word " * 150 for _ in range(20))
    cards = markdown_to_cards(f"## Big\n{body}")
    assert len(cards) > 1
    assert all(c.chars <= MAX_CARD_CHARS + 40 for c in cards)
    assert _text(cards[1]).startswith("## Big (continued)")


def test_huge_single_line_is_hard_split():
    cards = markdown_to_cards("## S\n" + "x" * (MAX_CARD_CHARS * 2 + 10))
    assert all(c.chars <= MAX_CARD_CHARS + 40 for c in cards)


def test_card_cap():
    md = "\n".join(f"## S{i}\nbody" for i in range(MAX_CARDS + 5))
    cards = markdown_to_cards(md)
    assert len(cards) == MAX_CARDS
    assert "attached file" in _text(cards[-1])


def test_build_views_counts():
    views = build_views("## A\nx\n\n## B\ny")
    assert len(views) == 2


@pytest.mark.asyncio
async def test_markdown_attachment_is_shown_as_cards(tmp_path: Path):
    doc = tmp_path / "plan.md"
    doc.write_text("## Hi\nthere")
    other = tmp_path / "data.csv"
    other.write_text("a,b")
    marker = tmp_path / ".ccdb-attachments-1"
    marker.write_text(f"{doc}\n{other}\n")
    surface = AsyncMock()
    await deliver_pending(surface, marker)
    surface.send_markdown_cards.assert_awaited_once_with("## Hi\nthere")
    assert surface.deliver_files.await_count == 2


@pytest.mark.asyncio
async def test_card_failure_still_attaches(tmp_path: Path):
    doc = tmp_path / "plan.md"
    doc.write_text("## Hi")
    marker = tmp_path / ".ccdb-attachments-1"
    marker.write_text(f"{doc}\n")
    surface = AsyncMock()
    surface.send_markdown_cards.side_effect = RuntimeError("boom")
    await deliver_pending(surface, marker)
    surface.deliver_files.assert_awaited_once()
