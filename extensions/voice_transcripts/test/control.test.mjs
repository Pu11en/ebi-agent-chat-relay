import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCommand } from "../src/control/command.mjs";
import { matchTarget } from "../src/control/targets.mjs";
import { createRelayClient } from "../src/control/api.mjs";
import { createVoiceController } from "../src/control/controller.mjs";

// ---------------------------------------------------------------------------
// parseCommand — spoken text has no punctuation and inconsistent casing
// ---------------------------------------------------------------------------

test("the plain form: put this in the X thread <prompt>", () => {
  const result = parseCommand(
    "Put this in the Aldus thread check whether the domain verified",
  );
  assert.equal(result.kind, "relay");
  assert.equal(result.target, "Aldus");
  assert.equal(result.prompt, "check whether the domain verified");
});

test("punctuation from a well-behaved transcript is not part of the prompt", () => {
  const result = parseCommand("put this in the aldus thread: run the tests.");
  assert.equal(result.prompt, "run the tests.");
  assert.equal(result.target, "aldus");
});

test("leading filler words are ignored", () => {
  for (const filler of ["OK ", "okay, ", "Hey ", "um ", "so "]) {
    const result = parseCommand(filler + "send this to the landing thread ship it");
    assert.equal(result?.kind, "relay", filler);
    assert.equal(result.target, "landing");
  }
});

test("tell/ask forms work and drop the connecting word", () => {
  assert.equal(parseCommand("tell the upwork thread to rewrite the title").prompt,
    "rewrite the title");
  assert.equal(parseCommand("ask the upwork session what is left").prompt,
    "what is left");
});

test("session and chat are accepted as well as thread", () => {
  for (const noun of ["thread", "session", "chat"]) {
    assert.equal(parseCommand(`put this in the aldus ${noun} go`)?.kind, "relay", noun);
  }
});

test("a multi-word target is kept whole", () => {
  assert.equal(parseCommand("put this in the ebi agent relay thread run make verify").target,
    "ebi agent relay");
});

test("ordinary conversation is not a command", () => {
  for (const said of [
    "I think the thread is fine",
    "so anyway what do you think",
    "",
    "   ",
    "put the kettle on",
  ]) {
    assert.equal(parseCommand(said), null, JSON.stringify(said));
  }
});

test("naming a thread without an instruction is reported, not guessed at", () => {
  const result = parseCommand("put this in the aldus thread");
  assert.equal(result.kind, "incomplete");
  assert.equal(result.target, "aldus");
});

// ---------------------------------------------------------------------------
// matchTarget — a spoken name against live sessions
// ---------------------------------------------------------------------------

const SESSIONS = [
  {
    thread_id: 1,
    thread_name: "📂 aldus-email",
    working_dir: "/home/drewp/main-projects/aldus-email",
    state: "history",
    last_used_at: "2026-09-20 10:00:00",
  },
  {
    thread_id: 2,
    thread_name: "📂 ebi-agent-chat-relay",
    working_dir: "/home/drewp/main-projects/ebi-agent-chat-relay",
    state: "running",
    last_used_at: "2026-09-26 08:00:00",
  },
  {
    thread_id: 3,
    thread_name: "Upwork profile rewrite",
    working_dir: "/home/drewp/main-projects/upwork",
    state: "history",
    last_used_at: "2026-09-26 05:00:00",
  },
];

test("an exact folder name matches", () => {
  assert.equal(matchTarget("aldus email", SESSIONS).session.thread_id, 1);
});

test("a partial spoken name matches the thread it is part of", () => {
  assert.equal(matchTarget("upwork", SESSIONS).session.thread_id, 3);
});

test("the emoji prefix Discord puts on a thread name is ignored", () => {
  assert.equal(matchTarget("ebi agent chat relay", SESSIONS).session.thread_id, 2);
});

test("a name nothing answers to is reported as no match", () => {
  assert.equal(matchTarget("quantum tunnelling", SESSIONS).status, "none");
});

