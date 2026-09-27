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
  parseNewSession,
  parseTidyUp,
} from "./command.mjs";
import { matchFolder } from "./folders.mjs";
import { parseRuntime } from "./runtime.mjs";
import { resolveTarget, untagged } from "./targets.mjs";

const IGNORED = { status: "ignored" };

/** How long a named-but-unfinished command waits for its instruction. */
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
/**
 * How long a silence has to be before a run is sent.
 *
 * Measured over 8,501 real utterances: his median gap between sentences is 3.2s
 * and 80% of gaps are under 12.7s, so anything near four seconds cuts him off
 * mid-thought. Ten seconds is his own number and it sits inside the measurement.
 *
 * Timed from when he *stopped speaking*, never from when the text arrived —
 * transcription lags by up to 30s on this machine, and timing off arrival would
 * count that lag as a pause.
 */
export const SILENCE_SEND_MS = 10_000;

/** Ends an open conversation without naming another thread. */
/**
 * How many sessions one "close everything" may end.
 *
 * Each close archives a Discord thread, and Discord meters that hard. Against
 * the 300 finished sessions this actually ran into, an unbounded loop is a
 * three-hundred-call burst from one spoken sentence — and a rate-limited close
 * fails, so the sweep would half-work and report success. Bounded, it says how
 * many are left and the sentence can simply be repeated.
 */
export const MAX_TIDY_UP_CLOSES = 25;

