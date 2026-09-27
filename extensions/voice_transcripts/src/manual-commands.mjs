/** Exact text commands; no model, slash-command registration, or arbitrary room. */
export function createVoiceTextCommands({
  config,
  controller,
  logger = console,
}) {
  return async (message) => {
    if (
      message.guildId !== config.guildId ||
      message.channelId !== config.transcriptChannelId ||
      message.author?.id !== config.ownerId ||
      message.author.bot ||
      message.webhookId
    )
      return;
    const match = String(message.content ?? "")
      .trim()
      .match(/^!voice(?:\s+(join|leave))?$/i);
    if (!match) return;
    const action = match[1]?.toLowerCase();
    let content =
      "Use `!voice join` to join the recording room, or `!voice leave` to remove DrewAI; auto-join is off.";
    if (action) {
      try {
        const result = await controller[action]();
        content =
          action === "leave"
            ? "DrewAI left voice and will stay out until you send `!voice join`."
            : result.status === "joined"
              ? "DrewAI joined voice. Send `!voice leave` here to remove it."
              : result.status === "owner-absent"
                ? `Join <#${config.channelId}> first, then send \`!voice join\` here.`
                : "Voice join was canceled; DrewAI stayed out.";
      } catch (error) {
        logger.error?.("[voice] text control failed:", error.message);
        content =
          "The voice command failed; no automatic join retry will run. Please try the command again.";
      }
    }
    await message.reply({
      content,
      allowedMentions: { parse: [], repliedUser: false },
    });
  };
}
