import { test } from "node:test";
import assert from "node:assert/strict";
import { createVoiceTextCommands } from "../src/manual-commands.mjs";

const config = {
  guildId: "guild",
  transcriptChannelId: "text",
  channelId: "voice",
  ownerId: "owner",
};
function rig(overrides = {}) {
  const actions = [],
    replies = [];
  const controller = {
    join: async () => {
      actions.push("join");
      return { status: "joined" };
    },
    leave: async () => {
      actions.push("leave");
      return { status: "left" };
    },
    ...overrides,
  };
  const handle = createVoiceTextCommands({
    config,
    controller,
    logger: { error() {} },
  });
  return {
    actions,
    replies,
    say: (content, extra = {}) =>
      handle({
        content,
        guildId: "guild",
        channelId: "text",
        author: { id: "owner", bot: false },
        reply: async (payload) => replies.push(payload),
        ...extra,
      }),
  };
}

test("join and leave are exact owner commands with a visible response", async () => {
  const r = rig();
  await r.say(" !voice JOIN ");
  await r.say("!voice leave");
  assert.deepEqual(r.actions, ["join", "leave"]);
  assert.match(r.replies[0].content, /joined/i);
  assert.match(r.replies[1].content, /left/i);
  assert.deepEqual(r.replies[1].allowedMentions, {
    parse: [],
    repliedUser: false,
  });
});

test("other users, bots, webhooks, guilds and channels cannot control voice", async () => {
  const r = rig();
  for (const extra of [
    { author: { id: "guest" } },
    { author: { id: "owner", bot: true } },
    { webhookId: "hook" },
    { guildId: "other" },
    { guildId: null },
    { channelId: "other" },
    { channelId: "thread-under-text" },
  ]) {
    await r.say("!voice join", extra);
    await r.say("!voice leave", extra);
  }
  assert.deepEqual(r.actions, []);
  assert.deepEqual(r.replies, []);
});

test("ordinary speech and quoted or compound commands are ignored", async () => {
  const r = rig();
  for (const text of [
    "voice join",
    "please !voice join",
    "!voice join and run tests",
    "!voice leave\n!voice join",
    "!voice delete",
    "Jester join",
  ])
    await r.say(text);
  assert.deepEqual(r.actions, []);
});

test("bare command explains usage without joining", async () => {
  const r = rig();
  await r.say("!voice");
  assert.deepEqual(r.actions, []);
  assert.match(r.replies[0].content, /!voice join.*!voice leave/);
});

test("owner must be present to join, but can always request leave", async () => {
  const r = rig({ join: async () => ({ status: "owner-absent" }) });
  await r.say("!voice join");
  assert.match(r.replies[0].content, /join.*<#voice>.*first/i);
  await r.say("!voice leave");
  assert.deepEqual(r.actions, ["leave"]);
});

test("canceled joining is not reported as success", async () => {
  const r = rig({ join: async () => ({ status: "left" }) });
  await r.say("!voice join");
  assert.match(r.replies[0].content, /cancel|stayed out/i);
});

test("failed actions do not leak error details or silently retry", async () => {
  let attempts = 0;
  const r = rig({
    join: async () => {
      attempts++;
      throw new Error("private token path");
    },
  });
  await r.say("!voice join");
  assert.equal(attempts, 1);
  assert.match(r.replies[0].content, /failed/i);
  assert.ok(!r.replies[0].content.includes("private token path"));
});
