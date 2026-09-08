import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createStore } from "../src/voice/store.mjs";
import { createTranscriptService } from "../src/voice/service.mjs";
import { createRuntime } from "../src/runtime.mjs";

function fixture(t, failNotice = false) {
  const dir = mkdtempSync(join(tmpdir(), "voice-runtime-"));
  const store = createStore(dir);
  const calls = [];
  const config = {
    guildId: "g",
    channelId: "v",
    transcriptChannelId: "text",
    ownerId: "owner",
    dataDir: dir,
    retentionDays: 30,
    stt: { provider: "local" },
  };
  let owner = "v";
  let ready = false;
  const service = createTranscriptService({
    store,
    queue: {},
    config,
    guildId: "g",
  });
  const voice = {
    id: "v",
    guildId: "g",
    name: "talk",
    send: async () => {
      calls.push("notice");
      if (failNotice) throw new Error("no send permission");
      return { id: "notice" };
    },
  };
  const transport = {
    connect: async () => {
      calls.push("connect");
      ready = true;
    },
    ready: () => ready,
    disconnect: () => {
      calls.push("disconnect");
      ready = false;
    },
  };
  const output = {
    id: "text",
    guildId: "g",
    isTextBased: () => true,
    send: async (payload) => {
      calls.push(["publish", payload]);
      return { id: "out" };
    },
    messages: {
      edit: async (id, payload) => {
        calls.push(["edit", payload]);
        return { id };
      },
    },
  };
  const runtime = createRuntime({
    config,
    store,
    service,
    transport,
    ownerChannel: () => owner,
    getVoice: async () => voice,
    getOutput: async () => output,
    controls: () => [],
    logger: { info() {}, warn() {}, error() {} },
  });
  t.after(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });
  return {
    runtime,
    store,
    service,
    calls,
    dir,
    owner: (value) => {
      owner = value;
    },
  };
}
test("disclosure is sent before voice connects; absent owner stops a recovered session", async (t) => {
  const f = fixture(t);
  await f.runtime.controller.reconcile();
  assert.deepEqual(f.calls, ["notice", "connect"]);
  assert.ok(f.service.active());
  f.owner(null);
  await f.runtime.controller.reconcile();
  assert.equal(f.service.active(), null);
  assert.ok(f.calls.includes("disconnect"));
});
test("failed disclosure blocks recording and voice receive", async (t) => {
  const f = fixture(t, true);
  await assert.rejects(f.runtime.controller.reconcile(), /permission/);
  assert.equal(f.service.active(), null);
  assert.deepEqual(f.calls, ["notice"]);
});
test("publication persists and is edited when delayed jobs finish after stop", async (t) => {
  const f = fixture(t);
  await f.runtime.controller.reconcile();
  const session = f.service.active();
  f.store.enqueueJob({
    id: "j",
    sessionId: session.id,
    userId: "u",
    displayName: "Alice",
    capturedAt: new Date().toISOString(),
    durationMs: 1000,
    audioPath: "/unused",
    createdAt: new Date().toISOString(),
  });
  f.owner(null);
  await f.runtime.controller.reconcile();
  await f.runtime.publish();
  assert.equal(
    f.calls.filter((x) => Array.isArray(x) && x[0] === "publish").length,
    1,
  );
  f.store.completeJob("j", {
    text: "words after stopping",
    provider: "local",
    model: "base.en",
  });
  await f.runtime.publish();
  assert.equal(
    f.calls.filter((x) => Array.isArray(x) && x[0] === "edit").length,
    1,
  );
  assert.match(
    readFileSync(join(f.dir, "transcripts", session.id + ".md"), "utf8"),
    /words after stopping/,
  );
  await f.runtime.publish();
  assert.equal(
    f.calls.filter((x) => Array.isArray(x) && x[0] === "edit").length,
    1,
  );
  assert.equal(f.store.getPublication(session.id).message_id, "out");
});
test("paused setting survives a reopened store", async (t) => {
  const f = fixture(t);
  await f.runtime.controller.reconcile();
  await f.runtime.controller.pause();
  const reopened = createStore(f.dir);
  assert.equal(reopened.getSetting("paused"), "true");
  reopened.close();
});
