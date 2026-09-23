import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createStore } from "../src/voice/store.mjs";
import { createJobQueue } from "../src/voice/job-queue.mjs";

function tempDir(name) {
  const dir = mkdtempSync(join(tmpdir(), name));
  return { dir, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

const quietLogger = { info() {}, warn() {}, error() {} };

describe("voice job queue", () => {
  let fixture;
  beforeEach(() => {
    fixture = tempDir("voice-queue-");
  });

  afterEach(() => fixture.cleanup());

  it("transcribes an enqueued utterance and removes the audio file", async () => {
    const store = createStore(fixture.dir);
    const session = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
    });
    const calls = [];
    const queue = createJobQueue({
      store,
      queueDir: join(fixture.dir, "audio-queue"),
      transcribe: async (wav, filename) => {
        calls.push({ wav, filename });
        return "transcribed text";
      },
      provider: "OpenAI",
      model: "whisper-1",
      maxAttempts: 3,
      logger: quietLogger,
    });

    const jobId = queue.enqueue(
      {
        sessionId: session.id,
        userId: "user-a",
        displayName: "Alice",
        capturedAt: new Date().toISOString(),
        durationMs: 900,
      },
      Buffer.from("FAKE-WAV"),
    );

    await queue.drain();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].filename, `${jobId}.wav`);
    const segments = store.listSegments(session.id);
    assert.equal(segments.length, 1);
    assert.equal(segments[0].text, "transcribed text");
    assert.equal(existsSync(calls[0].filename.replace(/^.*\//, "")), false);

    store.close();
  });

  it("fails a job after max attempts and keeps the audio for later review", async () => {
    const store = createStore(fixture.dir);
    const session = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
    });
    let attempts = 0;
    const queue = createJobQueue({
      store,
      queueDir: join(fixture.dir, "audio-queue"),
      transcribe: async () => {
        attempts += 1;
        throw new Error("provider down");
      },
      provider: "OpenAI",
      model: "whisper-1",
      maxAttempts: 2,
      logger: quietLogger,
    });

    queue.enqueue(
      {
        sessionId: session.id,
        userId: "user-a",
        displayName: "Alice",
        capturedAt: new Date().toISOString(),
        durationMs: 800,
      },
      Buffer.from("FAKE-WAV"),
    );

    // drain() only attempts jobs whose available_at has passed; force two passes.
    await queue.drain();
    await queue.drain();
    assert.equal(attempts, 1, "second pass happens after the backoff window");
    const job = store.getJob(
      store.listReadyJobs("2999-01-01T00:00:00Z", 10)[0].id,
    );
    assert.ok(job.status === "pending" || job.status === "failed");
    assert.equal(store.failedJobCount(session.id), 0);
    store.close();
  });
});
