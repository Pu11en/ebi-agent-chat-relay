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
            # Markdown is sent as the raw file: Discord's own text preview is
            # scrollable and readable, a screenshot of it is neither.
            ("notes.md", False),
            ("README.markdown", False),
            ("code.py", False),
            ("image.png", False),
            ("data.json", False),
            # Plans become plan.md text in file_sender, never a picture.
            ("demo.plan.json", False),
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

    def test_svg_extension(self) -> None:
        assert rp.preview_name("Chart.SVG") == "Chart.preview.png"


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


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = type("Req", (), {"url": url})()
        self.aborted = False
        self.continued = False

    async def abort(self, *_args: object) -> None:
        self.aborted = True

    async def continue_(self, *_args: object, **_kw: object) -> None:
        self.continued = True


class _FakePage:
    def __init__(self) -> None:
        self.content: str | None = None
        self.route_pattern: str | None = None
        self.route_handler = None
        self.closed = False

    async def goto(self, *_args: object, **_kw: object) -> None:
        raise AssertionError("goto must never be used: file:// grants local-file reads")

    async def route(self, pattern: str, handler) -> None:  # noqa: ANN001
        self.route_pattern = pattern
        self.route_handler = handler

    async def set_content(self, html: str, **_kw: object) -> None:
        self.content = html

    async def wait_for_load_state(self, *_args: object, **_kw: object) -> None:
        return None

    async def screenshot(self, **_kw: object) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake"

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    async def new_page(self) -> _FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.page = _FakePage()
        self.context = _FakeContext(self.page)
        self.context_kwargs: dict[str, object] | None = None

    async def new_context(self, **kwargs: object) -> _FakeContext:
        self.context_kwargs = kwargs
        return self.context

    async def new_page(self, **_kw: object) -> _FakePage:
        raise AssertionError("pages must come from an isolated, script-less context")


class TestRenderIsolation:
    """A session's HTML must not become a local-file reader.

    Rendering via ``goto(file://…)`` let a prompt-injected session embed
    ``<iframe src="file:///…/.codex/auth.json">`` and receive the secret back
    as a PNG. The document is now rendered from bytes with scripts off and
    every request aborted.
    """

    @pytest.fixture
    def browser(self, monkeypatch: pytest.MonkeyPatch) -> _FakeBrowser:
        fake = _FakeBrowser()

        async def fake_ensure() -> _FakeBrowser:
            return fake

        monkeypatch.setattr(rp, "_HAS_PLAYWRIGHT", True)
        monkeypatch.setattr(rp, "_ensure_browser", fake_ensure)
        return fake

    @pytest.mark.asyncio
    async def test_html_is_rendered_from_bytes_not_file_url(
        self, tmp_path: Path, browser: _FakeBrowser
    ) -> None:
        f = tmp_path / "r.html"
        f.write_text('<iframe src="file:///etc/passwd"></iframe>', encoding="utf-8")

        png = await rp.render_file_to_png(f)

        assert png is not None
        assert browser.page.content is not None
        assert "file:///etc/passwd" in browser.page.content  # markup is inlined, not fetched
        assert browser.context.closed is True

    @pytest.mark.asyncio
    async def test_context_has_javascript_disabled(
        self, tmp_path: Path, browser: _FakeBrowser
    ) -> None:
        f = tmp_path / "r.html"
        f.write_text("<h1>hi</h1>", encoding="utf-8")

        await rp.render_file_to_png(f)

        assert browser.context_kwargs is not None
        assert browser.context_kwargs["java_script_enabled"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "file:///C:/Users/x/.codex/auth.json",
            "file:///home/x/.claude.json",
            "https://attacker.example/collect?d=1",
            "http://127.0.0.1:8080/api/tasks",
        ],
    )
    async def test_every_request_is_aborted(
        self, tmp_path: Path, browser: _FakeBrowser, url: str
    ) -> None:
        f = tmp_path / "r.html"
        f.write_text("<h1>hi</h1>", encoding="utf-8")

        await rp.render_file_to_png(f)

        assert browser.page.route_pattern == "**/*"
        assert browser.page.route_handler is not None
        route = _FakeRoute(url)
        await browser.page.route_handler(route)
        assert route.aborted is True
        assert route.continued is False

    @pytest.mark.asyncio
    async def test_svg_is_inlined_as_a_data_image(
        self, tmp_path: Path, browser: _FakeBrowser
    ) -> None:
        """SVG keeps working — embedded inline, where it can neither script nor fetch."""
        f = tmp_path / "d.svg"
        f.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', encoding="utf-8")

        png = await rp.render_file_to_png(f)

        assert png is not None
        assert browser.page.content is not None
        assert "data:image/svg+xml;base64," in browser.page.content
