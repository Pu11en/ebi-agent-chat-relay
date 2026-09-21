"""Durable blocker questions for Go Work builds (T20).

When a task cannot go on without a person, the build asks one question. That
question is written down here before it is posted, keyed to the build, task
and attempt it belongs to, with the Discord message it became and — later —
the reply that answered it. A restart therefore never loses a question, never
asks it twice, and never attaches an answer to the wrong build.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

MAX_QUESTION_CHARS = 1500


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Blocker:
    blocker_id: str
    build_id: str
    task_id: str
    attempt_id: str
    plan_id: str
    plan_version: int
    channel_id: int
    question: str
    created_at: str
    message_id: int | None = None
    posted_channel_id: int | None = None
    resolved: bool = False
    answer: str | None = None
    resolved_by: int | None = None
    reply_message_id: int | None = None
    resolved_at: str | None = None

    def to_json(self) -> dict:
        return {
            "blocker_id": self.blocker_id,
            "build_id": self.build_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "channel_id": self.channel_id,
            "question": self.question,
            "created_at": self.created_at,
            "message_id": self.message_id,
            "posted_channel_id": self.posted_channel_id,
            "resolved": self.resolved,
            "answer": self.answer,
            "resolved_by": self.resolved_by,
            "reply_message_id": self.reply_message_id,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_json(cls, value: dict) -> Blocker:
        return cls(
            blocker_id=str(value["blocker_id"]),
            build_id=str(value["build_id"]),
            task_id=str(value["task_id"]),
            attempt_id=str(value["attempt_id"]),
            plan_id=str(value.get("plan_id", "")),
            plan_version=int(value.get("plan_version", 0)),
            channel_id=int(value["channel_id"]),
            question=str(value["question"]),
            created_at=str(value.get("created_at", "")),
            message_id=int(value["message_id"]) if value.get("message_id") is not None else None,
            posted_channel_id=(
                int(value["posted_channel_id"])
                if value.get("posted_channel_id") is not None
                else None
            ),
            resolved=bool(value.get("resolved", False)),
            answer=value.get("answer"),
            resolved_by=int(value["resolved_by"]) if value.get("resolved_by") is not None else None,
            reply_message_id=(
                int(value["reply_message_id"])
                if value.get("reply_message_id") is not None
                else None
            ),
            resolved_at=value.get("resolved_at"),
        )


def blocker_id_for(attempt_id: str) -> str:
    """One question per attempt: the attempt's own identity names it."""
    return "blocker:" + re.sub(r"[^A-Za-z0-9._:-]+", "_", attempt_id)


class BlockerLedger:
    """All builds' open and answered questions, in one JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    # -- reading -------------------------------------------------------------

    def _load(self) -> dict[str, Blocker]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        items = raw.get("blockers", []) if isinstance(raw, dict) else []
        result: dict[str, Blocker] = {}
        for item in items:
            try:
                blocker = Blocker.from_json(item)
            except (KeyError, ValueError, TypeError):
                continue
            result[blocker.blocker_id] = blocker
        return result

    def get(self, blocker_id: str) -> Blocker | None:
        return self._load().get(blocker_id)

    def by_message(self, message_id: int) -> Blocker | None:
        return next((b for b in self._load().values() if b.message_id == message_id), None)

    def unresolved(self, build_id: str | None = None) -> tuple[Blocker, ...]:
        return tuple(
            b
            for b in self._load().values()
            if not b.resolved and (build_id is None or b.build_id == build_id)
        )

    def unposted(self, build_id: str | None = None) -> tuple[Blocker, ...]:
        """Questions that exist but never became a Discord message (post these on recovery)."""
        return tuple(b for b in self.unresolved(build_id) if b.message_id is None)

    # -- writing -------------------------------------------------------------

    def open(
        self,
        build_id: str,
        task_id: str,
        attempt_id: str,
        *,
        plan_id: str,
        plan_version: int,
        channel_id: int,
        question: str,
    ) -> Blocker:
        """Record a question; the same attempt's question is returned, not duplicated."""
        blockers = self._load()
        blocker_id = blocker_id_for(attempt_id)
        existing = blockers.get(blocker_id)
        if existing is not None and not existing.resolved:
            return existing
        blocker = Blocker(
            blocker_id=blocker_id,
            build_id=build_id,
            task_id=task_id,
            attempt_id=attempt_id,
            plan_id=plan_id,
            plan_version=int(plan_version),
            channel_id=int(channel_id),
            question=question.strip()[:MAX_QUESTION_CHARS],
            created_at=_now(),
        )
        blockers[blocker_id] = blocker
        self._write(blockers)
        return blocker

    def note_posted(self, blocker_id: str, *, channel_id: int, message_id: int) -> Blocker | None:
        blockers = self._load()
        blocker = blockers.get(blocker_id)
        if blocker is None:
            return None
        updated = replace(blocker, message_id=int(message_id), posted_channel_id=int(channel_id))
        blockers[blocker_id] = updated
        self._write(blockers)
        return updated

    def resolve(
        self, blocker_id: str, *, answer: str, by_user_id: int, reply_message_id: int
    ) -> bool:
        """Answer a question once. A second answer is refused (False)."""
        blockers = self._load()
        blocker = blockers.get(blocker_id)
        if blocker is None or blocker.resolved:
            return False
        blockers[blocker_id] = replace(
            blocker,
            resolved=True,
            answer=answer.strip()[:MAX_QUESTION_CHARS],
            resolved_by=int(by_user_id),
            reply_message_id=int(reply_message_id),
            resolved_at=_now(),
        )
        self._write(blockers)
        return True

    def _write(self, blockers: dict[str, Blocker]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"blockers": [b.to_json() for b in blockers.values()]}, stream, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
