import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createVoiceTransport } from "../src/transport.mjs";
import { safeWorkerEnv } from "../src/voice/transcriber.mjs";

test("disconnect aborts a pending voice connection instead of waiting 20 seconds", async () => {
  const connection = new EventEmitter();
  connection.state = { status: "connecting" };
  connection.receiver = { speaking: new EventEmitter() };
  connection.destroy = () => {
    connection.state.status = "destroyed";
  };
  let signal;
  const transport = createVoiceTransport({
    config: { guildId: "g", channelId: "v" },
    client: {
      guilds: { cache: new Map([["g", { voiceAdapterCreator() {} }]]) },
    },
    service: {},
    voice: {
      joinVoiceChannel: () => connection,
      VoiceConnectionStatus: { Ready: "ready", Destroyed: "destroyed" },
      entersState: async (target, status, value) => {
        signal = value;
        if (!(signal instanceof AbortSignal)) return;
        await new Promise((resolve, reject) => {
          signal.addEventListener("abort", () => reject(new Error("aborted")), {
            once: true,
          });
        });
      },
    },
    logger: { info() {}, warn() {} },
  });
  const joining = transport.connect({ id: "s" });
  const result = joining.then(
    () => null,
    (error) => error,
  );
  try {
    assert.ok(
      signal instanceof AbortSignal,
      "voice wait must accept cancellation",
    );
    transport.disconnect();
    assert.equal(signal.aborted, true);
    assert.equal(connection.state.status, "destroyed");
    assert.match((await result).message, /aborted/);
    assert.equal(transport.ready(), false);
  } finally {
    transport.disconnect();
    await result;
  }
});

test("worker receives no bot token or unrelated credentials", () => {
  const env = safeWorkerEnv({
    HOME: "/home/example",
    PATH: "/usr/bin",
    DISCORD_BOT_TOKEN: "secret",
    OPENAI_API_KEY: "secret",
    AWS_SECRET_ACCESS_KEY: "secret",
  });
  assert.equal(env.HOME, "/home/example");
  assert.equal(env.DISCORD_BOT_TOKEN, undefined);
  assert.equal(env.OPENAI_API_KEY, undefined);
  assert.equal(env.AWS_SECRET_ACCESS_KEY, undefined);
});
test("receiver keeps separate speakers and flushes unfinished speech on disconnect", async () => {
  const speaking = new EventEmitter();
  const streams = new Map();
  const results = [];
  const connection = {
    state: { status: "ready" },
    receiver: {
      speaking,
      subscribe: (id) => {
        const s = new PassThrough();
        streams.set(id, s);
        return s;
      },
    },
    on() {},
    destroy() {
      this.state.status = "destroyed";
    },
  };
  const voice = {
    joinVoiceChannel: () => connection,
    entersState: async () => {},
    VoiceConnectionStatus: { Ready: "ready", Destroyed: "destroyed" },
    EndBehaviorType: { AfterSilence: 1 },
  };
  const guild = {
    voiceAdapterCreator: () => {},
    members: {
      cache: new Map([
        ["a", { displayName: "Alice" }],
        ["b", { displayName: "Bob" }],
      ]),
    },
  };
  const client = {
    user: { id: "bot" },
    guilds: { cache: new Map([["g", guild]]) },
    users: { cache: new Map() },
  };
  const config = {
    guildId: "g",
    channelId: "v",
    ownerId: "owner",
    silenceMs: 1000,
    minUtteranceMs: 100,
    maxUtteranceSeconds: 20,
  };
  const service = {
    active: () => ({ id: "s" }),
    enqueueUtterance: (meta, wav) => results.push({ meta, wav }),
  };
  const transport = createVoiceTransport({
    config,
    client,
    service,
    voice,
    createDecoder: () => new PassThrough(),
    mayCapture: () => true,
  });
  await transport.connect({ id: "s" });
  speaking.emit("start", "a");
  speaking.emit("start", "b");
  assert.equal(transport.pendingSpeech("s", "a").length, 1);
  assert.equal(transport.pendingSpeech("s", "a")[0].active, true);
  assert.ok(
    Number.isFinite(
      Date.parse(transport.pendingSpeech("s", "a")[0].capturedAt),
    ),
  );
  assert.deepEqual(transport.pendingSpeech("other-session", "a"), []);
  assert.deepEqual(transport.pendingSpeech("s", "nobody"), []);
  streams.get("a").write(Buffer.alloc(19200, 1));
  streams.get("b").write(Buffer.alloc(19200, 2));
  transport.disconnect();
  assert.deepEqual(transport.pendingSpeech("s", "a"), []);
  assert.equal(results.length, 2);
  assert.equal(results[0].meta.displayName, "Alice");
  assert.equal(results[1].meta.displayName, "Bob");
  assert.equal(results[0].wav.length, 19244);
  assert.equal(results[0].wav[44], 1);
  assert.equal(results[1].wav[44], 2);
});
