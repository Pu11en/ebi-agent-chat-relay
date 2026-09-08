import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";

export function createStore(dataDir) {
  mkdirSync(dataDir, { recursive: true });
  const db = new DatabaseSync(join(dataDir, "transcripts.sqlite"));
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      guild_id TEXT NOT NULL,
      channel_id TEXT NOT NULL,
      channel_name TEXT NOT NULL,
      started_by TEXT NOT NULL,
      started_at TEXT NOT NULL,
      ended_at TEXT,
      status TEXT NOT NULL CHECK(status IN ('recording', 'stopped', 'deleted')),
      disclosure_message_id TEXT
    );
    CREATE INDEX IF NOT EXISTS sessions_guild_started
      ON sessions(guild_id, started_at DESC);

    CREATE TABLE IF NOT EXISTS runtime_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publications (
      session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
      message_id TEXT NOT NULL, content_hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS segments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      user_id TEXT NOT NULL,
      display_name TEXT NOT NULL,
      captured_at TEXT NOT NULL,
      duration_ms INTEGER NOT NULL,
      text TEXT NOT NULL,
      provider TEXT NOT NULL,
      model TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS segments_session_captured
      ON segments(session_id, captured_at, id);

    CREATE TABLE IF NOT EXISTS transcription_jobs (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      user_id TEXT NOT NULL,
      display_name TEXT NOT NULL,
      captured_at TEXT NOT NULL,
      duration_ms INTEGER NOT NULL,
      audio_path TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      available_at TEXT NOT NULL,
      last_error TEXT,
      status TEXT NOT NULL CHECK(status IN ('pending', 'failed')),
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS jobs_ready
      ON transcription_jobs(status, available_at, created_at);
  `);

  const statements = {
    active: db.prepare(
      "SELECT * FROM sessions WHERE guild_id = ? AND status = 'recording' ORDER BY started_at DESC LIMIT 1",
    ),
    session: db.prepare(
      "SELECT * FROM sessions WHERE id = ? AND guild_id = ? AND status != 'deleted'",
    ),
    latest: db.prepare(
      "SELECT * FROM sessions WHERE guild_id = ? AND status != 'deleted' ORDER BY started_at DESC LIMIT 1",
    ),
    segments: db.prepare(
      "SELECT * FROM segments WHERE session_id = ? ORDER BY captured_at, id",
    ),
    readyJobs: db.prepare(
      "SELECT * FROM transcription_jobs WHERE status = 'pending' AND available_at <= ? ORDER BY created_at LIMIT ?",
    ),
    job: db.prepare("SELECT * FROM transcription_jobs WHERE id = ?"),
  };

  return {
    dbPath: join(dataDir, "transcripts.sqlite"),
    getSetting(key) {
      return (
        db.prepare("SELECT value FROM runtime_settings WHERE key = ?").get(key)
          ?.value ?? null
      );
    },
    setSetting(key, value) {
      db.prepare("INSERT OR REPLACE INTO runtime_settings VALUES (?, ?)").run(
        key,
        value,
      );
    },
    listSessions(guildId) {
      return db
        .prepare(
          "SELECT * FROM sessions WHERE guild_id = ? AND status != 'deleted' ORDER BY started_at",
        )
        .all(guildId);
    },
    getPublication(id) {
      return (
        db.prepare("SELECT * FROM publications WHERE session_id = ?").get(id) ||
        null
      );
    },
    savePublication(id, messageId, hash) {
      db.prepare("INSERT OR REPLACE INTO publications VALUES (?, ?, ?)").run(
        id,
        messageId,
        hash,
      );
    },
    createSession({
      guildId,
      channelId,
      channelName,
      startedBy,
      disclosureMessageId = null,
      now = new Date().toISOString(),
    }) {
      const id = randomUUID();
      db.prepare(
        `INSERT INTO sessions
        (id, guild_id, channel_id, channel_name, started_by, started_at, status, disclosure_message_id)
        VALUES (?, ?, ?, ?, ?, ?, 'recording', ?)`,
      ).run(
        id,
        guildId,
        channelId,
        channelName,
        startedBy,
        now,
        disclosureMessageId,
      );
      return statements.session.get(id, guildId);
    },
    getActiveSession(guildId) {
      return statements.active.get(guildId) || null;
    },
    getSession(id, guildId) {
      return statements.session.get(id, guildId) || null;
    },
    getLatestSession(guildId) {
      return statements.latest.get(guildId) || null;
    },
    stopSession(id, now = new Date().toISOString()) {
      db.prepare(
        "UPDATE sessions SET status = 'stopped', ended_at = ? WHERE id = ? AND status = 'recording'",
      ).run(now, id);
    },
    setDisclosureMessage(id, messageId) {
      db.prepare(
        "UPDATE sessions SET disclosure_message_id = ? WHERE id = ?",
      ).run(messageId, id);
    },
    listSegments(sessionId) {
      return statements.segments.all(sessionId);
    },
    enqueueJob(job) {
      db.prepare(
        `INSERT INTO transcription_jobs
        (id, session_id, user_id, display_name, captured_at, duration_ms, audio_path, available_at, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)`,
      ).run(
        job.id,
        job.sessionId,
        job.userId,
        job.displayName,
        job.capturedAt,
        job.durationMs,
        job.audioPath,
        job.createdAt,
        job.createdAt,
      );
    },
    listReadyJobs(now = new Date().toISOString(), limit = 10) {
      return statements.readyJobs.all(now, limit);
    },
    getJob(id) {
      return statements.job.get(id) || null;
    },
    completeJob(
      id,
      { text, provider, model, createdAt = new Date().toISOString() },
    ) {
      const job = statements.job.get(id);
      if (!job) return null;
      db.exec("BEGIN IMMEDIATE");
      try {
        if (String(text).trim())
          db.prepare(
            `INSERT INTO segments
          (session_id, user_id, display_name, captured_at, duration_ms, text, provider, model, created_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          ).run(
            job.session_id,
            job.user_id,
            job.display_name,
            job.captured_at,
            job.duration_ms,
            text,
            provider,
            model,
            createdAt,
          );
        db.prepare("DELETE FROM transcription_jobs WHERE id = ?").run(id);
        db.exec("COMMIT");
      } catch (error) {
        db.exec("ROLLBACK");
        throw error;
      }
      return job;
    },
    failJob(id, error, { maxAttempts, nextAttemptAt }) {
      const job = statements.job.get(id);
      if (!job) return null;
      const attempts = job.attempts + 1;
      const status = attempts >= maxAttempts ? "failed" : "pending";
      db.prepare(
        "UPDATE transcription_jobs SET attempts = ?, status = ?, available_at = ?, last_error = ? WHERE id = ?",
      ).run(attempts, status, nextAttemptAt, String(error).slice(0, 1000), id);
      return { ...job, attempts, status };
    },
    pendingJobCount(sessionId) {
      return db
        .prepare(
          "SELECT COUNT(*) AS count FROM transcription_jobs WHERE session_id = ? AND status = 'pending'",
        )
        .get(sessionId).count;
    },
    failedJobCount(sessionId) {
      return db
        .prepare(
          "SELECT COUNT(*) AS count FROM transcription_jobs WHERE session_id = ? AND status = 'failed'",
        )
        .get(sessionId).count;
    },
    deleteSession(id, guildId) {
      const audioPaths = db
        .prepare(
          "SELECT audio_path FROM transcription_jobs WHERE session_id = ?",
        )
        .all(id)
        .map((row) => row.audio_path);
      const result = db
        .prepare(
          "UPDATE sessions SET status = 'deleted' WHERE id = ? AND guild_id = ? AND status != 'recording'",
        )
        .run(id, guildId);
      if (result.changes) {
        db.prepare("DELETE FROM transcription_jobs WHERE session_id = ?").run(
          id,
        );
        db.prepare("DELETE FROM segments WHERE session_id = ?").run(id);
      }
      return { deleted: result.changes > 0, audioPaths };
    },
    prune(guildId, cutoff) {
      const ids = db
        .prepare(
          "SELECT id FROM sessions WHERE guild_id = ? AND status = 'stopped' AND ended_at < ?",
        )
        .all(guildId, cutoff)
        .map((row) => row.id);
      const audioPaths = [];
      for (const id of ids) {
        audioPaths.push(
          ...db
            .prepare(
              "SELECT audio_path FROM transcription_jobs WHERE session_id = ?",
            )
            .all(id)
            .map((row) => row.audio_path),
        );
        db.prepare("DELETE FROM sessions WHERE id = ?").run(id);
      }
      return { deletedSessions: ids.length, audioPaths };
    },
    close() {
      db.close();
    },
  };
}

export function renderTranscript(
  session,
  segments,
  { pending = 0, failed = 0 } = {},
) {
  const lines = [
    `# Voice transcript`,
    "",
    `- Session: \`${session.id}\``,
    `- Channel: ${session.channel_name}`,
    `- Started: ${session.started_at}`,
    `- Ended: ${session.ended_at || "still recording"}`,
    `- Pending transcription jobs: ${pending}`,
    `- Failed transcription jobs: ${failed}`,
    "",
  ];
  for (const segment of segments) {
    const time = new Date(segment.captured_at).toISOString().slice(11, 19);
    const name = String(segment.display_name).replace(/[\r\n]/g, " ");
    const text = String(segment.text)
      .replace(/[\r\n]+/g, " ")
      .trim();
    lines.push(`**${time} — ${name}:** ${text}`, "");
  }
  if (!segments.length) lines.push("_No completed transcript segments._", "");
  return `${lines.join("\n")}\n`;
}
