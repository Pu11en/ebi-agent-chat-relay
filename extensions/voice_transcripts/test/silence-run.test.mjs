/**
 * One tag, one run, one message — Drew's rule, written down.
 *
 * The old behaviour sent each sentence the moment it was transcribed and then
 * kept listening to that thread for 90 seconds. Two things went wrong with that,
 * and both happened to him on 2026-09-27:
 *
 * - One thought arrived as three separate messages and three separate turns.
 * - When the recogniser missed the *next* tag — "Luffy" never reached the
 *   transcript at all — his following sentences silently flowed into the previous
 *   thread. `nami` swallowed everything and `luffy` got nothing.
 *
 * His rule instead:
 *
 * 1. The first tag locks the target.
 * 2. Everything said afterwards accumulates. A later tag is just a word.
 * 3. Ten seconds of silence sends the lot as one message.
 * 4. The lock is then released: the next run needs its own tag, and with no tag
 *    nothing is sent — which is how he learns the tag was missed.
 *
 * Point 4 is the load-bearing one. Silence as the failure mode is deliberate:
 * with an unreliable recogniser, "nothing happened" is information, and "it went
 * somewhere you did not choose" is not.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createVoiceController, SILENCE_SEND_MS } from "../src/control/controller.mjs";

const SESSIONS = [
  { thread_id: "1", thread_name: "📂 relay", voice_label: "zoro", voice_label_aliases: ["zorro"], state: "history" },
  { thread_id: "2", thread_name: "📂 aldus", voice_label: "nami", voice_label_aliases: [], state: "history" },
  { thread_id: "3", thread_name: "📂 law", voice_label: "luffy", voice_label_aliases: ["lucy"], state: "history" },
];

/** A controller whose clock and timer are ours, so the pacing is asserted, not slept through. */
function rig() {
  const sent = [];
  const announced = [];
  let clock = 1_000_000;
  let pending = null; // the single armed timer

  const controller = createVoiceController({
    ownerId: "42",
    enabled: true,
    client: {
      listSessions: async () => SESSIONS,
      sendSpoken: async (payload) => {
        sent.push(payload);
        return { status: "delivered" };
      },
      close: async () => ({ state: "closed" }),
    },
    announce: async (m) => announced.push(m),
    logger: { info() {}, warn() {}, error() {} },
    now: () => clock,
    setTimer: (fn, ms) => {
      pending = { fn, at: clock + ms };
      return pending;
    },
    clearTimer: (handle) => {
      if (pending === handle) pending = null;
    },
  });

  return {
    sent,
    announced,
    /** Speak, at the current clock, for `seconds`. Clock advances by that much. */
    async say(text, seconds = 3) {
      const capturedAt = new Date(clock).toISOString();
      const durationMs = seconds * 1000;
      clock += durationMs;
      await controller.handleUtterance({
        userId: "42",
        text,
        capturedAt,
        durationMs,
      });
    },
    /** An utterance that arrived with no timing information at all. */
    async sayWithoutTiming(text) {
      await controller.handleUtterance({ userId: "42", text });
    },
    /** Let `seconds` of silence pass, firing the timer if it is due. */
    async wait(seconds) {
      clock += seconds * 1000;
      if (pending && clock >= pending.at) {
        const due = pending;
        pending = null;
        await due.fn();
      }
    },
    get armed() {
      return pending !== null;
    },
  };
}

test("the window is ten seconds", () => {
  assert.equal(SILENCE_SEND_MS, 10_000);
});

test("a tag locks the target and nothing is sent yet", async () => {
  const r = rig();
  await r.say("luffy look at the law notes");
  assert.deepEqual(r.sent, [], "nothing goes out until he stops talking");
  assert.ok(r.armed, "the silence timer is running");
});

test("speaking again inside ten seconds resets the clock", async () => {
  const r = rig();
  await r.say("luffy look at the law notes");
  await r.wait(2);
  await r.say("and also check the politics folder");
  await r.wait(9); // nine seconds since the *second* utterance
  assert.deepEqual(r.sent, [], "still talking as far as it knows");
});

