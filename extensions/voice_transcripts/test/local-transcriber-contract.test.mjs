import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const worker = resolve("src/voice/faster_whisper_worker.py");

describe("local transcription worker contract", () => {
  it("publishes the versioned JSON-lines request and response shape", () => {
    const result = spawnSync("python3", [worker, "--contract"], {
      encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr);
    const contract = JSON.parse(result.stdout.trim());
    assert.deepEqual(contract, {
      contract: 1,
      request: { id: "string", path: "string" },
      response: { id: "string", text: "string" },
    });
  });
});
