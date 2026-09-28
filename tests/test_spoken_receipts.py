import asyncio

import pytest

from claude_discord.database.spoken_receipts import SpokenReceiptRepository, payload_hash


@pytest.mark.asyncio
async def test_parallel_same_id_reserves_one_delivery_and_survives_reopen(tmp_path):
    path = str(tmp_path / "receipts.db")
    repo = SpokenReceiptRepository(path)
    fingerprint = payload_hash(
        thread_id="1554146845415055445",
        speaker_id="488763953397235712",
        text="Test this",
        mode="queue",
        source="voice",
    )
    results = await asyncio.gather(
        *[repo.reserve("jester-1", "1554146845415055445", fingerprint) for _ in range(5)]
    )
    assert sum(created for _, created in results) == 1
    await repo.mark_posted("jester-1", ["1554146845415055446"])
    await repo.mark_failed("jester-1", "later model error")
    saved = await SpokenReceiptRepository(path).get("jester-1")
    assert saved is not None
    assert saved.public()["status"] == "posted"
    assert saved.public()["message_ids"] == ["1554146845415055446"]


@pytest.mark.asyncio
async def test_failed_receipt_is_terminal_and_payload_mismatch_is_visible(tmp_path):
    repo = SpokenReceiptRepository(str(tmp_path / "receipts.db"))
    first, created = await repo.reserve("jester-2", "1554146845415055445", "hash-a")
    assert created and first.status == "accepted"
    await repo.mark_failed("jester-2", "Discord post failed")
    second, created = await repo.reserve("jester-2", "1554146845415055445", "hash-b")
    assert not created and second.payload_hash == "hash-a" and second.status == "failed"
