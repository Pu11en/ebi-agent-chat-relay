/**
 * Resolving a spoken tag against the sessions that actually exist.
 *
 * This used to also match a spoken *name* against a thread title — folder names
 * compared by sound, a score per candidate, a tie margin, and an "that was
 * ambiguous, say its tag instead" reply. All of it existed to serve one spoken
 * form, "put this in the ebi agent chat relay thread ...", which predates tags.
 *
 * It is gone (2026-09-27, at Drew's request to make the whole thing simpler).
 * Every thread is given a tag the moment it is created, and a tag is compared
 * exactly — so the scoring could only ever add ways to be misunderstood, never
 * ways to be understood. About 130 lines and a whole class of failure went with
 * it: "where does the name end and the instruction begin" is not a question
 * anybody has to answer now.
 *
 * What is left is an exact lookup, plus the one concession to reality: a tag also
 * answers to the words the recogniser writes *instead* of it.
 */

/** The "[luffy] " ccdb writes at the front of a tagged thread title. */
const TITLE_TAG = /^\s*\[[a-z]{3,10}\]\s*/;

/** The title without its tag prefix — the tag is shown separately. */
export function untagged(title) {
  return String(title ?? "").replace(TITLE_TAG, "").trim();
}

/** Letters and digits only — the form tags are compared in. */
function normalize(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .trim();
}

/**
 * The session whose tag is `spoken`, or null.
 *
 * Exact, deliberately. A character name is outside the recogniser's vocabulary,
 * so it substitutes an English one — "Luffy" comes back as "Lucy" — and the fix
 * is a list of those substitutions shipped with the session, never a fuzzy
 * comparison: the consonant skeleton of a four-letter name is two characters
 * long, so fuzzy matching would route `nami` and `kaido` to each other.
 */
export function matchTarget(spoken, sessions) {
  const want = normalize(spoken);
  if (!want) return { status: "none" };
  const hit = (sessions ?? []).find(
    (s) =>
      normalize(s.voice_label) === want ||
      (s.voice_label_aliases ?? []).some((alias) => normalize(alias) === want),
  );
  return hit
    ? { status: "ok", session: hit, score: 1, label: hit.voice_label }
    : { status: "none" };
}

/**
 * The session a parsed command names.
 *
 * `parseByTag` offers exactly one candidate, so this is a thin wrapper kept for
 * the shape the controller expects. It can no longer answer "ambiguous": two
 * threads cannot hold the same tag.
 *
 * @param {Array<{target: string, prompt: string}>} candidates
 * @param {Array<object>} sessions
 */
export function resolveTarget(candidates, sessions) {
  for (const candidate of candidates ?? []) {
    const match = matchTarget(candidate.target, sessions);
    if (match.status === "ok") return { ...match, candidate };
  }
  return { status: "none", candidate: (candidates ?? []).at(-1) ?? null };
}
