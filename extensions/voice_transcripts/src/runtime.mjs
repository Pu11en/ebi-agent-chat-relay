import { mkdirSync, writeFileSync, renameSync, rmSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { createPresenceController } from "./presence.mjs";

export function createRuntime({
  config,
  store,
  service,
  transport,
  ownerChannel,
  getVoice,
  getOutput,
  controls,
}) {
  const exportDir = join(config.dataDir, "transcripts");
  mkdirSync(exportDir, { recursive: true, mode: 0o700 });
  async function notice(resumed = false) {
    const channel = await getVoice();
    if (channel.guildId !== config.guildId || channel.id !== config.channelId)
      throw new Error("Voice channel mismatch");
    const message = await channel.send({
      content: `🔴 **Automatic transcription ${resumed ? "resumed" : "started"}.** Everyone speaking here is transcribed while <@${config.ownerId}> is present. Speech recognition runs locally. Speaker names and timestamps are saved to a private transcript channel. Local copies are kept for ${config.retentionDays} days; Discord copies remain until deleted. Use **Pause transcription** below or leave the room to stop participating.`,
      components: controls(),
      allowedMentions: { parse: [] },
    });
    return { channel, message };
  }
  async function connect(session) {
    await transport.connect(session);
  }
  const controller = createPresenceController({
    channelId: config.channelId,
    ownerChannel,
    active: () => service.active(),
    isPaused: () => store.getSetting("paused") === "true",
    setPaused: (value) => store.setSetting("paused", String(value)),
    start: async () => {
      const { channel, message } = await notice();
      if (ownerChannel() !== config.channelId) return;
      const { session } = service.start({
        channelId: channel.id,
        channelName: channel.name,
        startedBy: config.ownerId,
        disclosureMessageId: message.id,
      });
      try {
        await connect(session);
      } catch (error) {
        transport.disconnect();
        service.stop();
        throw error;
      }
    },
    stop: async () => {
      transport.disconnect();
      service.stop();
    },
    reconnect: async () => {
      if (transport.ready()) return;
      await notice(true);
      if (ownerChannel() === config.channelId) await connect(service.active());
    },
  });
  let publishing = null;
  async function publishAll() {
    const sessions = store.listSessions(config.guildId);
    let output = null;
    for (const session of sessions) {
      const markdown = service.exportSession(session);
      const hash = createHash("sha256").update(markdown).digest("hex");
      const previous = store.getPublication(session.id);
      const path = join(exportDir, session.id + ".md");
      writeFileSync(path + ".tmp", markdown, { mode: 0o600 });
      renameSync(path + ".tmp", path);
      if (previous?.content_hash === hash) continue;
      output ??= await getOutput();
      if (
        !output?.isTextBased() ||
        output.guildId !== config.guildId ||
        output.id !== config.transcriptChannelId
      )
        throw new Error("Transcript channel mismatch");
      const pending = store.pendingJobCount(session.id),
        failed = store.failedJobCount(session.id);
      const payload = {
        content: `**Voice transcript — ${session.status === "recording" ? "recording" : "stopped"}**\n<#${session.channel_id}> · started ${session.started_at}\n${pending} pending · ${failed} failed. The attached transcript updates as speech is processed. Times are UTC.`,
        allowedMentions: { parse: [] },
        attachments: [],
        files: [
          {
            attachment: Buffer.from(markdown),
            name: "voice-transcript-" + session.id + ".md",
          },
        ],
      };
      let message;
      if (previous) {
        try {
          message = await output.messages.edit(previous.message_id, payload);
        } catch (error) {
          if (error.code !== 10008) throw error;
        }
      }
      message ??= await output.send(payload);
      store.savePublication(session.id, message.id, hash);
    }
  }
  return {
    controller,
    publish() {
      publishing ??= publishAll().finally(() => {
        publishing = null;
      });
      return publishing;
    },
    prune() {
      const cutoff = Date.now() - config.retentionDays * 86400000;
      for (const session of store.listSessions(config.guildId)) {
        if (
          session.status === "stopped" &&
          Date.parse(session.ended_at) < cutoff
        )
          rmSync(join(exportDir, session.id + ".md"), { force: true });
      }
      service.prune();
    },
  };
}
