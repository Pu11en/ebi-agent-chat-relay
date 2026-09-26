/**
 * One spoken sentence, all the way to a running session.
 *
 * The order of the guards is the design. Only the owner is obeyed — everyone
 * in a recorded room is transcribed, and the room is not an authorisation
 * boundary. Only a sentence that parses as a command goes any further, and
 * everything else leaves no trace at all: a controller that answered "I didn't
 * catch that" to ordinary conversation would make the room unusable.
 *
 * The one exception is the follow-up window, and it exists because of how
 * people actually talk. Speech is captured per pause, so "put this in the
 * bravo thread —" *pause* "— run the tests" arrives as two utterances, and the
 * first one names a thread with nothing to do. Rather than discard it, the
 * thread is held briefly and the next thing said becomes its instruction. The
 * window is short and the hold is announced, so a held thread is never a
 * surprise.
 *
 * Every outcome the speaker cannot see for themselves is said back in the
 * transcript channel — what was sent and where, which name matched nothing,
 * which two threads both answered to it. The one thing never reported is
 * silence, because silence is the normal case.
 */

import {
  MIN_PROMPT_CHARS,
  parseByTag,
  parseCommand,
  parseNewSession,
  parseTidyUp,
} from "./command.mjs";
import { matchFolder } from "./folders.mjs";
import { parseRuntime } from "./runtime.mjs";
import { resolveTarget, untagged } from "./targets.mjs";

const IGNORED = { status: "ignored" };

/** How long a named-but-unfinished command waits for its instruction. */
export const FOLLOW_UP_MS = 30_000;

/**
 * How long a thread keeps receiving what is said after a delivered instruction.
 *
 * Requiring the tag in every utterance looked reasonable and is unusable, for a
 * reason that only shows up with a real microphone: speech is cut at every
 * pause, so thinking out loud produces five utterances and the tag is in one of
 * them. Measured — "Alpha. Okay, we need to plan out a massive build…" was
 * delivered, and the sentence 30 seconds later that finished the thought was
 * dropped, because it did not repeat the name. Nobody says a person's name in
 * every sentence, and being made to is what makes this feel broken.
 *
 * So the tag opens a conversation rather than addressing one line. Each further
 * utterance resets the clock, a new tag switches thread, and "stop listening"
 * ends it — the same shape the wake-word implementations converge on.
 */
export const ATTACH_MS = 90_000;

