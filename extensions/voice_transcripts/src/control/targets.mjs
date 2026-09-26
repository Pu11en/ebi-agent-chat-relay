/**
 * Resolving a spoken thread name against the sessions that actually exist.
 *
 * A person says "the upwork thread". Discord knows it as "📂 upwork" or
 * "Upwork profile rewrite", and the session also has a working directory.
 * Matching is therefore over several candidate strings per session, on
 * letters and digits only — the emoji Discord prefixes, the hyphens in a
 * folder name and the spaces speech recognition inserts all have to stop
 * mattering before anything is compared.
 *
 * Two answers matter as much as the match itself. A name nothing answers to
 * must come back as "none" rather than the least-bad session, and two
 * genuinely different threads that answer equally well must come back as
 * "ambiguous". Silently picking one of those is how a prompt lands in the
 * wrong repository.
 */

const STOPWORDS = new Set(["the", "a", "an", "my", "our", "that", "this"]);

/** Letters, digits and single spaces — the form everything is compared in. */
function normalize(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function tokens(value) {
  return normalize(value)
    .split(" ")
    .filter((t) => t && !STOPWORDS.has(t));
}

function basename(path) {
  return String(path ?? "")
    .replace(/\/+$/, "")
    .split("/")
    .pop();
}

function candidates(session) {
  return [session.thread_name, basename(session.working_dir)].filter(Boolean);
}

function score(spokenTokens, candidate) {
  const candidateTokens = tokens(candidate);
  if (!spokenTokens.length || !candidateTokens.length) return 0;
  const spoken = spokenTokens.join(" ");
  const target = candidateTokens.join(" ");
  if (spoken === target) return 1;
  if (target.includes(spoken) || spoken.includes(target)) return 0.8;
  const overlap = spokenTokens.filter((t) => candidateTokens.includes(t)).length;
  return 0.7 * (overlap / spokenTokens.length);
}

/** Below this, the spoken name did not really name anything. */
const MIN_SCORE = 0.45;
/** Two scores this close are a tie, not a winner. */
const TIE_MARGIN = 0.05;

/**
 * @param {string} spoken The target as it was said.
 * @param {Array<object>} sessions `/api/sessions` entries.
 * @returns {{status: "ok", session: object, score: number}
 *          |{status: "ambiguous", options: Array<object>}
 *          |{status: "none"}}
 */
export function matchTarget(spoken, sessions) {
  const spokenTokens = tokens(spoken);
  const ranked = (sessions ?? [])
    .map((session) => {
      let best = 0;
      let label = session.thread_name || basename(session.working_dir) || "";
      for (const candidate of candidates(session)) {
        const value = score(spokenTokens, candidate);
        if (value > best) {
          best = value;
          label = candidate;
        }
      }
      return { session, score: best, label };
    })
    .filter((entry) => entry.score >= MIN_SCORE)
    // A tie goes to whichever thread was spoken to most recently: that is what
    // "the aldus thread" means when the same folder is open twice.
    .sort(
      (a, b) =>
        b.score - a.score ||
        String(b.session.last_used_at ?? "").localeCompare(String(a.session.last_used_at ?? "")),
    );

  if (!ranked.length) return { status: "none" };
  const [first, second] = ranked;
  if (
    second &&
    first.score - second.score < TIE_MARGIN &&
    normalize(first.label) !== normalize(second.label)
  ) {
    return {
      status: "ambiguous",
      options: ranked
        .filter((entry) => first.score - entry.score < TIE_MARGIN)
        .map((entry) => ({ ...entry.session, matched_on: entry.label })),
    };
  }
  return { status: "ok", session: first.session, score: first.score };
}

/**
 * Pick the split that names something real.
 *
 * `parseCommand` cannot tell "the ebi agent **chat** relay thread" from "the
 * aldus thread check the **thread** pool"; the session list can. Every split
 * is scored and the best-resolving one wins, with a longer target breaking a
 * tie — a longer name consumed more of the sentence on purpose.
 *
 * @param {Array<{target: string, prompt: string}>} candidates
 * @param {Array<object>} sessions
 */
export function resolveTarget(candidates, sessions) {
  let best = null;
  let ambiguous = null;
  for (const candidate of candidates ?? []) {
    const match = matchTarget(candidate.target, sessions);
    if (match.status === "ambiguous") {
      ambiguous ??= { ...match, candidate };
      continue;
    }
    if (match.status !== "ok") continue;
    if (!best || match.score >= best.score) best = { ...match, candidate };
  }
  if (best) return best;
  if (ambiguous) return ambiguous;
  // Report the fullest name that was heard; it is the one worth correcting.
  return { status: "none", candidate: (candidates ?? []).at(-1) ?? null };
}
