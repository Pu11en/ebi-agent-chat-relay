/**
 * Turning one spoken sentence into "this goes to that thread".
 *
 * A tag is the only way to address a thread. There used to be a second way —
 * "put this in the ebi agent chat relay thread, run make verify" — with a parser
 * that offered every place the name might end and a resolver that scored the
 * readings against the sessions that exist. Both are gone (2026-09-27, so the
 * whole thing is simpler to hold in your head): every thread is tagged the moment
 * it is created, and a tag is compared exactly, so the guessing could only add
 * ways to be misunderstood.
 *
 * Nothing here assumes punctuation. Speech recognition emits a sentence with no
 * colon, inconsistent capitalisation and an occasional trailing full stop.
 */

/** Shortest instruction worth acting on; below this it is a stray word. */
export const MIN_PROMPT_CHARS = 2;

// ---------------------------------------------------------------------------
// The tag as a wake word
// ---------------------------------------------------------------------------

/**
 * What may precede a tag and still leave it an address.
 *
 * "Okay and alpha, say that we need a repo" addresses alpha. "The delta between
 * the two runs was small" does not — and a tag drawn from the phonetic alphabet
 * lands in ordinary speech sooner or later, so the difference has to be decided
 * rather than hoped about.
 *
 * Position alone is not the signal: `delta` is the second word in that
 * sentence. What separates them is the word immediately before: an article, a
 * preposition or a naming verb makes the tag a *noun* ("the delta", "call it
 * bravo", "I think alpha"), while anything else leaves it a vocative.
 *
 * This began as an allowlist of throat-clearing, and that was wrong in practice:
 * "But alpha, even if it fails…" and "You're alpha, okay, alpha, you need to…"
 * were both plainly addressed to alpha and both silently dropped, because no
 * list of filler words survives contact with how someone actually opens a
 * sentence. Blocking the few words that make a noun is the same judgement
 * inverted, and it fails in the safe direction: the cost of a false positive is
 * one visible confirmation in the transcript channel, while the cost of a false
 * negative is the speaker repeating themselves and not knowing why.
 */
const NOUN_MAKERS = new Set([
  // Determiners — "the delta", "that bravo"
  "the",
  "a",
  "an",
  "this",
  "that",
  "these",
  "those",
  "it",
  "its",
  "my",
  "your",
  "our",
  "their",
  "his",
  "her",
  // Prepositions — "to alpha", "about bravo"
  "to",
  "of",
  "in",
  "on",
  "at",
  "for",
  "with",
  "from",
  "about",
  "into",
  "by",
  // Naming and reporting verbs — "call it bravo", "I think alpha", "he said alpha"
  "call",
  "called",
  "calling",
  "name",
  "named",
  "say",
  "said",
  "says",
  "tell",
  "told",
  "mention",
  "mentioned",
  "think",
  "thought",
  "thinks",
  "guess",
  "believe",
  "mean",
  "means",
  "meant",
]);

/** Cheap bound on the scan; the filler rule above is what actually decides. */
const MAX_LEAD_WORDS = 6;

/** Connectors that carry no instruction once the thread is already named. */
const OPENERS =
  /^(?:[,.:;!?\s-]+|please\s+|to\s+|that\s+|you\s+|can\s+you\s+|could\s+you\s+)+/i;

/**
 * Address a thread by saying its tag, then just talking.
 *
 * The grammar in `parseCommand` is a sentence template, and a person mid-flow
 * does not follow one — the first live attempts were "okay and alpha say
 * that..." and "can you say hi inside of the upwork thread", neither of which
 * is "put this in the X thread Y". Once every thread has a tag, though, no
 * template is needed: the tag names the thread and the rest of the sentence is
 * the work.
 *
 * @param {string} said One finished utterance.
 * @param {Array<string|{label: string, aliases?: Array<string>}>} tags The tags
 *        currently in use, each optionally with the words it is misheard as.
 * @returns {{kind: "relay", target: string, prompt: string,
 *            candidates: Array<{target: string, prompt: string}>}|null}
 */
