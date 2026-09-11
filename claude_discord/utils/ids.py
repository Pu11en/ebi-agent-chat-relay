"""Discord user-ID parsing shared by the launcher and ``setup_bridge``.

Kept in one place so authorization (``allowed_user_ids``) and thread
membership (``thread_member_ids``) parse the same way, and so a trailing
comma or a stray label cannot crash startup or silently widen access.
"""

from __future__ import annotations


def parse_user_ids(value: str) -> set[int]:
    """Parse a comma-separated list of Discord user IDs.

    Blank entries and non-numeric junk are ignored so a trailing comma in an
    env var cannot crash startup or silently widen access.
    """
    return {int(part) for part in (p.strip() for p in value.split(",")) if part.isdigit()}


def build_allowed_user_ids(owner_id: int | None, extra: str) -> set[int] | None:
    """Union the single owner with any additionally allowed Discord users.

    ``DISCORD_OWNER_ID`` stays singular because it is also the account the bot
    @mentions and invites into threads; this set is the *authorization*
    allowlist.  Returns ``None`` when nothing is configured, preserving the
    channel-permissions-only behaviour for trusted private servers.
    """
    allowed = parse_user_ids(extra)
    if owner_id is not None:
        allowed.add(owner_id)
    return allowed or None


def build_thread_member_ids(
    owner_id: int | None,
    allowed: set[int] | None,
    extra: str,
) -> set[int] | None:
    """Resolve who is auto-joined to every ccdb thread.

    Membership defaults to the whole authorization allowlist, so today's
    behaviour ("authorized users see the threads") needs no extra config.  An
    explicit ``extra`` list narrows it, which is the point: an operator can
    grant someone execution rights without dragging them into every thread.
    The owner is always included because the dashboard addresses them by name.
    """
    members = parse_user_ids(extra) if extra.strip() else set(allowed or ())
    if owner_id is not None:
        members.add(owner_id)
    return members or None
