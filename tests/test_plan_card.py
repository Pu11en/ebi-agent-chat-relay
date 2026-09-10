"""Tests for the plan-card renderer (discord_ui.plan_card).

No browser is launched: the render path is exercised through a fake
Playwright page, and everything else (template tolerance, clipping,
escaping, parse failures) is pure logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_discord.discord_ui import plan_card
from claude_discord.discord_ui import render_preview as rp


class TestCardHtml:
    def test_non_dict_steps_do_not_raise(self) -> None:
        """A model can emit ``"steps": ["some text"]`` — valid JSON, dict top
        level — and the card must render it, not crash the file pipeline."""
        spec = {
            "goal": "g",
            "exists": "e",
            "steps": ["plain text", 7, {"do": "real", "outcome": "ok"}],
            "done": "d",
        }
        html = plan_card._card_html(spec)
        assert "plain text" in html
        assert "real" in html

    def test_missing_slots_show_placeholders(self) -> None:
        html = plan_card._card_html({})
        assert "goal missing" in html
        assert "what exists missing" in html
        assert "done-when missing" in html

    def test_long_fields_are_clipped(self) -> None:
        spec = {
            "goal": "x" * 20_000,
            "exists": "e",
            "steps": [{"do": "y" * 20_000, "outcome": ""}],
            "done": "d",
        }
        html = plan_card._card_html(spec)
        assert "truncated" in html
        assert len(html) < 30_000

    def test_values_are_html_escaped(self) -> None:
        html = plan_card._card_html({"goal": "<script>alert(1)</script>", "steps": []})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_step_count_is_capped_with_an_overflow_note(self) -> None:
        spec = {"goal": "g", "steps": [{"do": f"s{i}"} for i in range(12)]}
        html = plan_card._card_html(spec)
        assert "s7" in html
        assert "s8" not in html
        assert "+4 more" in html


class TestRenderPlanCardToPng:
    @pytest.mark.asyncio
    async def test_bad_json_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "x.plan.json"
        f.write_text("{nope", encoding="utf-8")
        assert await plan_card.render_plan_card_to_png(f) is None

    @pytest.mark.asyncio
    async def test_non_object_json_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "x.plan.json"
        f.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        assert await plan_card.render_plan_card_to_png(f) is None

    @pytest.mark.asyncio
    async def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert await plan_card.render_plan_card_to_png(tmp_path / "ghost.plan.json") is None

    @pytest.mark.asyncio
    async def test_renders_the_spec_through_the_browser(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, str] = {}

        class FakePage:
            async def set_content(self, html: str, **_kwargs: object) -> None:
                captured["html"] = html

            async def screenshot(self, **_kwargs: object) -> bytes:
                return b"png-bytes"

            async def close(self) -> None:
                return None

        class FakeBrowser:
            async def new_page(self, **_kwargs: object) -> FakePage:
                return FakePage()

        async def fake_ensure_browser() -> object:
            return FakeBrowser()

        monkeypatch.setattr(rp, "_ensure_browser", fake_ensure_browser)

        f = tmp_path / "x.plan.json"
        f.write_text(
            json.dumps(
                {
                    "goal": "Ship it",
                    "exists": "the file pipeline",
                    "steps": [{"do": "render", "outcome": "a PNG"}],
                    "done": "shipped",
                }
            ),
            encoding="utf-8",
        )
        png = await plan_card.render_plan_card_to_png(f)

        assert png == b"png-bytes"
        assert "Ship it" in captured["html"]
        assert "a PNG" in captured["html"]

    @pytest.mark.asyncio
    async def test_no_browser_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_ensure_browser() -> None:
            return None

        monkeypatch.setattr(rp, "_ensure_browser", fake_ensure_browser)
        f = tmp_path / "x.plan.json"
        f.write_text(json.dumps({"goal": "g"}), encoding="utf-8")
        assert await plan_card.render_plan_card_to_png(f) is None
