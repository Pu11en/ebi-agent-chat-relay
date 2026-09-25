#!/bin/bash
# One-shot: restart the bot, then record what actually happened. Launched
# detached with systemd-run --user because an agent session runs *inside*
# ebi-agent-chat-relay.service — a restart ordered from there kills the caller
# before it can report, so the check has to outlive it.
LOG=/home/drewp/main-projects/ebi-agent-chat-relay/.ccdb-live-restart.log
DB=/home/drewp/main-projects/ebi-agent-chat-relay/data/sessions.db
say() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

say "restart armed (feat/quick-chat: control center typing-only, panel off)"
sleep 40
say "restarting ebi-agent-chat-relay.service"
systemctl --user restart ebi-agent-chat-relay.service
say "restart returned $?"
sleep 30
say "service state: $(systemctl --user is-active ebi-agent-chat-relay.service)"
say "commit: $(git -C /home/drewp/main-projects/ebi-agent-chat-relay rev-parse --short HEAD)"
left=$(sqlite3 "$DB" "select count(*) from settings where key in ('launcher.panel:1546658182989086720','launcher.shortcut:1546658182989086720');")
say "control-center panel rows remaining (want 0): $left"