test("two equally good matches ask rather than pick", () => {
  const twins = [
    { ...SESSIONS[0], thread_id: 10, thread_name: "aldus email", working_dir: "/x/aldus-email" },
    { ...SESSIONS[0], thread_id: 11, thread_name: "aldus site", working_dir: "/x/aldus-site" },
  ];
  const result = matchTarget("aldus", twins);
  assert.equal(result.status, "ambiguous");
  assert.equal(result.options.length, 2);
});

test("the same folder open twice resolves to the one used most recently", () => {
  const twins = [
    { ...SESSIONS[0], thread_id: 10, last_used_at: "2026-09-01 00:00:00" },
    { ...SESSIONS[0], thread_id: 11, last_used_at: "2026-09-26 00:00:00" },
  ];
  assert.equal(matchTarget("aldus email", twins).session.thread_id, 11);
});

// ---------------------------------------------------------------------------
// createRelayClient
// ---------------------------------------------------------------------------

function stubFetch(handler) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  fn.calls = calls;
  return fn;
}

const ok = (body) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

test("the bearer secret is sent and never placed in the URL", async () => {
  const fetchImpl = stubFetch(() => ok({ sessions: SESSIONS }));
  const client = createRelayClient({
    baseUrl: "http://127.0.0.1:8080",
    secret: "s3cret",
    fetchImpl,
  });

  const sessions = await client.listSessions();
  assert.equal(sessions.length, 3);
  assert.equal(fetchImpl.calls[0].init.headers.Authorization, "Bearer s3cret");
  assert.ok(!fetchImpl.calls[0].url.includes("s3cret"));
});

test("with no secret configured no Authorization header is sent", async () => {
  const fetchImpl = stubFetch(() => ok({ sessions: [] }));
  await createRelayClient({ baseUrl: "http://127.0.0.1:8080", fetchImpl }).listSessions();
  assert.equal(fetchImpl.calls[0].init.headers.Authorization, undefined);
});

test("a spoken message posts the speaker and the source", async () => {
  const fetchImpl = stubFetch(
    () => new Response(JSON.stringify({ status: "delivered" }), { status: 202 }),
  );
  const client = createRelayClient({ baseUrl: "http://127.0.0.1:8080", fetchImpl });

  await client.sendSpoken({ threadId: 7, text: "run the tests", speakerId: "42" });

  const { url, init } = fetchImpl.calls[0];
  assert.equal(url, "http://127.0.0.1:8080/api/threads/7/spoken");
  assert.deepEqual(JSON.parse(init.body), {
    text: "run the tests",
    speaker_id: "42",
    source: "voice",
    mode: "queue",
  });
});

test("an error status surfaces as a thrown error, not a silent success", async () => {
  const fetchImpl = stubFetch(
    () => new Response(JSON.stringify({ error: "nope" }), { status: 403 }),
  );
  const client = createRelayClient({ baseUrl: "http://127.0.0.1:8080", fetchImpl });
  await assert.rejects(
    () => client.sendSpoken({ threadId: 7, text: "hi", speakerId: "42" }),
    /403|nope/,
  );
});

// ---------------------------------------------------------------------------
// createVoiceController — the whole loop
// ---------------------------------------------------------------------------

function makeController(overrides = {}) {
  const announced = [];
  const sent = [];
  const controller = createVoiceController({
    ownerId: "42",
    enabled: true,
    client: {
      listSessions: async () => SESSIONS,
      sendSpoken: async (payload) => {
        sent.push(payload);
        return { status: "delivered" };
      },
    },
    announce: async (message) => announced.push(message),
    logger: { info() {}, warn() {}, error() {} },
    ...overrides,
  });
  return { controller, announced, sent };
}

test("the owner's command reaches the matched thread and is confirmed", async () => {
  const { controller, announced, sent } = makeController();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the upwork thread rewrite the headline",
  });

  assert.equal(result.status, "sent");
  assert.deepEqual(sent, [
    { threadId: 3, text: "rewrite the headline", speakerId: "42", source: "voice" },
  ]);
  assert.equal(announced.length, 1);
  assert.ok(announced[0].includes("rewrite the headline"));
});

test("someone else in the room cannot drive a session", async () => {
  const { controller, sent, announced } = makeController();

  const result = await controller.handleUtterance({
    userId: "999",
    text: "put this in the upwork thread delete everything",
  });

  assert.equal(result.status, "ignored");
  assert.deepEqual(sent, []);
  assert.deepEqual(announced, []);
});

