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

test("naming a thread without an instruction yields an empty instruction", () => {
  const result = parseCommand("put this in the aldus thread");
  assert.equal(result.kind, "relay");
  assert.equal(result.target, "aldus");
  assert.equal(result.prompt, "");
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

test("a named thread with no instruction is held, not sent", async () => {
  const { controller, sent, announced } = makeController();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the upwork thread",
  });

  assert.equal(result.status, "incomplete");
  assert.deepEqual(sent, []);
  assert.equal(announced.length, 1);
  assert.ok(announced[0].includes("Holding"));
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

test("a name that contains the noun still offers its full reading", () => {
  const result = parseCommand("put this in the ebi agent chat relay thread");
  assert.deepEqual(
    result.candidates,
    [
      { target: "ebi agent", prompt: "relay thread" },
      { target: "ebi agent chat relay", prompt: "" },
    ],
  );
});

// ---------------------------------------------------------------------------
// Discord IDs are 19 digits — JSON.parse cannot hold them
// ---------------------------------------------------------------------------

test("a 19-digit thread id survives the API client exactly", async () => {
  const body = '{"sessions":[{"thread_id":1553390219548561508,"thread_name":"x"}]}';
  const fetchImpl = stubFetch(
    () => new Response(body, { status: 200, headers: { "content-type": "application/json" } }),
  );
  const client = createRelayClient({ baseUrl: "http://127.0.0.1:8080", fetchImpl });

  const [session] = await client.listSessions();
  // 1553390219548561508 is beyond Number.MAX_SAFE_INTEGER; parsed as a number it
  // silently becomes ...400 and Discord answers "Unknown Channel".
  assert.equal(session.thread_id, "1553390219548561508");
});

test("that id is used verbatim in the request URL", async () => {
  const fetchImpl = stubFetch(() => new Response("{}", { status: 202 }));
  const client = createRelayClient({ baseUrl: "http://127.0.0.1:8080", fetchImpl });

  await client.sendSpoken({
    threadId: "1553390219548561508",
    text: "hi",
    speakerId: "42",
  });

  assert.ok(fetchImpl.calls[0].url.endsWith("/api/threads/1553390219548561508/spoken"));
});

test("ordinary numbers in the payload are left alone", async () => {
  const body = '{"sessions":[{"thread_id":7,"duration":1.5,"capacity":{"limit":10}}]}';
  const fetchImpl = stubFetch(
    () => new Response(body, { status: 200, headers: { "content-type": "application/json" } }),
  );
  const [session] = await createRelayClient({
    baseUrl: "http://127.0.0.1:8080",
    fetchImpl,
  }).listSessions();

  assert.equal(session.thread_id, "7");
  assert.equal(session.duration, 1.5);
});

// ---------------------------------------------------------------------------
// Spoken tags, and the pause in the middle of a sentence
// ---------------------------------------------------------------------------

const TAGGED = [
  { thread_id: 1, thread_name: "📂 ebi-agent-chat-relay", working_dir: "/x/ebi-agent-chat-relay",
    voice_label: "alpha", state: "running", last_used_at: "2026-09-26 08:00:00" },
  { thread_id: 2, thread_name: "📂 aldus-email", working_dir: "/x/aldus-email",
    voice_label: "bravo", state: "history", last_used_at: "2026-09-26 07:00:00" },
  { thread_id: 3, thread_name: "📂 aldus-site", working_dir: "/x/aldus-site",
    voice_label: "charlie", state: "history", last_used_at: "2026-09-26 06:00:00" },
];

function tagged(overrides = {}) {
  const announced = [];
  const sent = [];
  let clock = 1_000_000;
  const controller = createVoiceController({
    ownerId: "42",
    enabled: true,
    client: {
      listSessions: async () => TAGGED,
      sendSpoken: async (p) => sent.push(p),
    },
    announce: async (m) => announced.push(m),
    logger: { info() {}, warn() {}, error() {} },
    now: () => clock,
    ...overrides,
  });
  return { controller, announced, sent, tick: (ms) => (clock += ms) };
}

test("a tag is an exact handle: 'thread alpha' goes to alpha", async () => {
  const { controller, sent } = tagged();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "put this in the alpha thread run make verify",
  });

  assert.equal(result.status, "sent");
  assert.equal(sent[0].threadId, 1);
  assert.equal(sent[0].text, "run make verify");
});

