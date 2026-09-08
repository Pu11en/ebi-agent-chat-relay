import { test } from "node:test";
import assert from "node:assert/strict";
import { createPresenceController, canControl } from "../src/presence.mjs";
import { readConfig, parseEnv } from "../src/config.mjs";
import { createPcmBuffer } from "../src/capture.mjs";

function fixture(paused = false) {
  const calls = [];
  let active = null;
  let ownerChannel = null;
  const controller = createPresenceController({
    channelId: "room",
    ownerChannel: () => ownerChannel,
    active: () => active,
    isPaused: () => paused,
    setPaused: (value) => {
      paused = value;
    },
    start: async () => {
      calls.push("start");
      active = { id: "session" };
    },
    stop: async () => {
      calls.push("stop");
      active = null;
    },
    reconnect: async () => {
      calls.push("reconnect");
    },
  });
  return {
    calls,
    controller,
    owner: (value) => {
      ownerChannel = value;
    },
    active: (value) => {
      active = value;
    },
  };
}
test("owner joining starts once; others/mute changes do not create more sessions", async () => {
  const f = fixture();
  await f.controller.reconcile();
  assert.deepEqual(f.calls, []);
  f.owner("room");
  await f.controller.reconcile();
  await f.controller.reconcile();
  assert.deepEqual(f.calls, ["start", "reconnect"]);
  f.owner("elsewhere");
  await f.controller.reconcile();
  assert.equal(f.calls.at(-1), "stop");
});
test("startup closes a persisted recording when owner is absent", async () => {
  const f = fixture();
  f.active({ id: "before-crash" });
  await f.controller.reconcile();
  assert.deepEqual(f.calls, ["stop"]);
});
test("pause survives health checks and restart until owner leaves", async () => {
  const f = fixture(true);
  f.owner("room");
  await f.controller.reconcile();
  assert.deepEqual(f.calls, []);
  f.owner(null);
  await f.controller.reconcile();
  f.owner("room");
  await f.controller.reconcile();
  assert.deepEqual(f.calls, ["start"]);
  await f.controller.pause();
  await f.controller.reconcile();
  assert.deepEqual(f.calls, ["start", "stop"]);
  await f.controller.resume();
  assert.equal(f.calls.at(-1), "start");
});
test("racing events serialize without duplicate starts", async () => {
  const f = fixture();
  f.owner("room");
  await Promise.all(Array.from({ length: 5 }, () => f.controller.reconcile()));
  assert.equal(f.calls.filter((x) => x === "start").length, 1);
});
test("only owner can resume; people in the recorded room can pause", () => {
  const config = { guildId: "g", ownerId: "owner", channelId: "room" };
  assert.equal(
    canControl(
      config,
      { guildId: "g", userId: "guest", channelId: "room" },
      "pause",
    ),
    true,
  );
  assert.equal(
    canControl(
      config,
      { guildId: "g", userId: "guest", channelId: "room" },
      "resume",
    ),
    false,
  );
  assert.equal(
    canControl(config, { guildId: "other", userId: "owner" }, "pause"),
    false,
  );
  assert.equal(
    canControl(
      config,
      { guildId: "g", userId: "guest", channelId: "elsewhere" },
      "pause",
    ),
    false,
  );
  assert.equal(
    canControl(config, { guildId: "g", userId: "owner" }, "resume"),
    true,
  );
});
test("PCM rotates at the cap without dropping continuous speech and flushes on stop", () => {
  const chunks = [];
  const buffer = createPcmBuffer({
    maxBytes: 8,
    minBytes: 4,
    onChunk: (b) => chunks.push(b),
  });
  buffer.write(Buffer.from("abcdefghijklmnopqrst"));
  buffer.flush();
  buffer.flush();
  assert.equal(Buffer.concat(chunks).toString(), "abcdefghijklmnopqrst");
  assert.deepEqual(
    chunks.map((x) => x.length),
    [8, 8, 4],
  );
});
test("env parser never evaluates shell syntax and config rejects missing/cross-channel IDs", () => {
  assert.equal(
    parseEnv('TOKEN="$(echo secret)"\n# ignore\nX=a=b').TOKEN,
    "$(echo secret)",
  );
  const env = {
    DISCORD_BOT_TOKEN: "local-test",
    DISCORD_OWNER_ID: "111111111111111111",
    VOICE_GUILD_ID: "222222222222222222",
    VOICE_CHANNEL_ID: "333333333333333333",
    VOICE_TRANSCRIPT_CHANNEL_ID: "444444444444444444",
    VOICE_DATA_DIR: "/tmp/test",
  };
  assert.equal(readConfig(env).stt.mode, "local");
  assert.throws(() => readConfig({ ...env, DISCORD_OWNER_ID: "" }), /OWNER/);
  assert.throws(
    () =>
      readConfig({ ...env, VOICE_CHANNEL_ID: env.VOICE_TRANSCRIPT_CHANNEL_ID }),
    /different/,
  );
  assert.throws(() => readConfig({ ...env, VOICE_GUILD_ID: "bad" }), /GUILD/);
});
