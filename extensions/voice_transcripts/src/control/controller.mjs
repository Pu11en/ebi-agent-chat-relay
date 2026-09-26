/**
 * One spoken sentence, all the way to a running session.
 *
 * The order of the guards is the design. Only the owner is obeyed — everyone
 * in a recorded room is transcribed, and the room is not an authorisation
 * boundary. Only a sentence that parses as a command goes any further, and
 * everything else leaves no trace at all: a controller that answered "I didn't
 * catch that" to ordinary conversation would make the room unusable.
 *
 * Every outcome the speaker cannot see for themselves is said back in the
 * transcript channel — what was sent and where, which name matched nothing,
 * which two threads both answered to it. The one thing never reported is
 * silence, because silence is the normal case.
 */

import { parseCommand } from "./command.mjs";
import { resolveTarget } from "./targets.mjs";

const IGNORED = { status: "ignored" };

export function createVoiceController({
  ownerId,
  enabled,
  client,
  announce,
  logger = console,
  source = "voice",
}) {
  async function say(message) {
    // A room that cannot be spoken to is still a room a command was sent from.
    try {
      await announce(message);
    } catch (error) {
      logger.warn?.("[control] announcement failed:", error.message);
    }
  }

  function describe(session) {
    return session.thread_name || session.working_dir || `thread ${session.thread_id}`;
  }

  return {
    async handleUtterance({ userId, text }) {
      if (!enabled) return { status: "disabled" };
      if (String(userId) !== String(ownerId)) return IGNORED;

      const command = parseCommand(text);
      if (!command) return IGNORED;

      if (command.kind === "incomplete") {
        await say(
          `🎙️ I heard **${command.target}** but no instruction after it. ` +
            `Say it as one sentence: "put this in the ${command.target} thread <what to do>".`,
        );
        return { status: "incomplete", target: command.target };
      }

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
          `🎙️ Nothing matched **${heard}**. Open sessions: ` +
            (sessions.map(describe).slice(0, 8).join(", ") || "none"),
        );
        return { status: "no-target", target: heard };
      }
      if (match.status === "ambiguous") {
        await say(
          `🎙️ **${heard}** matches more than one thread: ` +
            `${match.options.map(describe).join(" · ")}. Say more of the name.`,
        );
        return { status: "ambiguous", options: match.options };
      }

      const prompt = match.candidate.prompt;
      try {
        await client.sendSpoken({
          threadId: match.session.thread_id,
          text: prompt,
          speakerId: String(ownerId),
          source,
        });
      } catch (error) {
        await say(`🎙️ Could not deliver that to **${describe(match.session)}** — ${error.message}`);
        return { status: "failed", error: error.message };
      }

      await say(`🎙️ → **${describe(match.session)}**: ${prompt}`);
      logger.info?.(`[control] sent to thread ${match.session.thread_id}`);
      return { status: "sent", session: match.session, prompt };
    },
  };
}
