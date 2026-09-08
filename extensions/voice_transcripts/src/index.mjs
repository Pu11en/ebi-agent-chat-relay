import { readFileSync, mkdirSync } from "node:fs";
import { join, isAbsolute } from "node:path";
import {
  Client,
  GatewayIntentBits,
  Events,
  ChannelType,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  MessageFlags,
} from "discord.js";
import * as voice from "@discordjs/voice";
import prism from "prism-media";
import { parseEnv, readConfig } from "./config.mjs";
import { canControl } from "./presence.mjs";
import { createVoiceTransport } from "./transport.mjs";
import { createRuntime } from "./runtime.mjs";
import { createStore } from "./voice/store.mjs";
import { createJobQueue } from "./voice/job-queue.mjs";
import { createTranscriptService } from "./voice/service.mjs";
import { createTranscriber } from "./voice/transcriber.mjs";

process.umask(0o077);
function readEnvFile(key) {
  const path = process.env[key];
  if (!path || !isAbsolute(path))
    throw new Error(key + " must name an absolute env-file path");
  return parseEnv(readFileSync(path, "utf8"));
}
// Read only the identity from the existing bridge. Never log or forward its secrets.
const bridge = readEnvFile("VOICE_BRIDGE_ENV_FILE");
const config = readConfig({
  ...readEnvFile("VOICE_CONFIG_FILE"),
  DISCORD_BOT_TOKEN: bridge.DISCORD_BOT_TOKEN,
  DISCORD_OWNER_ID: bridge.DISCORD_OWNER_ID,
});
mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
const store = createStore(config.dataDir);
const transcribe = createTranscriber(config.stt);
const queue = createJobQueue({
  store,
  queueDir: join(config.dataDir, "audio-queue"),
  transcribe,
  provider: config.stt.provider,
  model: config.stt.model,
  maxAttempts: config.stt.maxAttempts,
});
const service = createTranscriptService({
  store,
  queue,
  config,
  guildId: config.guildId,
});
const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
  allowedMentions: { parse: [] },
});
const ownerChannel = () =>
  client.guilds.cache.get(config.guildId)?.voiceStates.cache.get(config.ownerId)
    ?.channelId ?? null;
const transport = createVoiceTransport({
  config,
  client,
  service,
  voice,
  createDecoder: () =>
    new prism.opus.Decoder({ rate: 48000, channels: 2, frameSize: 960 }),
  mayCapture: () =>
    client.isReady() &&
    ownerChannel() === config.channelId &&
    store.getSetting("paused") !== "true",
});
const controls = () => [
  new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId("ccdb-voice:pause")
      .setLabel("Pause transcription")
      .setStyle(ButtonStyle.Danger),
    new ButtonBuilder()
      .setCustomId("ccdb-voice:resume")
      .setLabel("Resume transcription")
      .setStyle(ButtonStyle.Secondary),
  ),
];
const runtime = createRuntime({
  config,
  store,
  service,
  transport,
  ownerChannel,
  controls,
  getVoice: async () => {
    const channel = await client.channels.fetch(config.channelId);
    if (channel?.type !== ChannelType.GuildVoice)
      throw new Error("Configured voice channel is unavailable");
    return channel;
  },
  getOutput: () => client.channels.fetch(config.transcriptChannelId),
});
let closing = false,
  ticking = false,
  lastPrune = 0;
async function tick() {
  if (closing || ticking || !client.isReady()) return;
  ticking = true;
  try {
    await runtime.controller.reconcile();
    void queue
      .drain()
      .catch((error) => console.error("[voice] queue:", error.message));
    // Avoid pruning a file while the worker is using it. Prune once at startup,
    // then only when the persisted queue is empty.
    if (
      Date.now() - lastPrune > 3600000 &&
      !store.listReadyJobs("2999-01-01T00:00:00Z", 1).length
    ) {
      runtime.prune();
      lastPrune = Date.now();
    }
    await runtime.publish();
  } catch (error) {
    console.error("[voice] health:", error.message);
  } finally {
    ticking = false;
  }
}
client.on(Events.VoiceStateUpdate, (before, after) => {
  if (after.guild.id !== config.guildId) return;
  if (after.id === config.ownerId && before.channelId !== after.channelId) {
    void runtime.controller
      .reconcile()
      .catch((error) => console.error("[voice] presence:", error.message));
  }
});
client.on(Events.InteractionCreate, async (interaction) => {
  if (
    !interaction.isButton() ||
    !interaction.customId.startsWith("ccdb-voice:")
  )
    return;
  const action = interaction.customId.slice("ccdb-voice:".length);
  if (!["pause", "resume"].includes(action)) return;
  try {
    const member = {
      guildId: interaction.guildId,
      userId: interaction.user.id,
      channelId: interaction.guild?.voiceStates.cache.get(interaction.user.id)
        ?.channelId,
    };
    if (!canControl(config, member, action)) {
      await interaction.reply({
        content:
          action === "resume"
            ? "Only the configured owner can resume transcription."
            : "Join the recorded voice room to pause transcription.",
        flags: MessageFlags.Ephemeral,
      });
      return;
    }
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    await runtime.controller[action]();
    await interaction.editReply(
      action === "pause"
        ? "Transcription paused. It stays paused until the owner resumes or leaves and rejoins."
        : "Automatic transcription enabled. It starts while the owner is in the recorded room.",
    );
    await runtime.publish();
  } catch (error) {
    console.error("[voice] control:", error.message);
    if (interaction.deferred)
      await interaction
        .editReply("The transcription control failed. Please try again.")
        .catch(() => {});
  }
});
client.on(Events.ShardDisconnect, () => transport.disconnect());
client.on(Events.Error, (error) =>
  console.error("[voice] gateway:", error.message),
);
client.once(Events.ClientReady, async () => {
  console.log(
    "[voice] ready bot=" +
      client.user.id +
      " guild=" +
      config.guildId +
      " channel=" +
      config.channelId,
  );
  await tick();
});
const timer = setInterval(() => void tick(), 5000);
async function shutdown() {
  if (closing) return;
  closing = true;
  clearInterval(timer);
  transport.disconnect();
  await queue.close();
  await runtime
    .publish()
    .catch((error) =>
      console.error("[voice] final publication:", error.message),
    );
  client.destroy();
  store.close();
}
process.on("SIGTERM", () => void shutdown());
process.on("SIGINT", () => void shutdown());
try {
  await transcribe.warmup();
  await client.login(config.token);
} catch (error) {
  console.error("[voice] startup failed:", error.message);
  await shutdown();
  process.exitCode = 1;
}
