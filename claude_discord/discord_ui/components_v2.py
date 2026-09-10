"""Discord Components v2 envelope for bot file deliveries.

The default "here are your files" post used to be plain text plus attachments —
useful only if the user was willing to download and open each file. This module
wraps the same delivery in a Container v2 layout so the message reads as one
card: header, short body, an inline preview gallery (when :mod:`render_preview`
produced PNG twins), and the file list attached below.

The layout is intentionally minimal — no interaction handlers, no persistent
state. The consumer builds a :class:`_FileDeliveryView`, passes
``view.attached_files`` to ``thread.send(files=..., view=view, flags=...)``,
and Discord renders the whole card as one message.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import MediaGalleryItem
from discord.ui import Container, LayoutView, MediaGallery, TextDisplay

_MAX_HEADER = 200
_MAX_BODY = 3800
_TRUNCATION_SUFFIX = "\n… (truncated)"


@dataclass(frozen=True)
class RenderedFile:
    """One file for delivery, optionally paired with a PNG preview.

    Attributes:
        file: The original file the user asked for (any type).
        preview: A PNG rendering of *file* if one was produced by
            :mod:`render_preview`; otherwise ``None``.
    """

    file: discord.File
    preview: discord.File | None


class _FileDeliveryView(LayoutView):
    """A LayoutView that also remembers which files should be uploaded with it.

    Callers pass ``view.attached_files`` to ``thread.send(files=...)`` so the
    MediaGallery ``attachment://`` refs resolve against real uploads.
    """

    def __init__(self, files_to_upload: list[discord.File]) -> None:
        super().__init__(timeout=None)
        self.attached_files = files_to_upload


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def build_file_delivery_view(
    *,
    header: str,
    body: str,
    files: list[RenderedFile],
    accent: int = 0x5865F2,
) -> _FileDeliveryView:
    """Wrap *files* in a Components v2 Container for inline display.

    The returned view carries an ``attached_files`` attribute listing every
    original file plus every preview PNG; pass it as ``files=`` to
    ``thread.send`` alongside ``view=`` and
    ``flags=discord.MessageFlags(components_v2=True)``.

    Args:
        header: One-line title shown at the top of the container.
        body: Optional description, truncated to fit the container payload
            budget.
        files: Files to deliver. Each may carry a rendered PNG preview.
        accent: Left-edge accent colour for the container.

    Raises:
        ValueError: When *files* is empty — an envelope with no payload is a
            programming error at the call site.
    """
    if not files:
        raise ValueError("build_file_delivery_view requires at least one file")

    header = _clip(header.strip() or "Files", _MAX_HEADER)
    body = _clip(body.strip(), _MAX_BODY)

    previews = [rf.preview for rf in files if rf.preview is not None]

    container_children: list = [TextDisplay(f"### {header}")]
    if body:
        container_children.append(TextDisplay(body))
    if previews:
        container_children.append(
            MediaGallery(
                *[
                    MediaGalleryItem(f"attachment://{p.filename}", description=p.filename)
                    for p in previews
                ]
            )
        )
    names = "\n".join(f"• `{rf.file.filename}`" for rf in files)
    container_children.append(TextDisplay(f"**Attached files**\n{names}"))

    all_files: list[discord.File] = []
    for rf in files:
        all_files.append(rf.file)
        if rf.preview is not None:
            all_files.append(rf.preview)

    view = _FileDeliveryView(all_files)
    view.add_item(Container(*container_children, accent_colour=accent))
    return view