test("a tag beats a name that looks more like what was said", async () => {
  // "bravo" resembles nothing in aldus-email's title, and must still win.
  const { controller, sent } = tagged();
  await controller.handleUtterance({ userId: "42", text: "tell the bravo thread to check DKIM" });
  assert.equal(sent[0].threadId, 2);
  assert.equal(sent[0].text, "check DKIM");
});

test("a tag removes the ambiguity two similar folders would cause", async () => {
  const { controller, sent, announced } = tagged();

  // By name, "aldus" matches both aldus-email and aldus-site.
  await controller.handleUtterance({ userId: "42", text: "put this in the aldus thread go" });
  assert.deepEqual(sent, []);
  assert.ok(announced[0].includes("more than one"));

  // By tag, there is nothing to ask about.
  await controller.handleUtterance({ userId: "42", text: "put this in the charlie thread go" });
  assert.equal(sent[0].threadId, 3);
});

test("the confirmation shows the tag so the speaker learns it", async () => {
  const { controller, announced } = tagged();
  await controller.handleUtterance({ userId: "42", text: "put this in the alpha thread go" });
  assert.ok(announced[0].includes("alpha"));
});

test("the exact sentence that failed live now holds instead of sending a fragment", async () => {
  const { controller, sent, announced } = tagged();

  // Captured per pause: the name arrives with no instruction behind it.
  const first = await controller.handleUtterance({
    userId: "42",
    text: "Put this in the ebi agent chat relay thread.",
  });

  assert.equal(first.status, "incomplete");
  assert.deepEqual(sent, [], "must not deliver 'relay thread.' as the work");
  assert.ok(announced[0].includes("Holding"));

  // The rest of the sentence lands on the held thread.
  const second = await controller.handleUtterance({
    userId: "42",
    text: "tell me what time it is",
  });

  assert.equal(second.status, "sent");
  assert.equal(sent[0].threadId, 1);
  assert.equal(sent[0].text, "tell me what time it is");
});

test("the hold expires, so later conversation is not swept into a thread", async () => {
  const { controller, sent, tick } = tagged();

  await controller.handleUtterance({ userId: "42", text: "put this in the alpha thread" });
  tick(31_000);
  const result = await controller.handleUtterance({ userId: "42", text: "anyway where were we" });

  assert.equal(result.status, "ignored");
  assert.deepEqual(sent, []);
});

test("a fresh command replaces a held thread rather than filling it", async () => {
  const { controller, sent } = tagged();

  await controller.handleUtterance({ userId: "42", text: "put this in the alpha thread" });
  await controller.handleUtterance({ userId: "42", text: "put this in the bravo thread ship it" });

  assert.equal(sent.length, 1);
  assert.equal(sent[0].threadId, 2);
  assert.equal(sent[0].text, "ship it");
});

test("a hold is not filled by someone else in the room", async () => {
  const { controller, sent } = tagged();

  await controller.handleUtterance({ userId: "42", text: "put this in the alpha thread" });
  const result = await controller.handleUtterance({ userId: "999", text: "delete everything" });

  assert.equal(result.status, "ignored");
  assert.deepEqual(sent, []);
});

test("with no tags assigned the room is told so, not shown an empty list", async () => {
  const { controller, announced } = tagged({
    client: {
      listSessions: async () => TAGGED.map(({ voice_label, ...rest }) => rest),
      sendSpoken: async () => {},
    },
  });

  await controller.handleUtterance({ userId: "42", text: "put this in the mongolia thread go" });
  assert.ok(announced[0].includes("no tags assigned yet"));
});

// ---------------------------------------------------------------------------
// The roster — tags are useless if they are not visible
// ---------------------------------------------------------------------------

import { renderRoster } from "../src/control/roster.mjs";

test("the roster lists every tag with its thread, working ones first", () => {
  const text = renderRoster(TAGGED);
  assert.ok(text.includes("`alpha` — 📂 ebi-agent-chat-relay"));
  assert.ok(text.includes("`bravo` — 📂 aldus-email"));
  assert.ok(text.indexOf("alpha") < text.indexOf("bravo"));
  assert.ok(text.includes("🟢 working"));
});

