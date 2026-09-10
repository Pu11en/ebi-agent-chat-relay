"""HTML/SVG/Markdown → PNG previews for Discord inline display.

Discord natively inlines images but never HTML/SVG/Markdown. So when a bot
hands us one of those, we render it with a headless browser and post the PNG
alongside the original file. Consumers get the visual, and the raw file stays
downloadable for anyone who wants the interactive version.

Playwright + Chromium are the render engine. Both are optional at runtime:
if either is missing (fresh install, headless server without the browser
download) this module degrades to no-op so file delivery still works.
Install the browser once per host with::

    python -m playwright install chromium
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_RENDERABLE_EXTENSIONS = {".html", ".htm", ".svg", ".md", ".markdown"}
_PLAN_CARD_SUFFIX = ".plan.json"

try:
    from playwright.async_api import async_playwright  # noqa: F401

    _HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    _HAS_PLAYWRIGHT = False

_PREVIEW_MAX_BYTES = 8 * 1024 * 1024
_PREVIEW_WIDTH = 800
_PREVIEW_HEIGHT = 900
_PREVIEW_TIMEOUT_MS = 8000

_browser_lock = asyncio.Lock()
_browser: object | None = None
_playwright_ctx: object | None = None


def is_renderable(filename: str) -> bool:
    """True when *filename* can be turned into an inline PNG preview.

    Covers the HTML/SVG/Markdown documents the browser renders directly, plus
    ``*.plan.json`` planning cards routed through :mod:`plan_card`.
    """
    lower = filename.lower()
    if lower.endswith(_PLAN_CARD_SUFFIX):
        return True
    return Path(filename).suffix.lower() in _RENDERABLE_EXTENSIONS


def preview_name(filename: str) -> str:
    """Return the preview PNG filename that pairs with *filename*.

    ``docs/dash.svg`` → ``docs/dash.preview.png``; ``x.plan.json`` → ``x.plan.png``.
    """
    if filename.lower().endswith(_PLAN_CARD_SUFFIX):
        return filename[: -len(_PLAN_CARD_SUFFIX)] + ".plan.png"
    p = Path(filename)
    return str(p.with_name(f"{p.stem}.preview.png"))


def _markdown_to_html(source: str) -> str:
    """Wrap raw Markdown in a minimal styled HTML document.

    Uses ``markdown`` if available, otherwise a naive line-oriented fallback so
    the preview still renders something useful. Styling matches Discord's dark
    theme so a rendered doc doesn't look like a white flash in the channel.
    """
    try:
        import markdown as _md

        body = _md.markdown(source, extensions=["fenced_code", "tables"])
    except ImportError:
        from html import escape

        body = "<pre>" + escape(source) + "</pre>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{font-family:-apple-system,Segoe UI,sans-serif;background:#1e1f22;"
        "color:#dbdee1;margin:0;padding:24px;max-width:760px}"
        "h1,h2,h3{color:#f2f3f5}code,pre{background:#2b2d31;padding:2px 6px;"
        "border-radius:4px;font-family:ui-monospace,Menlo,monospace;font-size:13px}"
        "pre{padding:12px;overflow-x:auto}table{border-collapse:collapse;"
        "background:#2b2d31}th,td{padding:6px 12px;border:1px solid #1e1f22}"
        "a{color:#00a8fc}img{max-width:100%}</style></head><body>" + body + "</body></html>"
    )


async def _ensure_browser() -> object | None:
    """Launch chromium once and cache it. Returns None if unavailable."""
    global _browser, _playwright_ctx
    if not _HAS_PLAYWRIGHT:
        return None
    async with _browser_lock:
        if _browser is not None:
            return _browser
        try:
            from playwright.async_api import async_playwright

            _playwright_ctx = await async_playwright().start()
            _browser = await _playwright_ctx.chromium.launch()  # type: ignore[attr-defined]
        except Exception:
            logger.warning(
                "Playwright chromium unavailable — HTML/SVG previews disabled. "
                "Install with: python -m playwright install chromium",
                exc_info=True,
            )
            _browser = None
            _playwright_ctx = None
    return _browser


async def render_file_to_png(source: Path) -> bytes | None:
    """Render *source* to a PNG byte string, or None if we can't.

    Never raises: any failure (missing file, non-renderable extension, missing
    playwright/browser, rendering exception) returns None so the file-delivery
    caller can fall back to sending the raw file alone.
    """
    if not source.exists() or not source.is_file():
        return None
    if not is_renderable(source.name):
        return None
    if source.name.lower().endswith(_PLAN_CARD_SUFFIX):
        from claude_discord.discord_ui.plan_card import render_plan_card_to_png

        return await render_plan_card_to_png(source)
    browser = await _ensure_browser()
    if browser is None:
        return None

    try:
        page = await browser.new_page(  # type: ignore[attr-defined]
            viewport={"width": _PREVIEW_WIDTH, "height": _PREVIEW_HEIGHT},
            device_scale_factor=2,
        )
        try:
            suffix = source.suffix.lower()
            if suffix in {".md", ".markdown"}:
                html = _markdown_to_html(source.read_text(encoding="utf-8", errors="replace"))
                await page.set_content(html, wait_until="networkidle", timeout=_PREVIEW_TIMEOUT_MS)
            else:
                await page.goto(source.resolve().as_uri(), timeout=_PREVIEW_TIMEOUT_MS)
                await page.wait_for_load_state("networkidle", timeout=_PREVIEW_TIMEOUT_MS)
            png: bytes = await page.screenshot(full_page=True, type="png")
        finally:
            with contextlib.suppress(Exception):
                await page.close()
    except Exception:
        logger.info("Preview render failed for %s", source, exc_info=True)
        return None

    if len(png) > _PREVIEW_MAX_BYTES:
        logger.info(
            "Preview PNG too large (%d bytes) for %s — skipping inline display",
            len(png),
            source,
        )
        return None
    return png


async def shutdown() -> None:
    """Close the cached browser. Safe to call multiple times."""
    global _browser, _playwright_ctx
    async with _browser_lock:
        if _browser is not None:
            with contextlib.suppress(Exception):
                await _browser.close()  # type: ignore[attr-defined]
            _browser = None
        if _playwright_ctx is not None:
            with contextlib.suppress(Exception):
                await _playwright_ctx.stop()  # type: ignore[attr-defined]
            _playwright_ctx = None
