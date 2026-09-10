"""Tests for discord_ui.render_preview.

Playwright itself is optional at import time — render_preview must degrade
gracefully when the browser isn't installed. These tests do NOT launch a
browser; they exercise selection, cache, and degradation logic only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_discord.discord_ui import render_preview as rp


class TestIsRenderable:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("report.html", True),
            ("Report.HTML", True),
            ("diagram.svg", True),
            ("notes.md", True),
            ("README.markdown", True),
            ("code.py", False),
            ("image.png", False),
            ("data.json", False),
            ("demo.plan.json", True),
            ("plan.json", False),
            ("no-extension", False),
        ],
    )
    def test_extension_detection(self, name: str, expected: bool) -> None:
        assert rp.is_renderable(name) is expected


class TestPreviewName:
    def test_appends_preview_png_suffix(self) -> None:
        assert rp.preview_name("report.html") == "report.preview.png"

    def test_preserves_subpath(self) -> None:
        assert rp.preview_name("docs/dash.svg") == "docs/dash.preview.png"

    def test_markdown_extension(self) -> None:
        assert rp.preview_name("NOTES.md") == "NOTES.preview.png"

    def test_plan_card_name(self) -> None:
        assert rp.preview_name("demo.plan.json") == "demo.plan.png"


class TestRenderFileToPng:
    @pytest.mark.asyncio
    async def test_non_renderable_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("x=1", encoding="utf-8")
        assert await rp.render_file_to_png(f) is None

    @pytest.mark.asyncio
    async def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert await rp.render_file_to_png(tmp_path / "ghost.html") is None

    @pytest.mark.asyncio
    async def test_degrades_when_playwright_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When playwright cannot be imported or the browser isn't installed,
        render_file_to_png returns None instead of raising."""
        f = tmp_path / "r.html"
        f.write_text("<h1>hi</h1>", encoding="utf-8")
        monkeypatch.setattr(rp, "_HAS_PLAYWRIGHT", False)
        assert await rp.render_file_to_png(f) is None

    @pytest.mark.asyncio
    async def test_oversized_plan_preview_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plan card rides in the same message batch as the original file —
        an oversized PNG would fail the whole batch, so it is dropped."""
        from claude_discord.discord_ui import plan_card as pc

        async def fake_render(_source: Path) -> bytes:
            return b"x" * (rp._PREVIEW_MAX_BYTES + 1)

        monkeypatch.setattr(pc, "render_plan_card_to_png", fake_render)
        f = tmp_path / "big.plan.json"
        f.write_text("{}", encoding="utf-8")
        assert await rp.render_file_to_png(f) is None


class TestMarkdownToHtml:
    def test_wraps_markdown_in_styled_html(self) -> None:
        html = rp._markdown_to_html("# Title\n\nHello **world**.")
        assert "<h1>" in html or "Title" in html
        assert "<html" in html.lower()
