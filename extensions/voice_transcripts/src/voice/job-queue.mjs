import {
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

export function createJobQueue({
  store,
  queueDir,
  transcribe,
  provider,
  model,
  maxAttempts,
  logger = console,
}) {
  mkdirSync(queueDir, { recursive: true });
  let draining = null;
  let retryTimer = null;

  function remove(path) {
    try {
      unlinkSync(path);
    } catch (error) {
      if (error.code !== "ENOENT") logger.warn("[queue] unlink failed", error);
    }
  }

  function scheduleRetry(delayMs = 30_000) {
    if (retryTimer) return;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void drain();
    }, delayMs);
    retryTimer.unref?.();
  }

  async function runDrain() {
    while (true) {
      const [job] = store.listReadyJobs(new Date().toISOString(), 1);
      if (!job) break;
      try {
        const wav = readFileSync(job.audio_path);
        const text = await transcribe(wav, `${job.id}.wav`);
        store.completeJob(job.id, { text, provider, model });
        remove(job.audio_path);
        logger.info(`[queue] transcribed ${job.id}`);
      } catch (error) {
        const delay = Math.min(5 * 60_000, 5_000 * 2 ** job.attempts);
        const failed = store.failJob(job.id, error.message || error, {
          maxAttempts,
          nextAttemptAt: new Date(Date.now() + delay).toISOString(),
        });
        logger.error(
          `[queue] transcription failed ${job.id} attempt=${failed?.attempts}: ${error.message}`,
        );
        if (failed?.status === "pending") scheduleRetry(delay);
      }
    }
  }

  async function drain() {
    if (!draining)
      draining = runDrain().finally(() => {
        draining = null;
      });
    return draining;
  }

  return {
    enqueue(meta, wav) {
      const id = randomUUID();
      const finalPath = join(queueDir, `${id}.wav`);
      const tempPath = `${finalPath}.${process.pid}.tmp`;
      writeFileSync(tempPath, wav, { mode: 0o600 });
      renameSync(tempPath, finalPath);
      const createdAt = new Date().toISOString();
      store.enqueueJob({ id, audioPath: finalPath, createdAt, ...meta });
      void drain();
      return id;
    },
    drain,
    remove,
    async close() {
      if (retryTimer) clearTimeout(retryTimer);
      if (draining) await draining;
      await transcribe.close?.();
    },
  };
}
