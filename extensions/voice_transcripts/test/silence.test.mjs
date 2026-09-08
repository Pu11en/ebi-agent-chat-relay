import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createStore } from "../src/voice/store.mjs";
import { createJobQueue } from "../src/voice/job-queue.mjs";
test("silence consumes its queue job without a blank segment or retained audio", async () => {
  const dir = mkdtempSync(join(tmpdir(), "voice-silence-"));
  const store = createStore(dir);
  const session = store.createSession({
    guildId: "g",
    channelId: "v",
    channelName: "room",
    startedBy: "o",
  });
  const queue = createJobQueue({
    store,
    queueDir: join(dir, "audio"),
    transcribe: async () => "",
    provider: "local",
    model: "base.en",
    maxAttempts: 5,
    logger: { info() {}, error() {}, warn() {} },
  });
  try {
    const id = queue.enqueue(
      {
        sessionId: session.id,
        userId: "u",
        displayName: "Alice",
        capturedAt: new Date().toISOString(),
        durationMs: 1000,
      },
      Buffer.from("silent audio"),
    );
    await queue.drain();
    assert.equal(store.listSegments(session.id).length, 0);
    assert.equal(store.pendingJobCount(session.id), 0);
    assert.equal(existsSync(join(dir, "audio", id + ".wav")), false);
  } finally {
    await queue.close();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