/** Spacing between closes, so a full sweep is paced rather than a burst. */
const TIDY_UP_GAP_MS = 250;

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
  // Rewrites a line already posted. A surface that cannot edit simply omits it
  // and gets a second line instead — the confirmation matters more than the
  // tidiness.
  revise = null,
  logger = console,
  source = "voice",
  now = () => Date.now(),
  silenceMs = SILENCE_SEND_MS,
  // Injected so the pacing is asserted rather than slept through.
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (handle) => clearTimeout(handle),
  sessionsTtlMs = 10_000,
  // Where a session goes when the folder could not be worked out. Opening it
  // somewhere beats refusing: the thread gets a tag, says what it misheard, and
  // can be corrected by talking to it — which is cheaper than saying the whole
  // sentence again.
  fallbackDir = "/home/drewp/main-projects",
}) {
  /**
   * The current run: one locked target and everything said since it was locked.
   *
   * A run replaces the old "send each sentence, then keep listening for 90s".
   * That sent one thought as three messages, and when the recogniser missed the
   * *next* tag the sentences after it flowed silently into the previous thread —
   * `nami` swallowed everything and `luffy` got nothing.
   *
   * `spokeUntil` is when he stopped talking, which is what the silence is
   * measured from. `timer` is armed for `silenceMs` after that and re-armed on
   * every further utterance.
   *
   * @type {{session: object, target: string, parts: string[], spokeUntil: number,
   *         timer: unknown} | null}
   */
  let run = null;
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

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function say(message) {
    // A room that cannot be spoken to is still a room a command was sent from.
    try {
      return await announce(message);
    } catch (error) {
      logger.warn?.("[control] announcement failed:", error.message);
      return null;
    }
  }

  /**
   * Rewrite the line this run already posted, or post a new one.
   *
   * A run says "Listening for X" the moment it locks, so he knows the tag landed
   * without waiting ten seconds, and the same line then becomes the confirmation
   * of what was sent. Two lines per run is twice the channel for no extra
   * information.
   */
  async function reviseOrSay(handle, message) {
    if (handle == null || typeof revise !== "function") return await say(message);
    try {
      await revise(handle, message);
      return handle;
    } catch (error) {
      logger.warn?.("[control] could not rewrite the line:", error.message);
      return await say(message);
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

  async function send(session, prompt, line = null) {
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
    await reviseOrSay(line, `🎙️ → **${describe(session)}**: ${prompt}`);
    logger.info?.(`[control] sent to thread ${session.thread_id}`);
    // Deliberately does *not* keep listening. Staying attached after a send is
    // what sent his untagged sentences into the previous thread when the
    // recogniser missed the next tag; the next run must name its own target.
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
    const spare = (live ?? []).filter(
      (s) =>
        s.state !== "running" &&
        // Already closed is not "needs closing": /api/sessions reports closed
        // sessions too, so without this the sweep re-closes them and reports a
        // number that means nothing.
        !s.closed &&
        String(s.thread_id) !== String(run?.session?.thread_id ?? ""),
    );
    if (!spare.length) {
      await say("🎙️ Nothing to close — every session is in use.");
      return { status: "tidied", closed: 0, failed: 0, remaining: 0 };
    }
    let closed = 0;
    let failed = 0;
    // Sequential and paced on purpose: these are dependent on a shared Discord
    // budget, so Promise.all would turn the sweep into the burst it is meant to
    // avoid.
    const batch = spare.slice(0, MAX_TIDY_UP_CLOSES);
    for (const session of batch) {
      try {
        await client.close(session.thread_id, { actor: ownerId });
        closed += 1;
      } catch (error) {
        failed += 1;
        logger.warn?.(`[control] could not close ${session.thread_id}: ${error.message}`);
      }
      if (TIDY_UP_GAP_MS) await sleep(TIDY_UP_GAP_MS);
    }
    const remaining = spare.length - batch.length;
    await say(
      `🎙️ Closed ${closed} session${closed === 1 ? "" : "s"} you were not using` +
        (failed ? ` (${failed} could not be reached)` : "") +
        (remaining ? ` — ${remaining} still to go, say it again.` : "") +
        ".",
    );
    return { status: "tidied", closed, failed, remaining };
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

  /** When he stopped speaking, from the capture time rather than arrival. */
  function spokeUntilFrom(capturedAt, durationMs) {
    const started = capturedAt ? Date.parse(capturedAt) : NaN;
    if (Number.isNaN(started)) return now();
    return started + (Number(durationMs) || 0);
  }

  /** Arm (or re-arm) the silence timer for the current run. */
  function armSilence() {
    if (!run) return;
    if (run.timer) clearTimer(run.timer);
    const remaining = Math.max(0, run.spokeUntil + silenceMs - now());
    run.timer = setTimer(() => {
      void flushRun();
    }, remaining);
  }

  /**
   * Ten seconds have passed. Send the whole run as one message and let go.
   *
   * Letting go is the point. The old behaviour kept listening for 90 seconds
   * after a send, so a sentence whose tag the recogniser missed went to the
   * previous thread instead of nowhere. Now the next run needs its own tag, and
   * no tag means nothing is sent — which is how he finds out the tag was missed.
   */
  async function flushRun() {
    if (!run) return IGNORED;
    const { session, parts, line } = run;
    if (run.timer) clearTimer(run.timer);
    run = null;
    const prompt = parts.join(" ").replace(/\s+/g, " ").trim();
    if (prompt.length < MIN_PROMPT_CHARS) return IGNORED;
    return await send(session, prompt, line);
  }

  return {
    /** Exposed so a shutdown does not silently swallow a run in progress. */
    async flush() {
      return await flushRun();
    },

    async handleUtterance({ userId, text, capturedAt = null, durationMs = 0 }) {
      if (!enabled) return { status: "disabled" };
      if (String(userId) !== String(ownerId)) return IGNORED;

      const said = String(text ?? "").trim();
      const spokeUntil = spokeUntilFrom(capturedAt, durationMs);

      // ---- a run is open: everything is words for it --------------------
      if (run) {
        // There is deliberately no way to cancel a run. "Stop listening" used to
        // exist for the ninety-second window, where being stuck on the wrong
        // thread was expensive. A run lasts ten seconds: letting it send and
        // correcting in the next one is fewer things to remember than a phrase
        // that has to be recognised correctly to work at all.
        //
        // Recogniser noise must not hold the run open. "Thank you." is what this
        // model emits for near-silence, so counting it as speech would reset the
        // clock every few seconds and the message would never be sent.
        if (said.length < MIN_PROMPT_CHARS || EMPTY_TALK.test(said)) return IGNORED;
        // A later tag is deliberately ignored: the first one owns the run, so
        // naming another thread mid-sentence can never redirect what he is saying.
        run.parts.push(said);
        run.spokeUntil = Math.max(run.spokeUntil, spokeUntil);
        armSilence();
        return { status: "collecting", target: run.target, parts: run.parts.length };
      }

      // ---- nothing is open: only a tag, or a standalone command, starts -
      let live;
      try {
        live = await sessions();
      } catch (error) {
        // Nothing is open and the tag list is unreadable, so this utterance
        // cannot be understood — and most utterances in a recorded room are not
        // commands. Complaining about every one of them would fill the channel
        // with errors about small talk, so it stays silent; a run in progress is
        // the case where he is expecting an answer, and that is handled above.
        logger.warn?.(`[control] could not read sessions: ${error.message}`);
        return IGNORED;
      }

      if (parseTidyUp(said)) return await tidyUp(live);

      const addressed = parseByTag(
        said,
        live.map((s) => ({ label: s.voice_label, aliases: s.voice_label_aliases })),
      );

      if (!addressed) {
        // "New session in X" names no thread because there is no thread yet.
        const opening = parseNewSession(said);
        if (opening) return await open(opening);
      }

      const command = addressed;
      if (!command) {
        // No tag and no named thread. Nothing is sent, and that silence is the
        // whole point: with an unreliable recogniser "nothing happened" is
        // information he can act on, and "it went somewhere you did not choose"
        // is not.
        return IGNORED;
      }

      const match = resolveTarget(command.candidates, live);
      const heard = match.candidate?.target ?? command.target;
      if (match.status === "none") {
        // Unreachable by construction: `parseByTag` only matches a word that is
        // already a tag or alias of a session in `live`, and this looks it up in
        // that same list. Kept as a log rather than a message to the room —
        // there is nothing useful to tell him about a thing that cannot happen.
        logger.warn?.(`[control] tag ${heard} parsed but did not resolve`);
        return IGNORED;
      }
      const prompt = match.candidate.prompt;

      // "Bravo, switch to opus" changes who answers rather than asking them
      // anything, and it is immediate — there is nothing to collect.
      const runtime = parseRuntime(prompt);
      if (runtime) return await tune(match.session, runtime);

      run = {
        session: match.session,
        target: heard,
        parts: prompt ? [prompt] : [],
        spokeUntil,
        timer: null,
        line: null,
      };
      armSilence();
      // Said immediately so he knows the tag landed without waiting ten seconds;
      // the same line is rewritten into the confirmation when the run is sent.
      run.line = await say(
        `🎙️ Listening for **${describe(match.session)}** — ` +
          `sends ${Math.round(silenceMs / 1000)}s after you stop.`,
      );
      return { status: "collecting", target: heard, parts: run.parts.length };
    },
  };
}
