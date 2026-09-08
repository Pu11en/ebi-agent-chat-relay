import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

export function safeWorkerEnv(env = process.env) {
  return {
    ...Object.fromEntries(
      ["HOME", "PATH", "LANG", "HF_HOME", "XDG_CACHE_HOME"]
        .filter((key) => env[key])
        .map((key) => [key, env[key]]),
    ),
    OMP_NUM_THREADS: "4",
    HF_HUB_OFFLINE: "1",
  };
}
export function createTranscriber(config) {
  const workDir = mkdtempSync(join(tmpdir(), "ccdb-whisper-"));
  const workerPath = fileURLToPath(
    new URL("./faster_whisper_worker.py", import.meta.url),
  );
  let worker = null;
  let ready = null;
  let closed = false;
  const pending = new Map();
  function boot() {
    if (closed) return Promise.reject(new Error("Transcriber closed"));
    if (worker) return ready;
    const child = spawn(config.python, [workerPath, "--model", config.model], {
      stdio: ["pipe", "pipe", "pipe"],
      env: safeWorkerEnv(),
    });
    worker = child;
    ready = new Promise((resolve, reject) => {
      let stderr = "";
      const fail = (error) => {
        if (worker !== child) return;
        clearTimeout(startTimer);
        reject(error);
        for (const item of pending.values()) {
          clearTimeout(item.timer);
          item.reject(error);
        }
        pending.clear();
        worker = null;
        child.kill("SIGTERM");
      };
      const startTimer = setTimeout(
        () => fail(new Error("Local speech model startup timed out")),
        config.timeoutMs,
      );
      child.on("error", fail);
      child.stdin.on("error", fail);
      child.stderr.on("data", (data) => {
        stderr = (stderr + data).slice(-500);
      });
      child.on("exit", () =>
        fail(new Error("Local speech worker exited: " + stderr)),
      );
      createInterface({ input: child.stdout }).on("line", (line) => {
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          return;
        }
        if ("ready" in message) {
          clearTimeout(startTimer);
          if (message.ready) resolve();
          else fail(new Error("Local speech model: " + message.error));
          return;
        }
        const item = pending.get(message.id);
        if (!item) return;
        clearTimeout(item.timer);
        pending.delete(message.id);
        if (message.error) item.reject(new Error(message.error));
        else item.resolve(String(message.text || "").trim());
      });
      child.fail = fail;
    });
    return ready;
  }
  const transcribe = async (wav) => {
    await boot();
    const id = randomUUID(),
      path = join(workDir, id + ".wav");
    writeFileSync(path, wav, { mode: 0o600 });
    try {
      return await new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => worker?.fail(new Error("Local speech transcription timed out")),
          config.timeoutMs,
        );
        pending.set(id, { resolve, reject, timer });
        worker.stdin.write(JSON.stringify({ id, path }) + "\n");
      });
    } finally {
      rmSync(path, { force: true });
    }
  };
  transcribe.warmup = boot;
  transcribe.close = async () => {
    closed = true;
    worker?.fail(new Error("Local speech transcription shutting down"));
    rmSync(workDir, { recursive: true, force: true });
  };
  return transcribe;
}
