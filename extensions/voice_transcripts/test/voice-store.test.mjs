import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createStore, renderTranscript } from "../src/voice/store.mjs";

function tempStore() {
  const dir = mkdtempSync(join(tmpdir(), "voice-store-"));
  const store = createStore(dir);
  return { store, dir };
}

describe("voice store", () => {
  let fixture;
  beforeEach(() => {
    fixture = tempStore();
  });

  afterEach(() => {
    if (fixture?.store) fixture.store.close();
    if (fixture?.dir) rmSync(fixture.dir, { recursive: true, force: true });
  });

  it("creates and reopens an active session, and persists across a fresh handle", () => {
    const { store, dir } = fixture;
    const session = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
      disclosureMessageId: "msg-1",
    });
    assert.equal(store.getActiveSession("guild-1").id, session.id);
    assert.equal(store.getActiveSession("guild-1").status, "recording");

    store.close();
    const reopened = createStore(dir);
    const active = reopened.getActiveSession("guild-1");
    assert.equal(active.id, session.id, "active session survives a restart");
    assert.equal(active.channel_name, "chill");
    reopened.close();
    fixture.store = null;
  });

  it("records a segment once a queued job completes, in capture order", () => {
    const { store } = fixture;
    const session = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
    });
    const first = new Date("2026-09-01T00:00:00Z");
    store.enqueueJob({
      id: "job-1",
      sessionId: session.id,
      userId: "user-a",
      displayName: "Alice",
      capturedAt: first.toISOString(),
      durationMs: 1200,
      audioPath: "/tmp/job-1.wav",
      createdAt: first.toISOString(),
    });
    store.completeJob("job-1", {
      text: "hello there",
      provider: "OpenAI",
      model: "whisper-1",
    });

    const segments = store.listSegments(session.id);
    assert.equal(segments.length, 1);
    assert.equal(segments[0].text, "hello there");
    assert.equal(segments[0].user_id, "user-a");
    assert.equal(segments[0].display_name, "Alice");
    assert.equal(store.pendingJobCount(session.id), 0);
    assert.equal(store.getJob("job-1"), null);
  });

  it("tracks attempts and marks a job failed past max attempts", () => {
    const { store } = fixture;
    const session = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
    });
    store.enqueueJob({
      id: "job-1",
      sessionId: session.id,
      userId: "user-a",
      displayName: "Alice",
      capturedAt: new Date().toISOString(),
      durationMs: 800,
      audioPath: "/tmp/job-1.wav",
      createdAt: new Date().toISOString(),
    });
    store.failJob("job-1", "boom", {
      maxAttempts: 2,
      nextAttemptAt: new Date().toISOString(),
    });
    assert.equal(store.getJob("job-1").attempts, 1);
    assert.equal(store.getJob("job-1").status, "pending");
    store.failJob("job-1", "boom again", {
      maxAttempts: 2,
      nextAttemptAt: new Date().toISOString(),
    });
    assert.equal(store.getJob("job-1").status, "failed");
    assert.match(store.getJob("job-1").last_error, /boom/);
  });

  it("stops a session and then deletes it with its segments", () => {
    const { store } = fixture;
    const session = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
    });
    store.enqueueJob({
      id: "job-1",
      sessionId: session.id,
      userId: "user-a",
      displayName: "Alice",
      capturedAt: new Date().toISOString(),
      durationMs: 500,
      audioPath: "/tmp/job-1.wav",
      createdAt: new Date().toISOString(),
    });
    store.completeJob("job-1", {
      text: "bye",
      provider: "OpenAI",
      model: "whisper-1",
    });

    store.stopSession(session.id);
    const stopped = store.getSession(session.id, "guild-1");
    assert.equal(stopped.status, "stopped");
    assert.ok(stopped.ended_at);

    const result = store.deleteSession(session.id, "guild-1");
    assert.equal(result.deleted, true);
    assert.equal(result.audioPaths.length, 0);
    assert.equal(store.getSession(session.id, "guild-1"), null);
    assert.equal(store.listSegments(session.id).length, 0);
  });

  it("prunes only stopped sessions older than the cutoff", () => {
    const { store } = fixture;
    const old = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
      now: "2026-07-01T00:00:00Z",
    });
    const fresh = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
      now: "2026-08-25T00:00:00Z",
    });
    const active = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
      now: "2026-08-30T00:00:00Z",
    });
    store.stopSession(old.id, "2026-07-01T01:00:00Z");
    store.stopSession(fresh.id, "2026-08-25T01:00:00Z");

    const result = store.prune("guild-1", "2026-08-01T00:00:00Z");
    assert.equal(result.deletedSessions, 1);
    assert.equal(store.getSession(old.id, "guild-1"), null);
    assert.ok(store.getSession(fresh.id, "guild-1"));
    assert.ok(store.getSession(active.id, "guild-1"));
  });

  it("renders a readable markdown transcript with pending/failed counts", () => {
    const { store } = fixture;
    const session = store.createSession({
      guildId: "guild-1",
      channelId: "voice-1",
      channelName: "chill",
      startedBy: "admin-1",
    });
    store.enqueueJob({
      id: "job-1",
      sessionId: session.id,
      userId: "user-a",
      displayName: "Alice",
      capturedAt: "2026-09-01T01:02:03.000Z",
      durationMs: 1000,
      audioPath: "/tmp/a.wav",
      createdAt: "2026-09-01T01:02:03.000Z",
    });
    store.enqueueJob({
      id: "job-2",
      sessionId: session.id,
      userId: "user-b",
      displayName: "Bob",
      capturedAt: "2026-09-01T01:02:10.000Z",
      durationMs: 1000,
      audioPath: "/tmp/b.wav",
      createdAt: "2026-09-01T01:02:10.000Z",
    });
    store.completeJob("job-1", {
      text: "first line",
      provider: "OpenAI",
      model: "whisper-1",
    });

    const markdown = renderTranscript(session, store.listSegments(session.id), {
      pending: 1,
      failed: 1,
    });
    assert.match(markdown, /# Voice transcript/);
    assert.match(markdown, /Session: `[0-9a-f-]{36}`/);
    assert.match(markdown, /Channel: chill/);
    assert.match(markdown, /Pending transcription jobs: 1/);
    assert.match(markdown, /Failed transcription jobs: 1/);
    assert.match(markdown, /\*\*01:02:03 — Alice:\*\* first line/);
    assert.match(markdown, /Pending transcription jobs: 1/);
    assert.doesNotMatch(markdown, /_No completed transcript segments\._/);
  });
});
