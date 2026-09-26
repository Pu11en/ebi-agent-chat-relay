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
const PROJECT_LIMIT = 200;

/**
 * Parse a control-plane response without destroying its Discord IDs.
 *
 * A snowflake is 19 digits — larger than `Number.MAX_SAFE_INTEGER` — so
 * `JSON.parse` rounds it: 1553390219548561508 silently becomes ...561400, and
 * the only symptom is Discord answering "Unknown Channel" for a thread that is
 * plainly right there. The ids are quoted before parsing so they stay strings,
 * which is all this client ever needs them to be: something to put in a URL.
 *
 * Only id fields are quoted; a duration or a count is left as a number.
 */
function parseIdSafe(text) {
  return JSON.parse(text.replace(/"(thread_id|session_id|speaker_id)":\s*(\d+)/g, '"$1":"$2"'));
}

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
      return parseIdSafe(await response.text())?.sessions ?? [];
    },
    async listProjects() {
      const response = await request(`/api/projects?limit=${PROJECT_LIMIT}`, {
        method: "GET",
        headers: headers(),
      });
      return parseIdSafe(await response.text())?.projects ?? [];
    },
    async spawn({ workingDir, prompt, threadName, userId }) {
      const response = await request("/api/spawn", {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          working_dir: workingDir,
          prompt,
          thread_name: threadName,
          user_id: Number(userId),
          auto_start: true,
        }),
      });
      return parseIdSafe(await response.text());
    },
    async getRuntime(threadId) {
      const response = await request(`/api/threads/${encodeURIComponent(threadId)}/runtime`, {
        method: "GET",
        headers: headers(),
      });
      return parseIdSafe(await response.text());
    },
    async setRuntime(threadId, { model, backend }) {
      const response = await request(`/api/threads/${encodeURIComponent(threadId)}/runtime`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          ...(model ? { model } : {}),
          ...(backend ? { backend } : {}),
        }),
      });
      return parseIdSafe(await response.text());
    },
    async sendSpoken({ threadId, text, speakerId, source = "voice", mode = "queue" }) {
      const response = await request(`/api/threads/${encodeURIComponent(threadId)}/spoken`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ text, speaker_id: String(speakerId), source, mode }),
      });
      return parseIdSafe(await response.text());
    },
  };
}