test("the roster shows how to use a tag, with a real one", () => {
  assert.ok(renderRoster(TAGGED).includes("put this in the alpha thread"));
});

test("an untagged set produces no roster rather than an empty one", () => {
  assert.equal(renderRoster([]), null);
  assert.equal(renderRoster(TAGGED.map(({ voice_label, ...r }) => r)), null);
});

test("the roster is capped so it cannot outgrow one message", () => {
  const many = Array.from({ length: 30 }, (_, i) => ({
    thread_id: i,
    thread_name: `t${i}`,
    voice_label: `tag${i}`,
    state: "history",
    last_used_at: `2026-09-${String(i + 1).padStart(2, "0")} 00:00:00`,
  }));
  assert.equal(renderRoster(many).split("\n").length - 1, 12);
});

test("a tagged title is not shown with its tag twice", () => {
  const titled = TAGGED.map((s) => ({ ...s, thread_name: `[${s.voice_label}] ${s.thread_name}` }));
  const text = renderRoster(titled);

  assert.ok(text.includes("`alpha` — 📂 ebi-agent-chat-relay"));
  assert.ok(!text.includes("[alpha]"));
});

test("the confirmation line does not repeat the tag either", async () => {
  const titled = TAGGED.map((s) => ({ ...s, thread_name: `[${s.voice_label}] ${s.thread_name}` }));
  const said = [];
  const controller = createVoiceController({
    ownerId: "42",
    enabled: true,
    client: { listSessions: async () => titled, sendSpoken: async () => {} },
    announce: async (m) => said.push(m),
    logger: { info() {}, warn() {}, error() {} },
  });

  await controller.handleUtterance({ userId: "42", text: "put this in the alpha thread go" });

  assert.equal(said[0], "🎙️ → **`alpha` 📂 ebi-agent-chat-relay**: go");
});

// ---------------------------------------------------------------------------
// The sentences actually spoken into the room, verbatim from the transcript
// ---------------------------------------------------------------------------

import { parseByTag } from "../src/control/command.mjs";

test("saying the tag and then just talking addresses that thread", () => {
  const r = parseByTag("Okay and alpha say that we need to make this a repo", ["alpha", "bravo"]);
  assert.equal(r.target, "alpha");
  assert.equal(r.prompt, "say that we need to make this a repo");
});

test("a bare tag with a comma works", () => {
  assert.equal(parseByTag("Alpha, run the tests.", ["alpha"]).prompt, "run the tests.");
});

test("'alpha thread' drops the word thread", () => {
  assert.equal(parseByTag("alpha thread, run make verify", ["alpha"]).prompt, "run make verify");
});

test("polite lead-ins are not part of the instruction", () => {
  assert.equal(parseByTag("hey bravo can you check DKIM", ["bravo"]).prompt, "check DKIM");
  assert.equal(parseByTag("alpha please push the branch", ["alpha"]).prompt, "push the branch");
});

test("a tag deep inside a sentence is conversation, not an address", () => {
  assert.equal(
    parseByTag("the delta between the two runs was small so I left it", ["delta"]),
    null,
  );
});

test("only tags in use are listened for", () => {
  assert.equal(parseByTag("zulu do the thing", ["alpha"]), null);
  assert.equal(parseByTag("alpha do the thing", []), null);
});

test("a bare tag with nothing after it yields an empty instruction, to be held", () => {
  assert.equal(parseByTag("Alpha.", ["alpha"]).prompt, "");
});

test("the whole loop: tag, pause, then the instruction", async () => {
  const { controller, sent, announced } = tagged();

  const first = await controller.handleUtterance({ userId: "42", text: "Okay, alpha." });
  assert.equal(first.status, "incomplete");
  assert.ok(announced[0].includes("Holding"));
  assert.deepEqual(sent, []);

  const second = await controller.handleUtterance({
    userId: "42",
    text: "say that we need to make this a repo",
  });
  assert.equal(second.status, "sent");
  assert.equal(sent[0].threadId, 1);
  assert.equal(sent[0].text, "say that we need to make this a repo");
});

