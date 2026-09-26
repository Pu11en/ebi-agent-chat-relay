/**
 * "Bravo, switch to opus." — choosing the agent by talking.
 *
 * Model names are a moving target and a spoken one arrives mangled, so this
 * does not try to recognise models in general. It recognises the *aliases*
 * ccdb already accepts — `opus`, `sonnet`, `haiku`, `fable` — plus the backend
 * names, and leaves anything else alone rather than guessing a version string
 * into a setting. An alias is the right unit anyway: it always means the newest
 * of that family, which is what someone says out loud when they mean "the good
 * one".
 *
 * The backends are the more valuable half. When one plan runs dry, moving a
 * thread from Claude to Codex or to the local model is the whole recovery, and
 * it is one sentence.
 */

/** Model words worth listening for, and how the recogniser mangles them. */
const MODEL_WORDS = new Map(
  Object.entries({
    opus: "opus",
    "oh pus": "opus",
    opis: "opus",
    sonnet: "sonnet",
    sonic: "sonnet",
    sonet: "sonnet",
    haiku: "haiku",
    "high coup": "haiku",
    hiku: "haiku",
    fable: "fable",
    fabel: "fable",
  }),
);

/** Backend words, with the spellings a transcript actually produces. */
const BACKEND_WORDS = new Map(
  Object.entries({
    claude: "claude",
    clod: "claude",
    cloud: "claude",
    claud: "claude",
    codex: "codex",
    "code x": "codex",
    kodex: "codex",
    local: "local",
    ollama: "local",
    llama: "local",
    deepseek: "dsh",
    "deep seek": "dsh",
    dsh: "dsh",
    agui: "agui",
  }),
);

const SWITCH =
  /\b(?:switch|change|swap|move|set|put|use|run)\b|\bon\s+(?:opus|sonnet|haiku|fable)\b/i;
const ASKING = /\b(?:what|which)\b.*\b(?:model|backend|agent|running\s+on)\b/i;

function found(words, text) {
  // Longest phrase first: "deep seek" must win over "seek" never matching, and
  // a two-word key must be tried before its first word is mistaken for noise.
  const keys = [...words.keys()].sort((a, b) => b.length - a.length);
  for (const key of keys) {
    if (new RegExp(`\\b${key.replace(/\s+/g, "\\s+")}\\b`, "i").test(text)) return words.get(key);
  }
  return null;
}

/**
 * @param {string} said The instruction, with the tag already stripped.
 * @returns {{kind: "runtime", model?: string, backend?: string}
 *          |{kind: "runtime-query"}
 *          |null}
 */
export function parseRuntime(said) {
  const text = String(said ?? "").trim();
  if (!text) return null;
  if (ASKING.test(text)) return { kind: "runtime-query" };
  if (!SWITCH.test(text)) return null;

  const model = found(MODEL_WORDS, text);
  const backend = found(BACKEND_WORDS, text);
  if (!model && !backend) return null;
  // "switch to codex" with no model named leaves the model alone; ccdb already
  // remembers a per-backend choice, and inventing one would discard it.
  return { kind: "runtime", ...(model ? { model } : {}), ...(backend ? { backend } : {}) };
}
