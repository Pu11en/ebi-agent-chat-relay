# Notify the person who messaged the bot

## How notifications will work

- **Drew sends a request → the bot pings Drew.**
- **David sends a request → the bot pings David.**
- When someone else sends the next message in the same thread, the next run’s alert follows that person.
- Both people keep access to the shared conversation; following a thread does not make someone the recipient of every agent ping.
- Explicit approval prompts already target the person asking; this change fixes the end-of-turn “your reply is needed” alert.
- Background jobs without an identified requester retain their configured fallback recipients; quiet worker threads remain quiet.

## Verification and activation

✅ Local change: 3,406 tests pass; lint, formatting, type checking, targeted security review, and imports pass. An unrelated morning-summary test needed a fixed fixture date to stop depending on the day it runs.

⏳ The current bot must restart to load the change. Activation is queued for an idle window after this conversation finishes; it will verify startup health and report the outcome in the lounge. David’s separate installation is not updated by that restart.

## Discord notification settings

✅ The server default has been changed from **All Messages** to **Only @mentions**.

Discord accounts can override that default. If either person previously chose **All Messages**, they need to change their server/channel/thread notification settings to **Only @mentions** themselves; the bot cannot edit another person’s account preferences.

The bot change controls explicit agent pings. It does not synchronize personal unread badges or clear notifications already delivered to another device.

## Give David’s local Claude this follow-up

David’s bot runs a separate installation, so changing Drew’s copy does not update David’s copy. Paste this into the local Claude session already doing his setup:

```text
Update my Discord bridge so only the human whose message starts a run receives that run’s agent notification. Both Drew (488763953397235712) and David (718234548139196476) must retain access to shared threads.

Use tests first. In claude_discord/discord_ui/thread_dashboard.py, add an optional keyword-only notify_user_id: int | None = None argument to ThreadStatusDashboard.set_state. When supplied, use only that ID for the WAITING_INPUT mention, rather than the configured owner/operator list. Keep existing fallback behavior when no requester is supplied. A user muted from fallback broadcasts must still receive replies to their own explicit requests. Preserve quiet-thread handling and duplicate-notification suppression.

Restrict allowed_mentions to the intended recipient IDs; disable role, everyone, and reply-author mentions. Keep recipients local to that set_state call so simultaneous threads cannot share or overwrite a notification target.

In claude_discord/cogs/claude_chat.py, pass notify_user_id=user_message.author.id when _run_claude transitions the dashboard to WAITING_INPUT. Check existing RunConfig.notify_user_id already uses the message author for approval prompts; preserve that behavior. Check slash-command runs use interaction.user.id.

Add regression tests for both operators, switching authors within one thread, parallel threads with different authors, explicit requesters muted from fallback broadcasts, and existing quiet/duplicate behavior. Run the repository’s required checks, commit only this change locally, and restart only this computer’s bot when it has no active sessions. Do not push or publish. Report whether the change is live or still waiting for a restart.
```
