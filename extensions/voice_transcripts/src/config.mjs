import { isAbsolute } from "node:path";

export function parseEnv(text) {
  return Object.fromEntries(
    text.split(/\r?\n/).flatMap((line) => {
      const match = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z_0-9]*)=(.*)$/);
      if (!match) return [];
      let value = match[2].trim();
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      )
        value = value.slice(1, -1);
      return [[match[1], value]];
    }),
  );
}
export function readConfig(env) {
  const id = (key) => {
    if (!/^\d{17,20}$/.test(env[key] || ""))
      throw new Error(`Missing or invalid ${key}`);
    return env[key];
  };
  const number = (key, fallback, min, max) => {
    const value = Number(env[key] ?? fallback);
    if (!Number.isInteger(value) || value < min || value > max)
      throw new Error(`Invalid ${key}`);
    return value;
  };
  if (!env.DISCORD_BOT_TOKEN) throw new Error("Missing DISCORD_BOT_TOKEN");
  if (!isAbsolute(env.VOICE_DATA_DIR || ""))
    throw new Error("VOICE_DATA_DIR must be absolute");
  const config = {
    token: env.DISCORD_BOT_TOKEN,
    ownerId: id("DISCORD_OWNER_ID"),
    guildId: id("VOICE_GUILD_ID"),
    channelId: id("VOICE_CHANNEL_ID"),
    transcriptChannelId: id("VOICE_TRANSCRIPT_CHANNEL_ID"),
    dataDir: env.VOICE_DATA_DIR,
    retentionDays: number("VOICE_RETENTION_DAYS", 30, 1, 365),
    silenceMs: 1200,
    minUtteranceMs: 350,
    maxUtteranceSeconds: 20,
    stt: {
      mode: "local",
      python: env.VOICE_PYTHON || "/usr/bin/python3",
      model: env.VOICE_MODEL || "base.en",
      provider: "local faster-whisper",
      timeoutMs: 120000,
      maxAttempts: 5,
    },
  };
  if (config.channelId === config.transcriptChannelId)
    throw new Error("Voice and transcript channels must be different");
  return config;
}
