# David setup — verified Discord state

## Active
Checked the live Discord messages after the installation receipt.
- Activation report at 2026-09-19 02:42:40 UTC says bridge e72a851 and the auth-header fix are loaded, together with CompletionPingCog, FolderLauncherCog and ThreadMembersCog.
- David bot 1550644558176460961 posted its New session button in the correct control-center 1550644904617582662: https://discord.com/channels/1546639912848199742/1550644904617582662/1550698614718988320
- New thread 1550698775436066836 is under that same parent and its welcome identifies C:/Users/david/projects. It has not yet received a human task in the messages checked.
- The installation receipt cited Drew's channel 1546658182989086720, but live placement is correct; do not change routing based on that stale report.

## Remaining interface gaps
The live persistent panel has only New session. The agreed Resume and Favorite folders management buttons are absent; favorites use slash commands according to the receipt. Keyword search is not reported as implemented.

## Still unverified
A real coding task in the newly created folder thread, requester-only notifications after the restart with alternating operators, and Windows reboot/logon survival. The last duplicated completion pings were before the restart and do not establish that the fix is still broken afterward. No direct Windows filesystem/process access was available to this agent.


## Folder/category upgrade, 2026-09-18
Created start-here channels: Lenovo 1550703906483343444, iMac 1550703908559527947,
David 1550703910983827577. No conversation history was removed. Their existing
workers channels are the session destinations in launcher-channels.json.

New shared code adds real filesystem navigation, explicit Start here, a separate
launcher home, and CCDB_ALLOWED_CATEGORY_IDS for commands/autocomplete/core chat.
Local settings now select Lenovo only; its activation is prepared for an idle
restart. David and iMac have not yet applied this latest shared upgrade.
Their new home channels do not by themselves prove their launcher installation.

Validation: 3110 tests passed in a clean snapshot; the previously aggregate-hanging
ingest test passed separately (1 more). Ruff lint/format and pyright pass. Reviewed
new category checks and UI authorization; no subprocess, credential or permission-mode
changes. Existing unrelated local edits excluded from snapshot and commit.

Discord's administrator command-permission bypass means native slash entries can
remain visible. The runtime guard prevents wrong-category execution; it does not
hide entries, remove bot members, or restrict independent custom listeners/API jobs.
Source: https://docs.discord.com/developers/interactions/application-commands#permissions


## User correction: control-center, not another channel
The user explicitly rejected separate start-here channels. Home mappings now point
to the existing control-centers; workers destinations are unchanged. DrewAI’s original
control-center panel was restored live. A tested silent bottom shortcut is being
prepared for idle activation so users do not need to hunt through channel history.
Earlier start-here links/status above are historical and no longer the intended flow.

Unused extra start-here channels were removed only after checking they contained
no user messages or active/archived threads. All three home IDs now reference
the original control-centers. Validation: 3114 suite tests plus the isolated ingest
test pass; ruff lint/format, pyright and changed-code security checks pass.