test("the tag form wins over the sentence template", async () => {
  const { controller, sent } = tagged();

  await controller.handleUtterance({
    userId: "42",
    text: "bravo put this in the alpha thread",
  });

  assert.equal(sent[0].threadId, 2, "bravo was addressed; the rest is the instruction");
});

test("the session list is read once for a burst of utterances", async () => {
  let reads = 0;
  const { controller } = tagged({
    client: {
      listSessions: async () => {
        reads += 1;
        return TAGGED;
      },
      sendSpoken: async () => {},
    },
  });

  await controller.handleUtterance({ userId: "42", text: "alpha go" });
  await controller.handleUtterance({ userId: "42", text: "just chatting here" });
  await controller.handleUtterance({ userId: "42", text: "bravo go" });

  assert.equal(reads, 1);
});

test("an unreachable API stays silent rather than complaining about small talk", async () => {
  const { controller, announced } = tagged({
    client: {
      listSessions: async () => {
        throw new Error("ECONNREFUSED");
      },
      sendSpoken: async () => {},
    },
  });

  const result = await controller.handleUtterance({ userId: "42", text: "anyway where were we" });

  assert.equal(result.status, "ignored");
  assert.deepEqual(announced, []);
});

test("a tag used as a noun is not an address, whatever its position", () => {
  for (const said of [
    "the delta between the two runs was small",
    "I think alpha is the better option",
    "we should check the echo settings",
    "call it bravo if you like",
  ]) {
    assert.equal(parseByTag(said, ["alpha", "bravo", "delta", "echo"]), null, said);
  }
});

test("throat-clearing before a tag still leaves it an address", () => {
  for (const said of ["okay so alpha go", "uh, alpha go", "oh yeah alpha go", "and alpha go"]) {
    assert.equal(parseByTag(said, ["alpha"])?.prompt, "go", said);
  }
});

// ---------------------------------------------------------------------------
// Opening a session by voice
// ---------------------------------------------------------------------------

import { parseNewSession } from "../src/control/command.mjs";
import { matchFolder, skeleton, soundsLike } from "../src/control/folders.mjs";

const PROJECTS = [
  { name: "the aldus", path: "/home/drewp/main-projects/the aldus" },
  { name: "aldus-email", path: "/home/drewp/main-projects/aldus-email" },
  { name: "upwork", path: "/home/drewp/main-projects/upwork" },
  { name: "archify", path: "/home/drewp/main-projects/archify" },
  { name: "gigamedia", path: "/home/drewp/main-projects/gigamedia" },
];

test("the folder name survives being misheard", () => {
  // Measured: the recogniser wrote "oldest" for "aldus".
  assert.equal(matchFolder("the oldest folder", PROJECTS).project.name, "the aldus");
  assert.equal(matchFolder("up work", PROJECTS).project.name, "upwork");
  assert.equal(matchFolder("arkify", PROJECTS).project.name, "archify");
});

test("an article does not decide the match", () => {
  // "the" folds to a 'd' and would otherwise dominate a short skeleton.
  assert.equal(skeleton("the aldus"), skeleton("aldus"));
});

test("a name nothing sounds like matches nothing", () => {
  assert.equal(matchFolder("quantum tunnelling", PROJECTS).status, "none");
  assert.equal(soundsLike("quantum tunnelling", "gigamedia"), 0);
});

test("a short skeleton does not prefix half the catalog", () => {
  // `knd` (gigamedia) is a literal prefix of `kndndnlnk` (quantum tunnelling).
  assert.ok(soundsLike("gigamedia", "quantum tunnelling") < 0.7);
});

test("two folders that sound equally close are a question", () => {
  const result = matchFolder("aldus", [
    { name: "aldus-one", path: "/x/aldus-one" },
    { name: "aldus-won", path: "/x/aldus-won" },
  ]);
  assert.equal(result.status, "unsure");
  assert.equal(result.options.length, 2);
});

test("the spoken sentence that failed live now parses", () => {
  const r = parseNewSession(
    "Make a new thread in the oldest folder and we're going to do design work for the audit link",
  );
  assert.equal(r.kind, "spawn");
  assert.deepEqual(r.candidates[0], {
    folder: "oldest",
    prompt: "we're going to do design work for the audit link",
  });
});

