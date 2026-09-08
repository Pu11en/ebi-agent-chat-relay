import { unlinkSync } from "node:fs";
import { renderTranscript } from "./store.mjs";

export function createTranscriptService({ store, queue, config, guildId }) {
  function exportSession(session) {
    if (!session) return null;
    return renderTranscript(session, store.listSegments(session.id), {
      pending: store.pendingJobCount(session.id),
      failed: store.failedJobCount(session.id),
    });
  }

  function removeFiles(paths) {
    for (const path of paths) {
      try {
        unlinkSync(path);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
  }

  return {
    active() {
      return store.getActiveSession(guildId);
    },
    start(meta) {
      const active = store.getActiveSession(guildId);
      if (active) return { created: false, session: active };
      return {
        created: true,
        session: store.createSession({ guildId, ...meta }),
      };
    },
    stop() {
      const session = store.getActiveSession(guildId);
      if (!session) return null;
      store.stopSession(session.id);
      return store.getSession(session.id, guildId);
    },
    latest(id) {
      const session = id
        ? store.getSession(id, guildId)
        : store.getLatestSession(guildId);
      return session ? { session, markdown: exportSession(session) } : null;
    },
    delete(id) {
      const result = store.deleteSession(id, guildId);
      if (result.deleted) removeFiles(result.audioPaths);
      return result.deleted;
    },
    prune(now = Date.now()) {
      const cutoff = new Date(
        now - config.retentionDays * 86_400_000,
      ).toISOString();
      const result = store.prune(guildId, cutoff);
      removeFiles(result.audioPaths);
      return result.deletedSessions;
    },
    enqueueUtterance(meta, wav) {
      return queue.enqueue(meta, wav);
    },
    exportSession,
  };
}