/** Ends an open conversation without naming another thread. */
const RELEASE =
  /^(?:(?:ok(?:ay)?|alright|and|so|um+|uh+)[\s,.]*)*(?:stop\s+listening|stop\s+it|that'?s\s+(?:all|it)|never\s*mind|nevermind|stand\s+down|we'?re\s+done|i'?m\s+done)\b/i;

/**
 * Utterances that carry nothing and should not be forwarded into an open
 * conversation. The recogniser emits "Thank you." and "Okay." for near-silence,
 * and forwarding those would spend a turn on a room tone artefact.
 */
const EMPTY_TALK =
  /^(?:thank\s*you|thanks|ok(?:ay)?|yeah|yep|yes|no|nope|hmm+|mm+|uh+|um+|huh|what|right|sure|so|well|like)[\s.,!?]*$/i;

export function createVoiceController({
  ownerId,
  enabled,
  client,
  announce,
  logger = console,
  source = "voice",
  now = () => Date.now(),
  followUpMs = FOLLOW_UP_MS,
  attachMs = ATTACH_MS,
  sessionsTtlMs = 10_000,
  // Where a session goes when the folder could not be worked out. Opening it
  // somewhere beats refusing: the thread gets a tag, says what it misheard, and
  // can be corrected by talking to it — which is cheaper than saying the whole
  // sentence again.
  fallbackDir = "/home/drewp/main-projects",
}) {
  /** @type {{session: object, target: string, at: number} | null} */
  let held = null;
  /** The thread an open conversation is going to, and when it last heard anything. */
  /** @type {{session: object, at: number} | null} */
  let attached = null;
  /** @type {{sessions: Array<object>, at: number} | null} */
  let cached = null;

  /**
   * The tags have to be known before the sentence can be understood, so this is
   * read for every utterance rather than only for a recognised command. It is a
   * loopback call, and a short cache keeps a talkative minute from making one
   * per breath.
   */
  async function sessions() {
    if (cached && now() - cached.at < sessionsTtlMs) return cached.sessions;
    const fresh = await client.listSessions();
    cached = { sessions: fresh, at: now() };
    return fresh;
  }

  async function say(message) {
    // A room that cannot be spoken to is still a room a command was sent from.
    try {
      await announce(message);
    } catch (error) {
      logger.warn?.("[control] announcement failed:", error.message);
    }
  }

  function describe(session) {
    const tag = session.voice_label ? `\`${session.voice_label}\` ` : "";
    // The title already carries "[bravo] " once ccdb has tagged it; showing the
    // tag twice in one line reads like two different things.
    const name =
      untagged(session.thread_name) || session.working_dir || `thread ${session.thread_id}`;
    return tag + name;
  }

  function taken() {
    if (held && now() - held.at <= followUpMs) return held;
    held = null;
    return null;
  }

  function openConversation() {
    if (attached && now() - attached.at <= attachMs) return attached;
    attached = null;
    return null;
  }

  async function send(session, prompt) {
    try {
      await client.sendSpoken({
        threadId: session.thread_id,
        text: prompt,
        speakerId: String(ownerId),
        source,
      });
    } catch (error) {
      await say(`🎙️ Could not deliver that to **${describe(session)}** — ${error.message}`);
      return { status: "failed", error: error.message };
    }
    await say(`🎙️ → **${describe(session)}**: ${prompt}`);
    logger.info?.(`[control] sent to thread ${session.thread_id}`);
    // Keep listening: the rest of the thought is coming, without the name.
    attached = { session, at: now() };
    return { status: "sent", session, prompt };
  }

  /**
   * Close every session that is not in use, and say what went.
   *
   * "In use" is the two things a sentence cannot know and this can: a thread
   * with a turn in flight, and the thread currently being talked to. Everything
   * else is a finished conversation sitting in the sidebar.
   *
   * One failure does not stop the sweep. A thread Discord has already lost is
   * the common case here — that is a thread which needs no closing, not a
   * reason to leave the other twenty open.
   */
  async function tidyUp(live) {
    const open = openConversation();
    const spare = (live ?? []).filter(
      (s) =>
        s.state !== "running" &&
        String(s.thread_id) !== String(open?.session?.thread_id ?? ""),
    );
    if (!spare.length) {
      await say("🎙️ Nothing to close — every session is in use.");
      return { status: "tidied", closed: 0, failed: 0 };
    }
    let closed = 0;
    let failed = 0;
    for (const session of spare) {
      try {
        await client.close(session.thread_id, { actor: ownerId });
        closed += 1;
      } catch (error) {
        failed += 1;
        logger.warn?.(`[control] could not close ${session.thread_id}: ${error.message}`);
      }
    }
    await say(
      `🎙️ Closed ${closed} session${closed === 1 ? "" : "s"} you were not using` +
        (failed ? ` (${failed} could not be reached)` : "") +
        ".",
    );
    return { status: "tidied", closed, failed };
  }

  /** Change (or report) which agent answers in a thread. */
  async function tune(session, runtime) {
    try {
      if (runtime.kind === "runtime-query") {
        const now = await client.getRuntime(session.thread_id);
        await say(
          `🎙️ **${describe(session)}** is on **${now.backend}** / **${now.model ?? "default"}**.`,
        );
        return { status: "runtime", ...now };
      }
      // Only the fields that were actually heard; `kind` is this layer's
      // bookkeeping and has no business in the request.
      const change = {
        ...(runtime.model ? { model: runtime.model } : {}),
        ...(runtime.backend ? { backend: runtime.backend } : {}),
      };
      const result = await client.setRuntime(session.thread_id, change);
      await say(
        `🎙️ **${describe(session)}** → **${result.backend}** / **${result.model ?? "default"}** ` +
          `(from its next turn).`,
      );
      logger.info?.(`[control] thread ${session.thread_id} set to ${result.backend}`);
      return { status: "runtime", ...result };
    } catch (error) {
      await say(`🎙️ Could not change the agent for **${describe(session)}** — ${error.message}`);
      return { status: "failed", error: error.message };
    }
  }

  /** Open a session in the folder that was named, or say why it could not. */
  async function open(opening) {
    let projects;
    try {
      projects = await client.listProjects();
    } catch (error) {
      await say(`🎙️ Could not read the folder list — ${error.message}`);
      return { status: "failed", error: error.message };
    }

    let chosen = null;
    let heard = opening.folder;
    for (const candidate of opening.candidates) {
      const match = matchFolder(candidate.folder, projects);
      if (match.status === "ok") {
        chosen = { project: match.project, prompt: candidate.prompt };
        heard = candidate.folder;
        break;
      }
    }

    const instruction =
      chosen?.prompt ||
      opening.candidates.find((c) => c.prompt)?.prompt ||
      "";

    // No folder matched. Open it anyway, in the projects root, and let the new
    // session say what it heard — correcting it by voice costs one sentence,
    // whereas refusing costs the whole request again.
    const workingDir = chosen?.project.path ?? fallbackDir;
    const name = chosen?.project.name ?? `voice: ${heard}`;
    const preamble = chosen
      ? ""
      : `Drew opened this session by voice and asked for the "${heard}" folder, ` +
        `which does not match any project — speech recognition mangles folder names, ` +
        `so read it for intent. You are currently in ${fallbackDir}. Say in one short ` +
        `line which folder you think he meant and work there, or ask him which one. ` +
        `He can answer by voice using this thread's tag.\n\n`;

    try {
      const result = await client.spawn({
        workingDir,
        threadName: name,
        userId: ownerId,
        prompt:
          preamble +
          (instruction ||
            "Drew opened this session by voice and has not said what to work on yet. " +
              "Ask him in one short line."),
      });
      await say(
        chosen
          ? `🎙️ Opened a session in **${chosen.project.name}**` +
              (instruction ? `: ${instruction}` : " — say what it should do next.")
          : `🎙️ No folder matched **${heard}** — opened a session in \`${fallbackDir}\` ` +
              `instead. Tell it which folder you meant using its tag.`,
      );
      logger.info?.(`[control] spawned thread ${result?.thread_id} in ${workingDir}`);
      return { status: "opened", workingDir, project: chosen?.project ?? null, result };
    } catch (error) {
      await say(`🎙️ Could not open a session — ${error.message}`);
      return { status: "failed", error: error.message };
    }
  }

  return {
    async handleUtterance({ userId, text }) {
      if (!enabled) return { status: "disabled" };
      if (String(userId) !== String(ownerId)) return IGNORED;

      let live;
      try {
        live = await sessions();
      } catch (error) {
        // Without the session list there is no tag list, so nothing can be
        // understood. Stay silent unless a thread was already being held —
        // there the speaker is expecting an answer.
        if (!taken()) return IGNORED;
        await say(`🎙️ Could not reach the bot's API — ${error.message}`);
        return { status: "failed", error: error.message };
      }

      // "Close everything I'm not using" acts on every thread rather than one,
      // so it is checked before any target is resolved — there is no target.
      if (parseTidyUp(text)) return await tidyUp(live);

      // Saying the tag is the primary form; the sentence template is the
      // fallback for a thread that has no tag yet.
      const addressed = parseByTag(
        text,
        live.map((s) => ({ label: s.voice_label, aliases: s.voice_label_aliases })),
      );
      // "New session in X" needs no tag — there is no thread to address yet,
      // and nobody says that phrase in conversation. An explicit tag still wins,
      // since naming a thread is a deliberate act.
      if (!addressed) {
        const opening = parseNewSession(text);
        if (opening) {
          held = null;
          return await open(opening);
        }
      }

      const command = addressed ?? parseCommand(text);

      if (!command) {
        const rest = String(text ?? "").trim();

        // A thread was named with no instruction: this is that instruction.
        const waiting = taken();
        if (waiting && rest.length >= MIN_PROMPT_CHARS) {
          held = null;
          return await send(waiting.session, rest);
        }

        const open = openConversation();
        if (!open) return IGNORED;
        if (RELEASE.test(rest)) {
          attached = null;
          await say(`🎙️ Stopped listening to **${describe(open.session)}**.`);
          return { status: "released" };
        }
        // Room tone and acknowledgement noise are not worth a turn, but they do
        // mean the speaker is still here, so the conversation stays open.
        if (rest.length < MIN_PROMPT_CHARS || EMPTY_TALK.test(rest)) {
          attached.at = now();
          return IGNORED;
        }
        return await send(open.session, rest);
      }

      held = null;
      const match = resolveTarget(command.candidates, live);
      const heard = match.candidate?.target ?? command.target;
      if (match.status === "none") {
        await say(
          `🎙️ Nothing matched **${heard}**. Say the thread's tag — ` +
            (live
              .filter((s) => s.voice_label)
              .slice(0, 8)
              .map((s) => `\`${s.voice_label}\` ${untagged(s.thread_name) || s.working_dir || ""}`)
              .join(" · ") || "no tags assigned yet"),
        );
        return { status: "no-target", target: heard };
      }
      if (match.status === "ambiguous") {
        await say(
          `🎙️ **${heard}** matches more than one thread: ` +
            `${match.options.map(describe).join(" · ")}. Say its tag instead.`,
        );
        return { status: "ambiguous", options: match.options };
      }

      const prompt = match.candidate.prompt;

      // "Bravo, switch to opus" changes who answers rather than asking them
      // anything. Handled here, without waking the session: a thread whose
      // plan has run out cannot be asked to change its own model.
      const runtime = parseRuntime(prompt);
      if (runtime) return await tune(match.session, runtime);

      if (prompt.length < MIN_PROMPT_CHARS) {
        // The thread was named and the sentence stopped. Hold it for whatever
        // is said next instead of throwing the naming away.
        held = { session: match.session, target: heard, at: now() };
        await say(
          `🎙️ Holding **${describe(match.session)}** — just say what it should do ` +
            `(within ${Math.round(followUpMs / 1000)}s).`,
        );
        return { status: "incomplete", session: match.session, target: heard };
      }

      return await send(match.session, prompt);
    },
  };
}
