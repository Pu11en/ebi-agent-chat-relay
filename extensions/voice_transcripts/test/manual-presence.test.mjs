import { test } from "node:test";
import assert from "node:assert/strict";
import { createPresenceController } from "../src/presence.mjs";

function rig(overrides = {}) {
  let owner = "room",
    active = null,
    paused = false,
    connected = false;
  const calls = [];
  const io = {
    channelId: "room",
    ownerChannel: () => owner,
    active: () => active,
    isPaused: () => paused,
    setPaused: (value) => {
      paused = value;
    },
    connected: () => connected,
    disconnect: () => {
      calls.push("disconnect");
      connected = false;
    },
    start: async (mayJoin = () => true) => {
      if (!mayJoin()) return;
      calls.push("start");
      active = { id: "recording" };
      connected = true;
    },
    reconnect: async (mayJoin = () => true) => {
      if (!mayJoin()) return;
      calls.push("connect");
      connected = true;
    },
    stop: async () => {
      calls.push("stop");
      active = null;
      connected = false;
    },
    ...overrides,
  };
  return {
    controller: createPresenceController(io),
    calls,
    owner: (value) => {
      owner = value;
    },
    active: (value) => {
      active = value;
    },
    drop: () => {
      connected = false;
    },
  };
}

test("owner presence and repeated health checks never auto-join", async () => {
  const r = rig();
  for (let i = 0; i < 5; i++) await r.controller.reconcile();
  assert.deepEqual(r.calls, []);
  r.owner(null);
  await r.controller.reconcile();
  r.owner("room");
  await r.controller.reconcile();
  assert.deepEqual(r.calls, []);
});

test("an explicit join connects once; repeated joins do not duplicate recordings", async () => {
  const r = rig();
  assert.equal((await r.controller.join()).status, "joined");
  await r.controller.join();
  await r.controller.reconcile();
  assert.deepEqual(r.calls, ["start"]);
});

test("leave disconnects immediately and health checks cannot rejoin", async () => {
  const r = rig();
  await r.controller.join();
  const leaving = r.controller.leave();
  assert.equal(
    r.calls.at(-1),
    "disconnect",
    "disconnect must not wait behind the queue",
  );
  assert.equal((await leaving).status, "left");
  for (let i = 0; i < 3; i++) await r.controller.reconcile();
  assert.equal(r.calls.filter((x) => x === "start").length, 1);
});

test("owner departure clears the request; returning requires another join", async () => {
  const r = rig();
  await r.controller.join();
  r.owner(null);
  await r.controller.reconcile();
  r.owner("room");
  await r.controller.reconcile();
  assert.equal(r.calls.at(-1), "stop");
  await r.controller.join();
  assert.equal(r.calls.filter((x) => x === "start").length, 2);
});

test("join outside the recording room does not arm a future auto-join", async () => {
  const r = rig();
  r.owner("elsewhere");
  assert.equal((await r.controller.join()).status, "owner-absent");
  r.owner("room");
  await r.controller.reconcile();
  assert.deepEqual(r.calls, []);
});

test("a recovered recording is stopped on startup even with its owner present", async () => {
  const r = rig();
  r.active({ id: "before-restart" });
  await r.controller.reconcile();
  assert.deepEqual(r.calls, ["stop"]);
});

test("connection loss is not permission to reconnect", async () => {
  const r = rig();
  await r.controller.join();
  r.drop();
  await r.controller.reconcile();
  await r.controller.reconcile();
  assert.deepEqual(r.calls, ["start", "stop"]);
});

test("pause and the old resume method do not bypass the join command", async () => {
  const r = rig();
  await r.controller.join();
  await r.controller.pause();
  await r.controller.resume();
  await r.controller.reconcile();
  assert.equal(r.calls.filter((x) => x === "start").length, 1);
  await r.controller.join();
  assert.equal(r.calls.filter((x) => x === "start").length, 2);
});

test("leave cancels a queued join before it gets a chance to start", async () => {
  const r = rig();
  const joining = r.controller.join();
  const leaving = r.controller.leave();
  await Promise.all([joining, leaving]);
  await r.controller.reconcile();
  assert.ok(!r.calls.includes("start"));
});

test("leave interrupts a slow connection without waiting for its timeout", async () => {
  let rejectConnect;
  let began;
  const started = new Promise((resolve) => {
    began = resolve;
  });
  let disconnected = false;
  const r = rig({
    start: async () => {
      began();
      await new Promise((resolve, reject) => {
        rejectConnect = reject;
      });
    },
    disconnect: () => {
      disconnected = true;
      rejectConnect(new Error("destroyed"));
    },
  });
  const joining = r.controller.join();
  await started;
  const leaving = r.controller.leave();
  assert.equal(disconnected, true);
  assert.equal((await joining).status, "left");
  await leaving;
});

test("leave during disclosure prevents a later connect", async () => {
  let finishNotice;
  let began;
  const started = new Promise((resolve) => {
    began = resolve;
  });
  let connected = false;
  const r = rig({
    start: async (mayJoin) => {
      began();
      await new Promise((resolve) => {
        finishNotice = resolve;
      });
      if (mayJoin()) connected = true;
    },
  });
  const joining = r.controller.join();
  await started;
  const leaving = r.controller.leave();
  finishNotice();
  await Promise.all([joining, leaving]);
  assert.equal(connected, false);
});

test("a failed join is reported once and never retried by health checks", async () => {
  let attempts = 0;
  const r = rig({
    start: async () => {
      attempts++;
      throw new Error("no permission");
    },
  });
  await assert.rejects(r.controller.join(), /permission/);
  await r.controller.reconcile();
  assert.equal(attempts, 1);
});
