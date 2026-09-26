/**
 * Turning one spoken sentence into "this goes to that thread".
 *
 * The grammar is deliberately narrow. Everything said in a recorded room
 * reaches this function, so a loose matcher would fire on conversation — and
 * the cost of a false positive is a live agent session acting on a fragment of
 * small talk. A command therefore has to name a target *and* call it a thread:
 * "put this in the aldus thread ..." is an instruction, "the thread is fine"
 * is not.
 *
 * Nothing here assumes punctuation. Speech recognition emits a sentence with
 * no colon, inconsistent capitalisation and an occasional trailing full stop,
 * so the separator between the target and the instruction is the noun, not the
 * typography.
 *
 * Which is why this returns *candidate* splits rather than one answer. The noun
 * that ends the target can also occur inside the target, and a parser cannot
 * tell which: "the ebi agent chat relay thread" splits at `chat` and at
 * `thread`, and only the list of sessions that actually exist knows that the
 * second is right. Equally, "the aldus thread check the thread pool" splits at
 * either `thread`, and there the first is right. So every split is offered and
 * the resolver picks — see targets.mjs.
 */

const FILLER = /^(?:ok(?:ay)?|alright|right|hey|um+|uh+|so|and|well|yeah)\b[\s,.:;-]*/i;
const NOUN = /\b(thread|session|chat)\b/gi;
const GAP = /^[\s,.:;–—-]+/;

const FORMS = [
  // put / send / drop this in the X thread <prompt>
  {
    lead: /^(?:put|send|drop|push|post|add)\s+(?:this|that|it)?\s*(?:inside\s+of|inside|in\s*to|into|in|to|on)\s+(?:the\s+)?/i,
    connector: null,
  },
  // tell / ask the X thread (to|that) <prompt>
  { lead: /^(?:tell|ask)\s+(?:the\s+)?/i, connector: /^(?:to|that)\s+/i },
  // (over) in / inside (of) the X thread, <prompt>
  { lead: /^(?:over\s+)?(?:inside\s+of|inside|in)\s+(?:the\s+)?/i, connector: null },
];

/** A spoken thread name is a few words, not a clause. */
const MAX_TARGET_WORDS = 6;
const MAX_TARGET_CHARS = 60;

function splitsFor(rest, connector) {
  const candidates = [];
  for (const noun of rest.matchAll(NOUN)) {
    const target = rest.slice(0, noun.index).trim();
    if (!target || target.length > MAX_TARGET_CHARS) continue;
    if (target.split(/\s+/).length > MAX_TARGET_WORDS) continue;
    let prompt = rest.slice(noun.index + noun[0].length).replace(GAP, "");
    if (connector) prompt = prompt.replace(connector, "");
    candidates.push({ target, prompt: prompt.trim() });
  }
  return candidates;
}

/**
 * @param {string} said One finished utterance.
 * @returns {{kind: "relay", target: string, prompt: string,
 *            candidates: Array<{target: string, prompt: string}>}
 *          |{kind: "incomplete", target: string}
 *          |null} `null` when this was not addressed to a thread.
 *
 * `target`/`prompt` are the first candidate; `candidates` holds every split,
 * shortest target first, for a resolver that can tell them apart.
 */
export function parseCommand(said) {
  if (typeof said !== "string") return null;
  let text = said.trim().replace(/\s+/g, " ");
  if (!text) return null;
  // Strip however many filler openers the speaker stacked up.
  for (let i = 0; i < 3 && FILLER.test(text); i += 1) text = text.replace(FILLER, "");

  for (const form of FORMS) {
    const lead = text.match(form.lead);
    if (!lead) continue;
    const splits = splitsFor(text.slice(lead[0].length), form.connector);
    if (!splits.length) continue;
    // Splits with an empty instruction are offered too. Discarding them here
    // is what broke the first real command: "the ebi agent chat relay thread"
    // (said with no instruction after it) had its only non-empty reading be
    // target "ebi agent" + instruction "relay thread", so the tail of the name
    // was delivered as the work. Whether an instruction is missing can only be
    // judged once the right split is known, and only the session list knows
    // that.
    return { kind: "relay", ...splits[0], candidates: splits };
  }
  return null;
}

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
 * sentence. What separates them is that an address is preceded only by throat-
 * clearing, while a tag used as a noun is preceded by the grammar that makes it
 * one — an article, a verb, a pronoun. So everything before the tag must be
 * filler, and "the" is decisive.
 */
const LEAD_FILLER = new Set([
  "ok",
  "okay",
  "alright",
  "right",
  "hey",
  "hi",
  "um",
  "uh",
  "er",
  "so",
  "and",
  "well",
  "yeah",
  "yep",
  "now",
  "also",
  "then",
  "oh",
]);

/** Cheap bound on the scan; the filler rule above is what actually decides. */
const MAX_LEAD_WORDS = 6;

/** Connectors that carry no instruction once the thread is already named. */
const OPENERS = /^(?:[,.:;!?\s-]+|please\s+|to\s+|that\s+|you\s+|can\s+you\s+|could\s+you\s+)+/i;

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
 * @param {Array<string>} tags The tags currently in use.
 * @returns {{kind: "relay", target: string, prompt: string,
 *            candidates: Array<{target: string, prompt: string}>}|null}
 */
export function parseByTag(said, tags) {
  const known = new Set((tags ?? []).filter(Boolean).map((t) => String(t).toLowerCase()));
  if (!known.size) return null;
  const text = String(said ?? "").trim();
  if (!text) return null;

  const words = text.split(/\s+/);
  const limit = Math.min(words.length, MAX_LEAD_WORDS);
  for (let i = 0; i < limit; i += 1) {
    const bare = words[i].toLowerCase().replace(/[^a-z0-9]/g, "");
    if (!known.has(bare)) continue;
    const lead = words
      .slice(0, i)
      .map((w) => w.toLowerCase().replace(/[^a-z0-9]/g, ""))
      .filter(Boolean);
    if (!lead.every((w) => LEAD_FILLER.has(w))) return null;
    // Everything after the tag is the instruction. "thread" straight after the
    // tag is how people say it out loud ("alpha thread, run the tests") and
    // carries nothing, so it goes too.
    let rest = words.slice(i + 1).join(" ");
    rest = rest.replace(/^(?:thread|session|chat)\b/i, "");
    rest = rest.replace(OPENERS, "").trim();
    const candidate = { target: bare, prompt: rest };
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
  if (named) add(rest.slice(0, named.index), rest.slice(named.index + named[0].length));
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
  const text = String(said ?? "").trim().replace(/\s+/g, " ");
  const match = text.match(NEW_SESSION);
  if (!match) return null;
  const candidates = folderSplits(match[1]);
  if (!candidates.length) return null;
  return { kind: "spawn", ...candidates[0], candidates };
}
