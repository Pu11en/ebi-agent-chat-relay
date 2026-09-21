"""HTML/SVG → PNG previews for Discord inline display.

Discord natively inlines images but never HTML/SVG. So when a bot
hands us one of those, we render it with a headless browser and post the PNG
alongside the original file. Consumers get the visual, and the raw file stays
downloadable for anyone who wants the interactive version.

Markdown is deliberately *not* rendered: Discord previews a ``.md`` attachment
inline as expandable, scrollable text, and a screenshot of it is harder to read
and cannot be scrolled or copied. Plans (``*.plan.json``) are turned into
Markdown by ``file_sender`` for the same reason.

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

_RENDERABLE_EXTENSIONS = {".html", ".htm", ".svg"}

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
    """True when *filename* is an HTML/SVG document worth an inline PNG preview."""
    return Path(filename).suffix.lower() in _RENDERABLE_EXTENSIONS


def preview_name(filename: str) -> str:
    """Return the preview PNG filename that pairs with *filename*.

    ``docs/dash.svg`` → ``docs/dash.preview.png``.
    """
    p = Path(filename)
    return p.with_name(f"{p.stem}.preview.png").as_posix()


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
    browser = await _ensure_browser()
    if browser is None:
        return None

    try:
        page = await browser.new_page(  # type: ignore[attr-defined]
            viewport={"width": _PREVIEW_WIDTH, "height": _PREVIEW_HEIGHT},
            device_scale_factor=2,
        )
        try:
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
