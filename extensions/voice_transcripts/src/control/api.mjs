/**
 * The ccdb control plane, as seen from the voice companion.
 *
 * Two calls: read which sessions exist, and hand one of them something its
 * owner said. The secret travels in a header and never in a URL, because a URL
 * is what ends up in an error message, a proxy log and a stack trace.
 *
 * Every call is bounded by a timeout. This runs inside a five-second health
 * loop with a live voice connection attached; a request that hangs would stall
 * the room, so failing fast and saying so is the better outcome.
 */

const DEFAULT_TIMEOUT_MS = 10_000;
const SESSION_LIMIT = 50;

export function createRelayClient({
  baseUrl,
  secret = null,
  fetchImpl = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
}) {
  const root = String(baseUrl ?? "").replace(/\/+$/, "");
  if (!root) throw new Error("A control-plane base URL is required");

  function headers(extra = {}) {
    const result = { ...extra };
    if (secret) result.Authorization = `Bearer ${secret}`;
    return result;
  }

  async function request(path, init = {}) {
    const response = await fetchImpl(root + path, {
      ...init,
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      let detail = "";
      try {
        detail = (await response.json())?.error ?? "";
      } catch {
        detail = "";
      }
      throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ""}`);
    }
    return response;
  }

  return {
    async listSessions() {
      const response = await request(`/api/sessions?limit=${SESSION_LIMIT}`, {
        method: "GET",
        headers: headers(),
      });
      return (await response.json())?.sessions ?? [];
    },
    async sendSpoken({ threadId, text, speakerId, source = "voice", mode = "queue" }) {
      const response = await request(`/api/threads/${encodeURIComponent(threadId)}/spoken`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ text, speaker_id: String(speakerId), source, mode }),
      });
      return await response.json();
    },
  };
}