export function parseByTag(said, tags) {
  // An entry is either the tag itself or `{label, aliases}` — ccdb sends the
  // words the recogniser writes instead of a tag (see targets.mjs), and those
  // have to wake the thread exactly as the tag does, while the *canonical* tag
  // is what comes back, so everything downstream sees one name per thread.
  const known = new Map();
  for (const entry of tags ?? []) {
    if (!entry) continue;
    const label = String(entry.label ?? entry).toLowerCase();
    if (!label) continue;
    known.set(label, label);
    for (const alias of entry.aliases ?? [])
      if (alias) known.set(String(alias).toLowerCase(), label);
  }
  if (!known.size) return null;
  const text = String(said ?? "").trim();
  if (!text) return null;

  const words = text.split(/\s+/);
  const limit = Math.min(words.length, MAX_LEAD_WORDS);
  for (let i = 0; i < limit; i += 1) {
    const bare = words[i].toLowerCase().replace(/[^a-z0-9]/g, "");
    if (!known.has(bare)) continue;
    const tag = known.get(bare);
    const before = (words[i - 1] ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
    if (before && NOUN_MAKERS.has(before)) return null;
    // Everything after the tag is the instruction. "thread" straight after the
    // tag is how people say it out loud ("alpha thread, run the tests") and
    // carries nothing, so it goes too.
    let rest = words.slice(i + 1).join(" ");
    rest = rest.replace(/^(?:thread|session|chat)\b/i, "");
    rest = rest.replace(OPENERS, "").trim();
    const candidate = { target: tag, prompt: rest };
    return { kind: "relay", ...candidate, candidates: [candidate] };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Opening a session, rather than talking to one
// ---------------------------------------------------------------------------

/**
 * "Make a new session in the aldus folder and do the design work."
 *
 * No tag is needed for this one, and that is not a shortcut. A tag answers
 * "which thread?", and there is no thread yet — while the phrase "new session"
 * is not something anyone says in conversation, so it can carry the whole
 * signal by itself.
 *
 * Where the folder name ends is the interesting part, and as with the thread
 * grammar it cannot be known here: "in the aldus folder and we'll do design
 * work" ends at `folder`, "in aldus and do design work" ends at `and`, and "in
 * aldus" does not end at all. So every reading is offered and the folder
 * catalog picks the one that names something real.
 */
const NEW_SESSION = new RegExp(
  // Throat-clearing, then however the request was framed. The framing varies far
  // more than the request does — "make a new session", "I'll make a new session",
  // "can you open a session", "let's start a thread" — and a transcript adds its
  // own noise on top ("I make a new session", with the "'ll" lost).
  String.raw`^(?:(?:ok(?:ay)?|alright|right|hey|so|and|well|um+|uh+|oh)[\s,.:;-]*)*` +
    String.raw`(?:(?:i|we|you)(?:'?ll|'?d)?\s+)?(?:can|could|would|will)?\s*(?:you\s+)?` +
    String.raw`(?:please\s+)?(?:(?:want|need|like|going)\s+(?:to|ta)\s+)?(?:let'?s\s+)?` +
    String.raw`(?:make|start|open|create|spin\s+up|fire\s+up|kick\s+off|boot\s+up)\s+` +
    String.raw`(?:me\s+)?(?:a|an|another)?\s*(?:new\s+)?(?:session|thread|chat)\s+` +
    String.raw`(?:inside\s+of|inside|in|for|on|at|under)\s+(?:the\s+)?(.+)$`,
  "i",
);

/** Words that end a spoken folder name. */
const FOLDER_END = /\b(folder|directory|project|repo|repository)\b/i;
/** Words that start the instruction once the folder has been named. */
const PROMPT_START = /\b(?:and|then|to|so|where|,)\b/i;

function folderSplits(rest) {
  const splits = [];
  const seen = new Set();
  const add = (folder, prompt) => {
    const f = folder.trim().replace(/[,.;:]+$/, "");
    if (!f || seen.has(f)) return;
    seen.add(f);
    const cleaned = prompt
      .trim()
      .replace(/^[,.;:\s-]+/, "")
      .replace(/^(?:and|then|to|so)\s+/i, "")
      .replace(/^[,.;:\s-]+/, "");
    splits.push({ folder: f, prompt: cleaned });
  };
  const named = rest.match(FOLDER_END);
  if (named)
    add(rest.slice(0, named.index), rest.slice(named.index + named[0].length));
  const joined = rest.match(PROMPT_START);
  if (joined) add(rest.slice(0, joined.index), rest.slice(joined.index));
  add(rest, "");
  return splits;
}

/**
 * @param {string} said One finished utterance.
 * @returns {{kind: "spawn", folder: string, prompt: string,
 *            candidates: Array<{folder: string, prompt: string}>}|null}
 */
export function parseNewSession(said) {
  const text = String(said ?? "")
    .trim()
    .replace(/\s+/g, " ");
  const match = text.match(NEW_SESSION);
  if (!match) return null;
  const candidates = folderSplits(match[1]);
  if (!candidates.length) return null;
  return { kind: "spawn", ...candidates[0], candidates };
}

// ---------------------------------------------------------------------------
// Clearing the sidebar
// ---------------------------------------------------------------------------

/**
 * "Close everything I'm not using."
 *
 * Every spoken instruction opens a thread and nothing ever closed one, so the
 * sidebar filled with finished conversations and clearing it was manual work —
 * the exact kind of chore voice control exists to remove.
 *
 * The pattern is deliberately anchored to the *start* of the sentence and
 * requires both halves ("close … everything … not using"). This is the one
 * spoken command that acts on threads it was not addressed from, so a false
 * positive is expensive: talking *about* the idea ("I should close everything
 * I'm not using at some point") must not carry it out. A leading word that
 * turns the phrase into a report or a wish is what separates them, which is why
 * only throat-clearing may precede it.
 *
 * Nothing that is running is ever touched — that is enforced by the caller, not
 * by this pattern, because a sentence cannot know what is busy.
 */
const TIDY_UP =
  /^(?:ok(?:ay)?|alright|right|hey|um+|uh+|so|and|well|yeah|please|can you|could you|go ahead and)?[\s,.:;-]*close\s+(?:out\s+)?(?:everything|every(?:\s+single)?\s+(?:one|session|thread|chat)|all\s+(?:the\s+)?(?:sessions|threads|chats)|the\s+(?:sessions|threads|chats))\s+(?:that\s+)?(?:i'?m|i\s+am|we'?re|we\s+are)\s+not\s+(?:using|working\s+on|in)\b/i;

/**
 * @param {string} said One finished utterance.
 * @returns {{kind: "tidy-up"}|null}
 */
export function parseTidyUp(said) {
  const text = String(said ?? "")
    .trim()
    .replace(/\s+/g, " ");
  return TIDY_UP.test(text) ? { kind: "tidy-up" } : null;
}
