import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  pcmDurationMs,
  pcmToWav,
  BYTES_PER_SECOND,
} from "../src/voice/wav.mjs";

describe("voice wav helpers", () => {
  it("computes PCM duration at 48kHz stereo 16-bit", () => {
    const oneSecond = BYTES_PER_SECOND;
    assert.equal(pcmDurationMs(oneSecond), 1000);
    assert.equal(pcmDurationMs(0), 0);
    assert.equal(pcmDurationMs(BYTES_PER_SECOND / 2), 500);
  });

  it("writes a valid 44-byte WAV header matching the PCM", () => {
    const pcm = Buffer.alloc(BYTES_PER_SECOND); // 1 second of silence
    const wav = pcmToWav(pcm);
    assert.equal(wav.length, 44 + pcm.length);
    assert.equal(wav.toString("ascii", 0, 4), "RIFF");
    assert.equal(wav.toString("ascii", 8, 12), "WAVE");
    assert.equal(wav.toString("ascii", 36, 40), "data");
    assert.equal(wav.readUInt32LE(4), 36 + pcm.length);
    assert.equal(wav.readUInt16LE(22), 2, "channels");
    assert.equal(wav.readUInt32LE(24), 48_000, "sample rate");
    assert.equal(wav.readUInt16LE(34), 16, "bits per sample");
    assert.equal(wav.readUInt32LE(40), pcm.length);
  });
});
