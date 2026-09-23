// Explicit provisioning command. Does not run during normal worker startup.
import { readFileSync, writeFileSync } from "node:fs";
import { parseEnv } from "../src/config.mjs";
const bridge = parseEnv(
  readFileSync(process.env.VOICE_BRIDGE_ENV_FILE, "utf8"),
);
const {
  VOICE_GUILD_ID: guildId,
  VOICE_CATEGORY_ID: parentId,
  VOICE_SETUP_OUTPUT: output,
} = process.env;
if (
  !/^\d{17,20}$/.test(guildId || "") ||
  !/^\d{17,20}$/.test(parentId || "") ||
  !output?.startsWith("/")
)
  throw new Error("Supply guild/category IDs and an absolute output file");
async function api(route, method = "GET", body) {
  const response = await fetch("https://discord.com/api/v10" + route, {
    method,
    headers: {
      Authorization: "Bot " + bridge.DISCORD_BOT_TOKEN,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok)
    throw new Error(
      "Discord " + method + " " + route + " returned " + response.status,
    );
  return response.status === 204 ? null : response.json();
}
const identity = await api("/users/@me");
const parent = await api("/channels/" + parentId);
if (parent.guild_id !== guildId || parent.type !== 4)
  throw new Error("Category does not belong to the specified guild");
const channels = await api("/guilds/" + guildId + "/channels");
if (
  channels.some((channel) =>
    ["🎙️ Auto Transcripts", "voice-transcripts"].includes(channel.name),
  )
)
  throw new Error(
    "A target channel already exists; inspect it before provisioning again",
  );
const voice = await api("/guilds/" + guildId + "/channels", "POST", {
  name: "🎙️ Auto Transcripts",
  type: 2,
  parent_id: parentId,
  permission_overwrites: [],
});
const state = {
  botId: identity.id,
  guildId,
  ownerId: bridge.DISCORD_OWNER_ID,
  voiceChannelId: voice.id,
};
writeFileSync(output, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
const view = 1024n,
  send = 2048n,
  history = 65536n,
  attach = 32768n;
const text = await api("/guilds/" + guildId + "/channels", "POST", {
  name: "voice-transcripts",
  type: 0,
  parent_id: parentId,
  topic:
    "Private speaker-attributed transcripts from Auto Transcripts. Local recognition; one updating Markdown file per session.",
  permission_overwrites: [
    { id: guildId, type: 0, deny: String(view), allow: "0" },
    {
      id: bridge.DISCORD_OWNER_ID,
      type: 1,
      allow: String(view + send + history + attach),
      deny: "0",
    },
    {
      id: identity.id,
      type: 1,
      allow: String(view + send + history + attach),
      deny: "0",
    },
  ],
});
state.transcriptChannelId = text.id;
writeFileSync(output, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
const notice = await api("/channels/" + voice.id + "/messages", "POST", {
  content:
    "**This room automatically makes transcripts.**\nWhen <@" +
    bridge.DISCORD_OWNER_ID +
    "> joins, Drew AI joins muted and transcribes everyone speaking. When Drew leaves, transcription stops. Speaker names and UTC timestamps are saved in a private transcript channel. Speech recognition runs locally. Local copies are retained for 30 days; Discord copies remain until deleted.\n\nA recording notice appears for each session. Anyone in this room can use its **Pause transcription** button. Use another room if you do not want to be transcribed.",
  allowed_mentions: { parse: [] },
});
state.noticeMessageId = notice.id;
writeFileSync(output, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
console.log(JSON.stringify(state));
