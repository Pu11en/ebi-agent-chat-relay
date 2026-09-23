import { createPcmBuffer } from "./capture.mjs";
import { BYTES_PER_SECOND, pcmDurationMs, pcmToWav } from "./voice/wav.mjs";

export function createVoiceTransport({
  config,
  client,
  service,
  voice,
  createDecoder,
  mayCapture,
  logger = console,
}) {
  let connection = null;
  const captures = new Map();
  function capture(session, userId) {
    if (
      captures.has(userId) ||
      userId === client.user.id ||
      !mayCapture() ||
      service.active()?.id !== session.id
    )
      return;
    const guild = client.guilds.cache.get(config.guildId);
    const member = guild?.members.cache.get(userId);
    if (member?.user?.bot || client.users.cache.get(userId)?.bot) return;
    const displayName =
      member?.displayName || client.users.cache.get(userId)?.username || userId;
    const opus = connection.receiver.subscribe(userId, {
      end: {
        behavior: voice.EndBehaviorType.AfterSilence,
        duration: config.silenceMs,
      },
    });
    const decoder = createDecoder();
    let finished = false;
    let capturedAt = new Date().toISOString();
    const buffer = createPcmBuffer({
      maxBytes: config.maxUtteranceSeconds * BYTES_PER_SECOND,
      minBytes: Math.ceil((config.minUtteranceMs * BYTES_PER_SECOND) / 1000),
      onChunk: (pcm) => {
        logger.info(
          "[voice] captured " + displayName + " " + pcmDurationMs(pcm.length) + "ms",
        );
        service.enqueueUtterance(
          {
            sessionId: session.id,
            userId,
            displayName,
            capturedAt,
            durationMs: pcmDurationMs(pcm.length),
          },
          pcmToWav(pcm),
        );
        capturedAt = new Date().toISOString();
      },
    });
    function finish(error) {
      if (finished) return;
      finished = true;
      captures.delete(userId);
      opus.unpipe(decoder);
      if (receivedBytes > 0 || error)
        logger.info("[voice] stream ended " + displayName + " pcm=" + receivedBytes + "B");
      try {
        buffer.flush();
      } catch (captureError) {
        logger.error("[voice] unable to persist audio:", captureError.message);
      }
      opus.destroy();
      decoder.destroy();
      if (error) logger.warn("[voice] speaker stream failed:", error.message);
    }
    captures.set(userId, finish);
    let receivedBytes = 0;
    decoder.on("data", (chunk) => {
      receivedBytes += chunk.length;
      if (!mayCapture()) {
        finish();
        return;
      }
      try {
        buffer.write(chunk);
      } catch (error) {
        finish(error);
      }
    });
    decoder.once("end", () => finish());
    decoder.once("close", () => finish());
    decoder.once("error", finish);
    opus.once("error", finish);
    opus.once("close", () => finish());
    opus.pipe(decoder);
  }
  const disconnect = () => {
    for (const finish of [...captures.values()]) finish();
    if (
      connection &&
      connection.state.status !== voice.VoiceConnectionStatus.Destroyed
    )
      connection.destroy();
    connection = null;
  };
  return {
    ready: () => connection?.state.status === voice.VoiceConnectionStatus.Ready,
    async connect(session) {
      disconnect();
      const guild = client.guilds.cache.get(config.guildId);
      if (!guild) throw new Error("Configured guild is unavailable");
      connection = voice.joinVoiceChannel({
        channelId: config.channelId,
        guildId: config.guildId,
        adapterCreator: guild.voiceAdapterCreator,
        selfDeaf: false,
        selfMute: true,
        debug: true,
      });
      // @discordjs/voice drops packets it can't E2EE-decrypt and only says so
      // on its debug channel — surface DAVE/decrypt events so lost speech is
      // visible in the journal instead of silently missing from transcripts.
      connection.on("debug", (message) => {
        if (/decrypt|dave|transition|epoch|mls|session (re|in|down|up)/i.test(message))
          logger.warn("[voice] debug:", message);
      });
      connection.on("error", (error) =>
        logger.warn("[voice] connection:", error.message),
      );
      try {
        await voice.entersState(
          connection,
          voice.VoiceConnectionStatus.Ready,
          20000,
        );
      } catch (error) {
        disconnect();
        throw error;
      }
      connection.receiver.speaking.on("start", (userId) =>
        capture(session, userId),
      );
      logger.info("[voice] connected session=" + session.id);
    },
    disconnect,
  };
}
