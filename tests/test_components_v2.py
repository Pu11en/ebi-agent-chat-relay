"""Tests for discord_ui.components_v2 — the Container v2 file-delivery envelope."""

from __future__ import annotations

import io

import discord
import pytest
from discord.ui import Container, LayoutView, MediaGallery, TextDisplay

from claude_discord.discord_ui.components_v2 import (
    RenderedFile,
    build_file_delivery_view,
)


def _dfile(name: str) -> discord.File:
    return discord.File(io.BytesIO(b"x"), filename=name)


def _walk(view: LayoutView) -> list[type]:
    types: list[type] = []
    for child in view.children:
        types.append(type(child))
        for sub in getattr(child, "children", ()):
            types.append(type(sub))
    return types


class TestBuildFileDeliveryView:
    def test_returns_layout_view(self) -> None:
        view = build_file_delivery_view(
            header="Report",
            body="Weekly numbers.",
            files=[RenderedFile(file=_dfile("r.html"), preview=None)],
        )
        assert isinstance(view, LayoutView)

    def test_contains_container(self) -> None:
        view = build_file_delivery_view(
            header="Report",
            body="Weekly numbers.",
            files=[RenderedFile(file=_dfile("r.html"), preview=None)],
        )
        assert any(isinstance(c, Container) for c in view.children)

    def test_header_appears_as_text_display(self) -> None:
        view = build_file_delivery_view(
            header="Weekly Report",
            body="",
            files=[RenderedFile(file=_dfile("r.html"), preview=None)],
        )
        container = next(c for c in view.children if isinstance(c, Container))
        text_nodes = [c for c in container.children if isinstance(c, TextDisplay)]
        assert any("Weekly Report" in t.content for t in text_nodes)

    def test_media_gallery_present_when_preview_exists(self) -> None:
        view = build_file_delivery_view(
            header="X",
            body="",
            files=[RenderedFile(file=_dfile("r.html"), preview=_dfile("r.preview.png"))],
        )
        container = next(c for c in view.children if isinstance(c, Container))
        assert any(isinstance(c, MediaGallery) for c in container.children)

    def test_no_media_gallery_when_no_previews(self) -> None:
        view = build_file_delivery_view(
            header="X",
            body="",
            files=[RenderedFile(file=_dfile("r.py"), preview=None)],
        )
        container = next(c for c in view.children if isinstance(c, Container))
        assert not any(isinstance(c, MediaGallery) for c in container.children)

    def test_all_files_and_previews_returned_for_send(self) -> None:
        rf = [
            RenderedFile(file=_dfile("a.html"), preview=_dfile("a.preview.png")),
            RenderedFile(file=_dfile("b.py"), preview=None),
        ]
        view = build_file_delivery_view(header="X", body="", files=rf)
        # Property that the caller uses to pass files= to thread.send
        assert len(view.attached_files) == 3
        names = {f.filename for f in view.attached_files}
        assert names == {"a.html", "a.preview.png", "b.py"}

    def test_body_truncated_to_container_budget(self) -> None:
        big = "x" * 5000
        view = build_file_delivery_view(
            header="X",
            body=big,
            files=[RenderedFile(file=_dfile("r.py"), preview=None)],
        )
        container = next(c for c in view.children if isinstance(c, Container))
        joined = "".join(t.content for t in container.children if isinstance(t, TextDisplay))
        assert len(joined) < 4200  # header + trimmed body + slack

    def test_raises_when_no_files(self) -> None:
        with pytest.raises(ValueError):
            build_file_delivery_view(header="X", body="", files=[])