test("several phrasings all open a session", () => {
  for (const said of [
    "start a new session in aldus and do the design work",
    "open a session in archify",
    "make me a new session in the upwork folder, rewrite the headline",
    "okay create a new chat inside the aldus folder",
    "spin up a thread for archify",
  ]) {
    assert.equal(parseNewSession(said)?.kind, "spawn", said);
  }
});

test("talking to an existing thread is not opening one", () => {
  for (const said of [
    "put this in the alpha thread go",
    "alpha run the tests",
    "I made a new session earlier",
  ]) {
    assert.equal(parseNewSession(said), null, said);
  }
});

function opener(overrides = {}) {
  const announced = [];
  const spawned = [];
  const controller = createVoiceController({
    ownerId: "42",
    enabled: true,
    fallbackDir: "/root/projects",
    client: {
      listSessions: async () => TAGGED,
      listProjects: async () => PROJECTS,
      spawn: async (p) => {
        spawned.push(p);
        return { thread_id: "1553418072084324383" };
      },
      sendSpoken: async () => {},
    },
    announce: async (m) => announced.push(m),
    logger: { info() {}, warn() {}, error() {} },
    ...overrides,
  });
  return { controller, announced, spawned };
}

test("a misheard folder still opens the right session, with the instruction", async () => {
  const { controller, spawned, announced } = opener();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "Make a new thread in the oldest folder and we're going to do design work",
  });

  assert.equal(result.status, "opened");
  assert.equal(spawned[0].workingDir, "/home/drewp/main-projects/the aldus");
  assert.ok(spawned[0].prompt.includes("design work"));
  assert.ok(announced[0].includes("the aldus"));
});

test("no folder match opens one anyway and says what it heard", async () => {
  const { controller, spawned, announced } = opener();

  const result = await controller.handleUtterance({
    userId: "42",
    text: "make a new session in the flibbertigibbet folder and plan the launch",
  });

  assert.equal(result.status, "opened");
  assert.equal(spawned[0].workingDir, "/root/projects");
  assert.ok(spawned[0].prompt.includes("flibbertigibbet"), "the new session is told what he said");
  assert.ok(spawned[0].prompt.includes("plan the launch"), "and what he wanted done");
  assert.ok(announced[0].includes("No folder matched"));
});

test("opening with no instruction asks rather than inventing work", async () => {
  const { controller, spawned } = opener();

  await controller.handleUtterance({ userId: "42", text: "open a session in archify" });

  assert.equal(spawned[0].workingDir, "/home/drewp/main-projects/archify");
  assert.ok(spawned[0].prompt.includes("has not said what to work on"));
});

test("someone else in the room cannot open a session", async () => {
  const { controller, spawned } = opener();
  await controller.handleUtterance({ userId: "999", text: "open a session in archify" });
  assert.deepEqual(spawned, []);
});

test("addressing a thread by tag beats opening a new one", async () => {
  const { controller, spawned, sent } = { ...opener(), sent: [] };
  const captured = [];
  const c = createVoiceController({
    ownerId: "42",
    enabled: true,
    client: {
      listSessions: async () => TAGGED,
      listProjects: async () => PROJECTS,
      spawn: async () => ({ thread_id: "1" }),
      sendSpoken: async (p) => captured.push(p),
    },
    announce: async () => {},
    logger: { info() {}, warn() {}, error() {} },
  });
  void spawned;
  void sent;

  await c.handleUtterance({ userId: "42", text: "alpha make a new session in archify" });

  assert.equal(captured.length, 1, "the instruction went to alpha, not to the spawner");
});

test("however the request is framed, it still opens a session", () => {
  for (const said of [
    "Make a new session in the aldus folder and do design work",
    "I make a new session in the oldest folder and then do design work",
    "I'll make a new session in aldus",
    "can you open a session in archify",
    "let's start a thread in upwork",
    "okay so I want to make a new session in aldus and plan the launch",
    "we need to create a new chat in archify",
  ]) {
    assert.equal(parseNewSession(said)?.kind, "spawn", said);
  }
});

test("talking about sessions in the past tense is not a request", () => {
  for (const said of [
    "I made a new session in aldus earlier",
    "the new session in aldus is going fine",
  ]) {
    assert.equal(parseNewSession(said), null, said);
  }
});
