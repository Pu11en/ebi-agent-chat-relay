// Keep continuous speech bounded without destroying the speaker subscription.
export function createPcmBuffer({ maxBytes, minBytes, onChunk }) {
  let chunks = [];
  let size = 0;
  function flush() {
    const data = Buffer.concat(chunks, size);
    chunks = [];
    size = 0;
    if (data.length >= minBytes) onChunk(data);
  }
  return {
    write(data) {
      let offset = 0;
      while (offset < data.length) {
        const count = Math.min(maxBytes - size, data.length - offset);
        chunks.push(data.subarray(offset, offset + count));
        size += count;
        offset += count;
        if (size === maxBytes) flush();
      }
    },
    flush,
  };
}