test("ten seconds of silence sends everything as one message", async () => {
  const r = rig();
  await r.say("luffy look at the law notes");
  await r.wait(2);
  await r.say("and also check the politics folder");
  await r.wait(10);

  assert.equal(r.sent.length, 1, "one thought, one message");
  assert.equal(r.sent[0].threadId, "3");
  assert.match(r.sent[0].text, /law notes/);
  assert.match(r.sent[0].text, /politics folder/);
});

test("a second tag inside the run is just a word", async () => {
  const r = rig();
  await r.say("luffy compare this against nami and tell me");
  await r.wait(10);

  assert.equal(r.sent.length, 1);
  assert.equal(r.sent[0].threadId, "3", "the first tag owns the run");
  assert.match(r.sent[0].text, /nami/, "the later tag stays in the words");
});

test("after it sends, the aim is cleared and a tagless sentence goes nowhere", async () => {
  const r = rig();
  await r.say("luffy look at the law notes");
  await r.wait(10);
  assert.equal(r.sent.length, 1);

  await r.say("today is a new day so what can we plan");
  await r.wait(10);
  assert.equal(r.sent.length, 1, "no tag, no delivery — that is the signal");
});

test("after it sends, a new tag starts a new run", async () => {
  const r = rig();
  await r.say("luffy look at the law notes");
  await r.wait(10);
  await r.say("nami what local host site can I make");
  await r.wait(10);

  assert.equal(r.sent.length, 2);
  assert.equal(r.sent[1].threadId, "2");
  assert.match(r.sent[1].text, /local host/);
});

test("a tagless sentence when idle is never delivered anywhere", async () => {
  const r = rig();
  await r.say("today is a new day so what can we plan");
  await r.wait(20);
  assert.deepEqual(r.sent, [], "silence is how he learns the tag was missed");
});

test("recogniser noise does not hold the run open forever", async () => {
  // "Thank you." is what this model emits for near-silence; it reached the
  // transcript at 08:39:38 today. Treating it as speech would reset the clock
  // every few seconds and the message would never be sent.
  const r = rig();
  await r.say("luffy look at the law notes");
  await r.wait(6);
  await r.say("Thank you.", 1);
  await r.wait(5);

  assert.equal(r.sent.length, 1, "the noise did not count as talking");
  assert.ok(!/thank you/i.test(r.sent[0].text), "and was not added to the message");
});

test("stop listening throws the run away without sending it", async () => {
  const r = rig();
  await r.say("luffy look at the law notes");
  await r.wait(2);
  await r.say("stop listening");
  await r.wait(20);

  assert.deepEqual(r.sent, []);
});

test("the silence is measured from when he stopped speaking, not when the text arrived", async () => {
  // Transcription lags — measured up to 30s on this machine. Timing off arrival
  // would count that lag as silence and cut him off mid-thought.
  const r = rig();
  await r.say("luffy this is a long thought that took a while to transcribe", 25);
  // Only 3 seconds have passed since he stopped.
  await r.wait(3);
  assert.deepEqual(r.sent, [], "a slow transcription is not a pause");
  await r.wait(7);
  assert.equal(r.sent.length, 1);
});

test("without the duration a long sentence would be cut off immediately", async () => {
  // The silence is `capturedAt + durationMs + 10s`. `capturedAt` is when he
  // *started*, so leaving the duration out makes a 25-second sentence look like
  // it ended 25 seconds ago — the run would flush the instant it opened. The
  // queue therefore has to pass the duration through, and this pins that.
  const r = rig();
  await r.say("luffy a long thought", 25);
  await r.wait(1);
  assert.deepEqual(r.sent, [], "one second after he stopped is not silence");
});

test("a missing capture time falls back to now rather than sending at once", async () => {
  const r = rig();
  await r.sayWithoutTiming("luffy do the thing");
  assert.deepEqual(r.sent, [], "an unknown capture time must not mean 'ten seconds ago'");
});
