"""HTML/SVG → PNG previews for Discord inline display.

Discord natively inlines images but never HTML/SVG. So when a bot
hands us one of those, we render it with a headless browser and post the PNG
alongside the original file. Consumers get the visual, and the raw file stays
downloadable for anyone who wants the interactive version.

Markdown is deliberately *not* rendered: Discord previews a ``.md`` attachment
inline as expandable, scrollable text, and a screenshot of it is harder to read
and cannot be scrolled or copied. Plans (``*.plan.json``) are turned into
Markdown by ``file_sender`` for the same reason.

The document is untrusted — it was written by a model session that may have
been prompt-injected — so it is rendered as a sealed picture, never as a page
with privileges. The bytes are handed to ``page.set_content`` (never
``goto(file://…)``, which would let ``<iframe src="file:///…/.codex/auth.json">``
read any local file into the PNG), JavaScript is disabled on the context, and a
catch-all route aborts every request so neither ``file://`` nor ``http(s)://``
subresources are fetched. Inline ``data:`` content still renders.

Playwright + Chromium are the render engine. Both are optional at runtime:
if either is missing (fresh install, headless server without the browser
download) this module degrades to no-op so file delivery still works.
Install the browser once per host with::

    python -m playwright install chromium
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from pathlib import Path
from typing import Any

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


async def _abort_request(route: Any) -> None:
    """Refuse every request the document tries to make.

    The document is rendered from bytes, so it has no legitimate reason to
    fetch anything: a ``file://`` subresource is a local-file read, an
    ``http(s)://`` one is an egress channel. Inline ``data:`` content never
    reaches the network layer and is unaffected.
    """
    await route.abort("blockedbyclient")


def _inline_document(filename: str, raw: bytes) -> str:
    """Return the HTML handed to ``set_content`` for *raw*.

    HTML is passed through as text. SVG is wrapped as a ``data:`` image: an
    SVG inside ``<img>`` can run no script and load no external resource,
    which is exactly the sandbox we want, and it scales to the viewport.
    """
    if Path(filename).suffix.lower() == ".svg":
        encoded = base64.b64encode(raw).decode("ascii")
        return (
            "<!doctype html><html><body style='margin:0'>"
            f"<img src='data:image/svg+xml;base64,{encoded}' "
            "style='display:block;max-width:100%'></body></html>"
        )
    return raw.decode("utf-8", errors="replace")


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
    try:
        raw = source.read_bytes()
    except OSError:
        logger.info("Preview source unreadable: %s", source, exc_info=True)
        return None
    browser = await _ensure_browser()
    if browser is None:
        return None

    markup = _inline_document(source.name, raw)
    try:
        # A fresh context per render: no cookies, no scripts, nothing shared
        # with the previous session's document.
        context = await browser.new_context(  # type: ignore[attr-defined]
            viewport={"width": _PREVIEW_WIDTH, "height": _PREVIEW_HEIGHT},
            device_scale_factor=2,
            java_script_enabled=False,
            offline=True,
        )
        try:
            page = await context.new_page()
            await page.route("**/*", _abort_request)
            await page.set_content(markup, wait_until="load", timeout=_PREVIEW_TIMEOUT_MS)
            png: bytes = await page.screenshot(full_page=True, type="png")
        finally:
            with contextlib.suppress(Exception):
                await context.close()
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
