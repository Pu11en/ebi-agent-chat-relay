import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createVoiceController } from "../src/control/controller.mjs";
import { createStore } from "../src/voice/store.mjs";
import { createJobQueue } from "../src/voice/job-queue.mjs";

const start = Date.parse("2026-09-27T16:35:49.802Z");
const sessions = [
  { thread_id: "1", voice_label: "zoro", voice_label_aliases: ["zorro"] },
  { thread_id: "2", voice_label: "nami", voice_label_aliases: [] },
];
const speech = (offset, durationMs, extra = {}) => ({
  capturedAt: new Date(start + offset).toISOString(), durationMs, ...extra,
});

function rig({ announce = async () => "message-1", getPendingSpeech = null } = {}) {
  let clock = start;
  let unfinished = [];
  const timers = new Set();
  const sent = [];
  const queries = [];
  const controller = createVoiceController({
    ownerId: "42", enabled: true, now: () => clock,
    client: { listSessions: async () => sessions, sendSpoken: async (p) => sent.push(p) },
    announce, revise: async () => {}, logger: { info() {}, warn() {}, error() {} },
    getPendingSpeech: (scope) => {
      queries.push(scope);
      return getPendingSpeech ? getPendingSpeech(scope) : unfinished;
    },
    setTimer: (fn, delay) => {
      const timer = { fn, at: clock + delay };
      timers.add(timer);
      return timer;
    },
    clearTimer: (timer) => timers.delete(timer),
  });
  return {
    sent, queries, controller,
    pending: (items) => { unfinished = items; },
    // Move the wall clock before delivering a transcript, without assuming its
    // arrival has any relationship to when the speaker stopped.
    clock: (offset) => { clock = start + offset; },
    hear: (text, meta) => controller.handleUtterance({
      userId: "42", sessionId: "recording-1", text, ...meta,
    }),
    async tick(offset) {
      clock = start + offset;
      for (const timer of [...timers]) {
        if (timer.at <= clock) {
          timers.delete(timer);
          await timer.fn();
        }
      }
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

test("the reported 1.788-second continuation waits for its delayed transcript", async () => {
  const r = rig();
  r.pending([speech(31_788, 2740)]);
  r.clock(43_561);
  await r.hear("Okay Zorro create a separate website section.", speech(0, 30_000));
  await r.tick(44_000);
  assert.equal(r.sent.length, 0, "the last sentence is still in transcription");
  assert.deepEqual(r.queries.at(-1), { sessionId: "recording-1", userId: "42" });

  r.pending([]);
  r.clock(47_244);
  await r.hear("They can go to our website and look at that section.", speech(31_788, 2740));
  await r.tick(47_244);
  assert.equal(r.sent.length, 1);
  assert.equal(r.sent[0].threadId, "1");
  assert.equal(r.sent[0].text,
    "create a separate website section. They can go to our website and look at that section.");
  await r.tick(70_000);
  assert.equal(r.sent.length, 1, "no duplicate send from an old timer");
});

test("continued active capture holds the run even before an audio job exists", async () => {
  const r = rig();
  r.clock(3000);
  await r.hear("zoro keep the whole request", speech(0, 3000));
  r.pending([speech(5000, 0, { active: true })]);
  await r.tick(13_000);
  await r.tick(20_000);
  assert.equal(r.sent.length, 0);
  r.pending([]);
  r.clock(21_000);
  await r.hear("including this last bit", speech(5000, 15_000));
  await r.tick(29_999);
  assert.equal(r.sent.length, 0);
  await r.tick(30_000);
  assert.equal(r.sent.length, 1);
  assert.match(r.sent[0].text, /including this last bit/);
});

test("a delayed named request after a real ten-second pause gets its own destination", async () => {
  const r = rig();
  r.pending([speech(20_000, 3000)]);
  r.clock(40_000);
  await r.hear("zoro first task please", speech(0, 3000));
  // Simulate both callbacks completing in one event-loop turn before an
  // overdue timer has had a chance to fire.
  r.pending([]);
  await r.hear("nami second task please", speech(20_000, 3000));
  await r.tick(40_000);
  assert.deepEqual(r.sent.map((p) => [p.threadId, p.text]), [
    ["1", "first task please"], ["2", "second task please"],
  ]);
});

test("later unaddressed speech after a real pause is not appended to the old request", async () => {
  const r = rig();
  r.clock(40_000);
  await r.hear("zoro first task please", speech(0, 3000));
  await r.hear("talking to somebody else now", speech(20_000, 3000));
  await r.tick(40_000);
  assert.equal(r.sent.length, 1);
  assert.equal(r.sent[0].text, "first task please");
});

test("an unrelated future capture does not postpone a completed request", async () => {
  const r = rig();
  r.pending([speech(20_000, 0, { active: true })]);
  r.clock(40_000);
  await r.hear("zoro first task please", speech(0, 3000));
  await r.tick(40_000);
  assert.equal(r.sent.length, 1);
});

test("slow acknowledgment cannot clear the run under the transcript callback", async () => {
  let finishAnnouncement;
  let announcements = 0;
  const r = rig({ announce: () => ++announcements === 1
    ? new Promise((resolve) => { finishAnnouncement = resolve; })
    : Promise.resolve("another-message") });
  r.clock(40_000);
  const handling = r.hear("zoro first task please", speech(0, 3000));
  await new Promise((resolve) => setImmediate(resolve));
  await r.tick(40_000);
  finishAnnouncement("message-1");
  await assert.doesNotReject(handling);
  await r.tick(40_000);
  assert.equal(r.sent.length, 1);
});

test("real queue/store callbacks hold delayed words without a fake pending list", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "voice-delayed-"));
  const store = createStore(dir);
  const recording = store.createSession({
    guildId: "guild", channelId: "voice", channelName: "room", startedBy: "42",
  });
  const r = rig({ getPendingSpeech: ({ sessionId, userId }) =>
    store.listPendingSpeech(sessionId, userId) });
  const releases = [];
  let completed = 0;
  const queue = createJobQueue({
    store, queueDir: join(dir, "audio"), provider: "offline-fake", model: "none",
    maxAttempts: 1, logger: { info() {}, warn() {}, error() {} },
    transcribe: () => new Promise((resolve) => releases.push(resolve)),
    onTranscript: async (utterance) => {
      await r.controller.handleUtterance(utterance);
      completed++;
    },
  });
  t.after(async () => {
    for (const release of releases) release("");
    await queue.close();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });
  const enqueue = (offset, durationMs) => queue.enqueue({
    sessionId: recording.id, userId: "42", displayName: "Owner",
    ...speech(offset, durationMs),
  }, Buffer.from("fake wav; never sent to a provider"));
  enqueue(0, 30_000);
  enqueue(31_788, 2740);
  r.clock(43_561);
  releases[0]("Zorro create a separate website section.");
  // Let the real serial queue finish the first hook and start the second job.
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(completed, 1);
  assert.equal(releases.length, 2);
  await r.tick(44_000);
  assert.equal(r.sent.length, 0);
  r.clock(47_244);
  releases[1]("They can go to our website and look at that section.");
  await queue.drain();
  await r.tick(47_244);
  assert.equal(r.sent.length, 1);
  assert.match(r.sent[0].text, /website section\. They can go/);
  assert.equal(store.listSegments(recording.id).length, 2);
});
