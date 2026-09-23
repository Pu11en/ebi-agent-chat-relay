"""How a session ends itself when the person in the thread says to.

"Close this session" is said to the agent, in the thread — but closing was a
slash command only, so the agent had to answer "I can't" and the person had to
do it by hand. The capability is `POST /api/threads/{id}/close`, which records
the close as :class:`CloseAuthority.USER_INSTRUCTION` and carries the actor who
asked; this module is the other half, the one sentence that tells the session
the endpoint exists. A capability nothing mentions is a capability nobody uses.

The hint is deliberately tiny and injected per turn, and it states the one
condition that matters: the person has to have asked. The endpoint enforces the
same thing — it refuses a close with no actor — so the hint cannot be talked
into a close the service would not accept.
"""

from __future__ import annotations

_TEMPLATE = """\
## Closing this session
When the person asks you to close, end, or wrap up this session, do it yourself \
as your last action — do not tell them to run a command:
```bash
curl -s -X POST "$CCDB_API_URL/api/threads/{thread_id}/close" \\
  -H "Authorization: Bearer $CCDB_API_SECRET" -H "Content-Type: application/json" \\
  -d '{{"actor": "{actor_id}"}}'
```
It answers `pending` while your turn is still running and finishes the moment it \
ends: a wrap-up is written and the thread is archived. Nothing is deleted and the \
conversation stays resumable. Only when they ask — their instruction is the \
authority, and the endpoint refuses a close without it."""


def build_close_hint(*, thread_id: int, actor_id: int | str) -> str:
    """The per-turn hint naming this thread's close endpoint and its authority."""
    return _TEMPLATE.format(thread_id=int(thread_id), actor_id=actor_id)
