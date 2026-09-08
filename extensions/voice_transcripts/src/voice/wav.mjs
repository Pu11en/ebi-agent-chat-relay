const CHANNELS = 2;
const SAMPLE_RATE = 48_000;
const BITS_PER_SAMPLE = 16;
const BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * (BITS_PER_SAMPLE / 8);

export function pcmDurationMs(byteLength) {
  return Math.round((byteLength / BYTES_PER_SECOND) * 1000);
}

export function pcmToWav(pcm) {
  const audio = Buffer.isBuffer(pcm) ? pcm : Buffer.from(pcm);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + audio.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(CHANNELS, 22);
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(BYTES_PER_SECOND, 28);
  header.writeUInt16LE(CHANNELS * (BITS_PER_SAMPLE / 8), 32);
  header.writeUInt16LE(BITS_PER_SAMPLE, 34);
  header.write("data", 36);
  header.writeUInt32LE(audio.length, 40);
  return Buffer.concat([header, audio]);
}

export { BYTES_PER_SECOND };
