/**
 * Finding the folder someone said, when the recogniser wrote something else.
 *
 * "Aldus" came through as "oldest". No amount of clearer speech fixes that —
 * the word is not in the model's vocabulary and it will keep substituting a
 * word that is. Exact and even token-overlap matching are both useless here,
 * because the two strings share no whole word.
 *
 * What they do share is their consonants. Dropping the vowels and folding
 * consonants that a microphone confuses ("d" with "t", "s" with "z") turns
 * "oldest" into `ldsd` and "aldus" into `lds` — one edit apart. That is the
 * comparison this file makes: a skeleton, then an edit distance over it.
 *
 * Deliberately not soundex: soundex keeps the first letter, and the first
 * letter is exactly what went wrong ("a" heard as "o").
 */

const VOWELS = /[aeiouyh]/g;

/**
 * Words that carry no sound worth comparing.
 *
 * "the" is the dangerous one: its "t" folds to "d", so "the aldus" skeletonises
 * to `dlds` while "aldus" gives `lds`, and an article decides a match it has no
 * business deciding. "folder" and "project" go for the same reason — they are
 * how the folder was referred to, not part of its name.
 */
const NOISE = new Set([
  "the",
  "a",
  "an",
  "my",
  "our",
  "that",
  "this",
  "folder",
  "directory",
  "project",
  "repo",
  "repository",
]);

function meaningful(text) {
  return String(text ?? "")
    .toLowerCase()
    .split(/[^a-z]+/)
    .filter((w) => w && !NOISE.has(w))
    .join("");
}

/** Consonants a room microphone routinely swaps for one another. */
const FOLD = new Map(
  Object.entries({
    t: "d",
    z: "s",
    c: "k",
    q: "k",
    x: "ks",
    v: "f",
    p: "b",
    m: "n",
    g: "k",
    j: "d",
  }),
);

/** The consonant skeleton two mishearings of the same word have in common. */
export function skeleton(text) {
  const letters = meaningful(text).replace(VOWELS, "");
  let out = "";
  for (const ch of letters) {
    const folded = FOLD.get(ch) ?? ch;
    if (!out.endsWith(folded)) out += folded;
  }
  return out;
}

function distance(a, b) {
  if (a === b) return 0;
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    let diagonal = prev[0];
    prev[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const current = prev[j];
      prev[j] = Math.min(
        prev[j] + 1,
        prev[j - 1] + 1,
        diagonal + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
      diagonal = current;
    }
  }
  return prev[b.length];
}

/**
 * Edits a mishearing is allowed to be away from the real word.
 *
 * Scoring the distance as a *fraction* of length looks reasonable and is wrong:
 * it lets long names match on nothing at all — "quantum tunnelling" scored 0.7
 * against "gigamedia" because eight edits out of eleven letters still reads as
 * 27% similar. A mishearing substitutes a word that sounds close, so the
 * allowance is a small absolute number of edits regardless of length.
 */
const MAX_EDITS = 2;

/** 0..1 — how likely these are the same word heard twice. */
export function soundsLike(spoken, candidate) {
  const a = skeleton(spoken);
  const b = skeleton(candidate);
  if (!a || !b) return 0;
  if (a === b) return 1;
  // A candidate whose skeleton *begins with* the spoken one is a near-certain
  // hit — "aldus email" heard as "aldus" should find aldus-email — but only
  // when the shared part is most of the word. Without that floor, a three-
  // letter skeleton prefixes half the catalog: "gigamedia" (`knd`) opens
  // "quantum tunnelling" (`kndndnlnk`).
  const short = Math.min(a.length, b.length);
  const long = Math.max(a.length, b.length);
  if ((b.startsWith(a) || a.startsWith(b)) && short >= 3 && short / long >= 0.6) return 0.9;
  // Likewise the edit allowance has to shrink with the word. Two edits is
  // nothing across nine letters and everything across three, where it makes
  // `sbk` and `knk` — shipcheck and cinco — the same folder.
  const allowed = Math.min(MAX_EDITS, Math.max(1, Math.floor(short / 3)));
  const edits = distance(a, b);
  if (edits > allowed) return 0;
  return 0.9 - 0.08 * edits;
}

/** Below this, the spoken words did not name a folder anyone has. */
const MIN_SCORE = 0.7;
/** Two folders this close are a question, not an answer. */
const TIE_MARGIN = 0.08;

/**
 * @param {string} spoken The folder as it was heard.
 * @param {Array<{name: string, path: string}>} projects The catalog.
 * @returns {{status: "ok", project: object, score: number}
 *          |{status: "unsure", options: Array<object>}
 *          |{status: "none"}}
 */
export function matchFolder(spoken, projects) {
  const ranked = (projects ?? [])
    .map((project) => ({ project, score: soundsLike(spoken, project.name) }))
    .filter((entry) => entry.score >= MIN_SCORE)
    .sort((a, b) => b.score - a.score);

  if (!ranked.length) return { status: "none" };
  const [first, second] = ranked;
  if (second && first.score - second.score < TIE_MARGIN) {
    return {
      status: "unsure",
      options: ranked.filter((e) => first.score - e.score < TIE_MARGIN).map((e) => e.project),
    };
  }
  return { status: "ok", project: first.project, score: first.score };
}
