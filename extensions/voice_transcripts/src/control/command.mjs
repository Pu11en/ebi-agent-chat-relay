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
    lead: /^(?:put|send|drop|push|post|add)\s+(?:this|that|it)?\s*(?:in\s*to|into|in|to|on)\s+(?:the\s+)?/i,
    connector: null,
  },
  // tell / ask the X thread (to|that) <prompt>
  { lead: /^(?:tell|ask)\s+(?:the\s+)?/i, connector: /^(?:to|that)\s+/i },
  // (over) in the X thread, <prompt>
  { lead: /^(?:over\s+)?in\s+(?:the\s+)?/i, connector: null },
];

/** Shortest instruction worth sending; below this it is a stray word. */
const MIN_PROMPT_CHARS = 2;
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
    const usable = splits.filter((s) => s.prompt.length >= MIN_PROMPT_CHARS);
    if (!usable.length) {
      // A target was named and nothing followed it. The longest reading is the
      // whole name: "put this in the aldus site thread" means aldus site.
      const named = splits[splits.length - 1];
      return { kind: "incomplete", target: named.target };
    }
    return { kind: "relay", ...usable[0], candidates: usable };
  }
  return null;
}
