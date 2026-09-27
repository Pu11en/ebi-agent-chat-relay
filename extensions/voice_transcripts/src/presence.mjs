export function canControl(config, member, action) {
  if (member.guildId !== config.guildId) return false;
  if (member.userId === config.ownerId) return true;
  return action === "pause" && member.channelId === config.channelId;
}

export function createPresenceController(io) {
  let tail = Promise.resolve();
  // Deliberately not persisted: restarting is not permission to join the room.
  let requested = false;
  let revision = 0;
  const serialize = (fn) => {
    const next = tail.then(fn);
    tail = next.catch(() => {});
    return next;
  };
  async function reconcile() {
    if (io.ownerChannel() !== io.channelId) {
      requested = false;
      io.setPaused(false);
    }
    if (!requested || io.isPaused() || (io.connected && !io.connected())) {
      requested = false;
      if (io.active()) await io.stop();
    }
    // Health/presence checks may stop recording, but never connect.
    return { status: requested && io.active() ? "joined" : "left" };
  }
  function stopRequested(paused) {
    revision++;
    requested = false;
    // Interrupt voice.entersState immediately, even while join owns the queue.
    io.disconnect?.();
    return serialize(async () => {
      io.setPaused(paused);
      if (io.active()) await io.stop();
      return { status: "left" };
    });
  }
  return {
    reconcile: () => serialize(reconcile),
    join() {
      const ticket = ++revision;
      return serialize(async () => {
        if (ticket !== revision) return { status: "left" };
        if (io.ownerChannel() !== io.channelId) {
          await reconcile();
          return { status: "owner-absent" };
        }
        requested = true;
        io.setPaused(false);
        const mayJoin = () =>
          requested &&
          ticket === revision &&
          io.ownerChannel() === io.channelId;
        try {
          if (!io.active()) await io.start(mayJoin);
          else if (io.connected && !io.connected()) await io.reconnect(mayJoin);
        } catch (error) {
          requested = false;
          if (io.active()) await io.stop();
          if (ticket !== revision) return { status: "left" };
          throw error;
        }
        if (!mayJoin()) {
          requested = false;
          if (io.active()) await io.stop();
          return { status: "left" };
        }
        return await reconcile();
      });
    },
    leave: () => stopRequested(false),
    pause: () => stopRequested(true),
    // Old buttons may still exist in Discord. They must not re-enable auto-join.
    resume: () =>
      serialize(async () => {
        io.setPaused(false);
        return await reconcile();
      }),
  };
}
