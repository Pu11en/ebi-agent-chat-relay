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

import { MIN_PROMPT_CHARS, parseByTag, parseCommand } from "./command.mjs";
import { resolveTarget, untagged } from "./targets.mjs";

const IGNORED = { status: "ignored" };

/** How long a named-but-unfinished command waits for its instruction. */
export const FOLLOW_UP_MS = 30_000;

export function createVoiceController({
  ownerId,
  enabled,
  client,
  announce,
  logger = console,
  source = "voice",
  now = () => Date.now(),
  followUpMs = FOLLOW_UP_MS,
  sessionsTtlMs = 10_000,
}) {
  /** @type {{session: object, target: string, at: number} | null} */
  let held = null;
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
    return { status: "sent", session, prompt };
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

      // Saying the tag is the primary form; the sentence template is the
      // fallback for a thread that has no tag yet.
      const command =
        parseByTag(
          text,
          live.map((s) => s.voice_label),
        ) ?? parseCommand(text);

      if (!command) {
        // Not addressed to a thread — but if one is being held, this is the
        // rest of the sentence rather than conversation.
        const waiting = taken();
        const rest = String(text ?? "").trim();
        if (!waiting || rest.length < MIN_PROMPT_CHARS) return IGNORED;
        held = null;
        return await send(waiting.session, rest);
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
