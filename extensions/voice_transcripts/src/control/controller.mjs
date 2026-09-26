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

import { MIN_PROMPT_CHARS, parseCommand } from "./command.mjs";
import { resolveTarget } from "./targets.mjs";

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
}) {
  /** @type {{session: object, target: string, at: number} | null} */
  let held = null;

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
    return tag + (session.thread_name || session.working_dir || `thread ${session.thread_id}`);
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

      const command = parseCommand(text);

      if (!command) {
        // Not a command — but if a thread is being held, this is the rest of
        // the sentence rather than conversation.
        const waiting = taken();
        const rest = String(text ?? "").trim();
        if (!waiting || rest.length < MIN_PROMPT_CHARS) return IGNORED;
        held = null;
        return await send(waiting.session, rest);
      }

      held = null;
      let sessions;
      try {
        sessions = await client.listSessions();
      } catch (error) {
        await say(`🎙️ Could not reach the bot's API to find that thread — ${error.message}`);
        return { status: "failed", error: error.message };
      }

      const match = resolveTarget(command.candidates, sessions);
      const heard = match.candidate?.target ?? command.target;
      if (match.status === "none") {
        await say(
          `🎙️ Nothing matched **${heard}**. Say the thread's tag — ` +
            (sessions
              .filter((s) => s.voice_label)
              .slice(0, 8)
              .map((s) => `\`${s.voice_label}\` ${s.thread_name ?? s.working_dir ?? ""}`)
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
