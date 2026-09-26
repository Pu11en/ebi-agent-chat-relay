/**
 * The list of tags, where the speaker is already looking.
 *
 * A tag is only useful if it is known before it is needed, and a tag that has
 * to be looked up somewhere else is one nobody uses. So the roster lives as a
 * single message in the transcript channel — the channel already open beside
 * the voice room — and is rewritten in place rather than reposted, so it stays
 * at hand without becoming a feed.
 *
 * Rendering is pure and content-addressed: the caller only writes to Discord
 * when the text actually changes, which is what keeps a five-second health loop
 * from spending its rate limit on an unchanged list.
 */

import { untagged } from "./targets.mjs";

const MAX_ROWS = 12;

/** @returns {string|null} The roster text, or null when no thread has a tag. */
export function renderRoster(sessions) {
  const tagged = (sessions ?? [])
    .filter((s) => s.voice_label)
    .sort(
      (a, b) =>
        (b.state === "running") - (a.state === "running") ||
        String(b.last_used_at ?? "").localeCompare(String(a.last_used_at ?? "")),
    )
    .slice(0, MAX_ROWS);
  if (!tagged.length) return null;

  const rows = tagged.map((s) => {
    const name = untagged(s.thread_name) || s.working_dir || `thread ${s.thread_id}`;
    const live = s.state === "running" ? " · 🟢 working" : "";
    return `\`${s.voice_label}\` — ${name}${live}`;
  });
  return (
    "🏷️ **Say the tag.** e.g. *“put this in the " +
    tagged[0].voice_label +
    ' thread, <what to do>”*\n' +
    rows.join("\n")
  );
}
