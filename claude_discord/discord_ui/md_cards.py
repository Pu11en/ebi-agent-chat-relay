"""Render a Markdown document as Discord Components V2 cards.

Drew only reads Discord, never attached files, so every Markdown deliverable is
shown inline: each ``## section`` becomes a colored card, ``###`` headings and
``---`` rules become dividers, and tables (which Discord cannot draw) become
bullet lines. The pure functions here are tested without Discord; only
``build_views`` touches discord.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Discord caps a Components V2 message at 4000 text characters and 40
# components. Stay well under both so a card never fails to send.
MAX_CARD_CHARS = 3600
MAX_CARD_PARTS = 30
MAX_CARDS = 10
COLORS = (0x5865F2, 0xFAA61A, 0x3BA55C, 0xED4245, 0xEB459E, 0x1ABC9C)

SEPARATOR = None  # sentinel part: a divider line


@dataclass
class Card:
    parts: list[str | None] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(p) for p in self.parts if p)


def _table_to_bullets(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]
    if not cells:
        return ""
    header, body = cells[0], cells[1:]
    if not body:
        return "• " + " · ".join(header)
    lines = []
    for row in body:
        first = f"**{row[0]}**" if row and row[0] else ""
        rest = [
            f"{header[i]}: {c}" if i < len(header) and header[i] else c
            for i, c in enumerate(row[1:], start=1)
            if c
        ]
        lines.append("• " + " · ".join([first, *rest] if first else rest))
    return "\n".join(lines)


def _blocks(section: str) -> list[str | None]:
    """Split one section into text blocks and separators."""
    out: list[str | None] = []
    buf: list[str] = []
    table: list[str] = []
    in_code = False

    def flush() -> None:
        if table:
            buf.append(_table_to_bullets(table))
            table.clear()
        text = "\n".join(buf).strip("\n")
        if text.strip():
            out.append(text)
        buf.clear()

    for line in section.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            buf.append(line)
            continue
        if in_code:
            buf.append(line)
            continue
        if line.lstrip().startswith("|"):
            table.append(line)
            continue
        if table:
            buf.append(_table_to_bullets(table))
            table.clear()
        if re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line):
            flush()
            out.append(SEPARATOR)
        elif line.startswith("### "):
            flush()
            if out:
                out.append(SEPARATOR)
            buf.append(line)
        elif not line.strip():
            flush()
        else:
            buf.append(line)
    flush()
    # Collapse leading/trailing/double separators.
    cleaned: list[str | None] = []
    for part in out:
        if part is SEPARATOR and (not cleaned or cleaned[-1] is SEPARATOR):
            continue
        cleaned.append(part)
    while cleaned and cleaned[-1] is SEPARATOR:
        cleaned.pop()
    return cleaned


def _hard_split(text: str) -> list[str]:
    pieces, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > MAX_CARD_CHARS:
            if cur:
                pieces.append(cur)
            cur = ""
            pieces.append(line[:MAX_CARD_CHARS])
            line = line[MAX_CARD_CHARS:]
        if len(cur) + len(line) > MAX_CARD_CHARS:
            pieces.append(cur)
            cur = ""
        cur += line
    if cur:
        pieces.append(cur)
    return [p.strip("\n") for p in pieces if p.strip()]


def markdown_to_cards(text: str) -> list[Card]:
    """Turn Markdown into cards: one per ``##`` section, split when too big."""
    sections = re.split(r"(?m)^(?=## )", text.strip())
    cards: list[Card] = []
    for section in sections:
        if not section.strip():
            continue
        heading = section.splitlines()[0] if section.startswith("## ") else None
        card = Card()
        for part in _blocks(section):
            pieces: list[str | None] = (
                [part] if part is None or len(part) <= MAX_CARD_CHARS else list(_hard_split(part))
            )
            for piece in pieces:
                size = len(piece) if piece else 0
                if card.parts and (
                    card.chars + size > MAX_CARD_CHARS or len(card.parts) >= MAX_CARD_PARTS
                ):
                    while card.parts and card.parts[-1] is SEPARATOR:
                        card.parts.pop()
                    cards.append(card)
                    card = Card([f"{heading} (continued)"] if heading else [])
                if piece is SEPARATOR and not card.parts:
                    continue
                card.parts.append(piece)
        if card.parts:
            cards.append(card)
    if len(cards) > MAX_CARDS:
        cards = cards[:MAX_CARDS]
        cards[-1].parts.append("-# ✂️ The rest is too long for cards. It is in the attached file.")
    if len(cards) > 1:
        for i, card in enumerate(cards, start=1):
            card.parts.append(f"-# Card {i} of {len(cards)}")
    return cards


def build_views(text: str) -> list:
    """Build one discord.py LayoutView per card."""
    import discord

    views = []
    for i, card in enumerate(markdown_to_cards(text)):
        container = discord.ui.Container(accent_colour=COLORS[i % len(COLORS)])
        for part in card.parts:
            if part is None:
                container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            else:
                container.add_item(discord.ui.TextDisplay(part))
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        views.append(view)
    return views