test("ordinary talk in the room sends nothing and says nothing", async () => {
  const { controller, sent, announced } = makeController();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "yeah I was thinking about the upwork thing later",
  });

  assert.equal(result.status, "ignored");
  assert.deepEqual(sent, []);
  assert.deepEqual(announced, []);
});

test("when the feature is off nothing happens at all", async () => {
  const { controller, sent } = makeController({ enabled: false });
  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the upwork thread go",
  });
  assert.equal(result.status, "disabled");
  assert.deepEqual(sent, []);
});

test("an unmatched name is reported back instead of guessing a thread", async () => {
  const { controller, sent, announced } = makeController();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the mongolia thread go",
  });

  assert.equal(result.status, "no-target");
  assert.deepEqual(sent, []);
  assert.ok(announced[0].toLowerCase().includes("mongolia"));
});

test("an ambiguous name lists the candidates instead of picking one", async () => {
  const twins = [
    { ...SESSIONS[0], thread_id: 10, thread_name: "aldus email", working_dir: "/x/aldus-email" },
    { ...SESSIONS[0], thread_id: 11, thread_name: "aldus site", working_dir: "/x/aldus-site" },
  ];
  const { controller, sent, announced } = makeController({
    client: { listSessions: async () => twins, sendSpoken: async () => {} },
  });

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the aldus thread go",
  });

  assert.equal(result.status, "ambiguous");
  assert.deepEqual(sent, []);
  assert.ok(announced[0].includes("aldus email"));
  assert.ok(announced[0].includes("aldus site"));
});

test("a named thread with no instruction asks for the instruction", async () => {
  const { controller, sent, announced } = makeController();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the upwork thread",
  });

  assert.equal(result.status, "incomplete");
  assert.deepEqual(sent, []);
  assert.equal(announced.length, 1);
});

test("an API failure is reported in the room rather than lost in a log", async () => {
  const { controller, announced } = makeController({
    client: {
      listSessions: async () => SESSIONS,
      sendSpoken: async () => {
        throw new Error("HTTP 503");
      },
    },
  });

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the upwork thread go now",
  });

  assert.equal(result.status, "failed");
  assert.ok(announced.some((m) => m.includes("503")));
});

test("a failing announcement never breaks the send", async () => {
  const { controller, sent } = makeController({
    announce: async () => {
      throw new Error("Discord is down");
    },
  });

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the upwork thread rewrite the headline",
  });

  assert.equal(result.status, "sent");
  assert.equal(sent.length, 1);
});

// ---------------------------------------------------------------------------
// The queue hook — where a finished transcription becomes a command
// ---------------------------------------------------------------------------

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createStore } from "../src/voice/store.mjs";
import { createJobQueue } from "../src/voice/job-queue.mjs";

