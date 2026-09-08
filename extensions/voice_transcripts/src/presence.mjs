export function canControl(config, member, action) {
  if (member.guildId !== config.guildId) return false;
  if (member.userId === config.ownerId) return true;
  return action === "pause" && member.channelId === config.channelId;
}

export function createPresenceController(io) {
  let tail = Promise.resolve();
  const serialize = (fn) => {
    const next = tail.then(fn);
    tail = next.catch(() => {});
    return next;
  };
  async function reconcile() {
    if (io.ownerChannel() !== io.channelId) {
      io.setPaused(false);
      if (io.active()) await io.stop();
      return;
    }
    if (io.isPaused()) {
      if (io.active()) await io.stop();
      return;
    }
    if (io.active()) await io.reconnect();
    else await io.start();
    // Presence may have changed while Discord was connecting.
    if (io.ownerChannel() !== io.channelId && io.active()) await io.stop();
  }
  return {
    reconcile: () => serialize(reconcile),
    pause: () =>
      serialize(async () => {
        io.setPaused(true);
        if (io.active()) await io.stop();
      }),
    resume: () =>
      serialize(async () => {
        io.setPaused(false);
        await reconcile();
      }),
  };
}
