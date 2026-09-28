"""Durable at-most-once receipts for owner speech sent to an EBI thread."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS spoken_receipts (
    request_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    message_ids TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass(frozen=True)
class SpokenReceipt:
    request_id: str
    thread_id: str
    payload_hash: str
    status: str
    message_ids: tuple[str, ...]
    error: str | None

    def public(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "status": self.status,
            "message_ids": list(self.message_ids),
            "error": self.error,
        }


def payload_hash(*, thread_id: str, speaker_id: str, text: str, mode: str, source: str) -> str:
    body = json.dumps([thread_id, speaker_id, text, mode, source], ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class SpokenReceiptRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def reserve(
        self, request_id: str, thread_id: str, fingerprint: str
    ) -> tuple[SpokenReceipt, bool]:
        async with aiosqlite.connect(self.db_path, timeout=10) as db:
            await db.execute(SCHEMA)
            cursor = await db.execute(
                "INSERT OR IGNORE INTO spoken_receipts"
                "(request_id, thread_id, payload_hash) VALUES(?, ?, ?)",
                (request_id, thread_id, fingerprint),
            )
            created = cursor.rowcount == 1
            await db.commit()
        receipt = await self.get(request_id)
        assert receipt is not None
        return receipt, created

    async def get(self, request_id: str) -> SpokenReceipt | None:
        async with aiosqlite.connect(self.db_path, timeout=10) as db:
            await db.execute(SCHEMA)
            async with db.execute(
                "SELECT request_id, thread_id, payload_hash, status, message_ids, error "
                "FROM spoken_receipts WHERE request_id = ?",
                (request_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return SpokenReceipt(row[0], row[1], row[2], row[3], tuple(json.loads(row[4])), row[5])

    async def mark_posted(self, request_id: str, message_ids: list[str]) -> None:
        await self._mark(request_id, "posted", json.dumps(message_ids), None)

    async def mark_failed(self, request_id: str, error: str) -> None:
        await self._mark(request_id, "failed", "[]", error[:200])

    async def _mark(
        self, request_id: str, status: str, message_ids: str, error: str | None
    ) -> None:
        async with aiosqlite.connect(self.db_path, timeout=10) as db:
            await db.execute(
                "UPDATE spoken_receipts SET status = ?, message_ids = ?, error = ?, "
                "updated_at = datetime('now') WHERE request_id = ? AND status = 'accepted'",
                (status, message_ids, error, request_id),
            )
            await db.commit()