function queueFixture(onTranscript) {
  const dir = mkdtempSync(join(tmpdir(), "voice-control-"));
  const store = createStore(dir);
  const session = store.createSession({
    guildId: "g",
    channelId: "v",
    channelName: "room",
    startedBy: "42",
  });
  const queue = createJobQueue({
    store,
    queueDir: join(dir, "audio-queue"),
    transcribe: async () => "put this in the upwork thread go",
    provider: "local",
    model: "base.en",
    maxAttempts: 1,
    logger: { info() {}, warn() {}, error() {} },
    onTranscript,
  });
  return { dir, store, session, queue, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

test("a finished transcription is handed on with its speaker", async () => {
  const seen = [];
  const fixture = queueFixture(async (utterance) => seen.push(utterance));
  try {
    fixture.queue.enqueue(
      {
        sessionId: fixture.session.id,
        userId: "42",
        displayName: "Owner",
        capturedAt: new Date().toISOString(),
        durationMs: 900,
      },
      Buffer.from("FAKE-WAV"),
    );
    await fixture.queue.drain();

    assert.equal(seen.length, 1);
    assert.equal(seen[0].userId, "42");
    assert.equal(seen[0].text, "put this in the upwork thread go");
  } finally {
    fixture.cleanup();
  }
});

test("a hook that throws never costs the transcript its segment", async () => {
  const fixture = queueFixture(async () => {
    throw new Error("controller exploded");
  });
  try {
    fixture.queue.enqueue(
      {
        sessionId: fixture.session.id,
        userId: "42",
        displayName: "Owner",
        capturedAt: new Date().toISOString(),
        durationMs: 900,
      },
      Buffer.from("FAKE-WAV"),
    );
    await fixture.queue.drain();

    const segments = fixture.store.listSegments(fixture.session.id);
    assert.equal(segments.length, 1);
    assert.equal(fixture.store.pendingJobCount(fixture.session.id), 0);
    assert.equal(fixture.store.failedJobCount(fixture.session.id), 0);
  } finally {
    fixture.cleanup();
  }
});

// ---------------------------------------------------------------------------
// Configuration — off until it is turned on, and never half-configured
// ---------------------------------------------------------------------------

import { readConfig } from "../src/config.mjs";

const BASE_ENV = {
  DISCORD_BOT_TOKEN: "token",
  DISCORD_OWNER_ID: "12345678901234567",
  VOICE_GUILD_ID: "12345678901234568",
  VOICE_CHANNEL_ID: "12345678901234569",
  VOICE_TRANSCRIPT_CHANNEL_ID: "12345678901234570",
  VOICE_DATA_DIR: "/tmp/voice-data",
};

test("voice control is off unless it is explicitly turned on", () => {
  assert.equal(readConfig({ ...BASE_ENV }).control.enabled, false);
  assert.equal(
    readConfig({ ...BASE_ENV, CCDB_API_URL: "http://127.0.0.1:8080" }).control.enabled,
    false,
  );
});

test("turning it on without an API address fails at startup, not mid-sentence", () => {
  assert.throws(
    () => readConfig({ ...BASE_ENV, VOICE_CONTROL_ENABLED: "true" }),
    /CCDB_API_URL/,
  );
});

test("a non-http API address is rejected", () => {
  assert.throws(
    () =>
      readConfig({
        ...BASE_ENV,
        VOICE_CONTROL_ENABLED: "true",
        CCDB_API_URL: "file:///etc/passwd",
      }),
    /CCDB_API_URL/,
  );
});

test("when it is on, the address and secret are carried through", () => {
  const config = readConfig({
    ...BASE_ENV,
    VOICE_CONTROL_ENABLED: "true",
    CCDB_API_URL: "http://127.0.0.1:8080/",
    CCDB_API_SECRET: "s3cret",
  });
  assert.equal(config.control.enabled, true);
  assert.equal(config.control.apiUrl, "http://127.0.0.1:8080");
  assert.equal(config.control.secret, "s3cret");
});

// ---------------------------------------------------------------------------
// The split the parser cannot decide alone
// ---------------------------------------------------------------------------

test("a target containing the noun offers both splits, longest name last", () => {
  const result = parseCommand(
    "put this in the ebi agent chat relay thread run make verify",
  );
  assert.equal(result.kind, "relay");
  assert.deepEqual(
    result.candidates.map((c) => c.target),
    ["ebi agent", "ebi agent chat relay"],
  );
});

test("the resolver picks the split that names a real session", async () => {
  const { controller, sent: captured } = makeController();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the ebi agent chat relay thread run make verify",
  });

  assert.equal(result.status, "sent");
  assert.equal(captured[0].threadId, 2);
  assert.equal(captured[0].text, "run make verify");
});

test("a noun inside the instruction does not steal the split", async () => {
  const captured = [];
  const controller = createVoiceController({
    ownerId: "42",
    enabled: true,
    client: {
      listSessions: async () => SESSIONS,
      sendSpoken: async (p) => captured.push(p),
    },
    announce: async () => {},
    logger: { info() {}, warn() {}, error() {} },
  });

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the upwork thread check the thread pool size",
  });

  assert.equal(result.status, "sent");
  assert.equal(captured[0].threadId, 3);
  assert.equal(captured[0].text, "check the thread pool size");
});

test("the longest name wins when nothing follows it", () => {
  assert.deepEqual(parseCommand("put this in the aldus site thread"), {
    kind: "incomplete",
    target: "aldus site",
  });
});
