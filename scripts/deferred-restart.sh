#!/bin/bash
# One-shot helper: wait for in-flight Discord sessions to finish, then restart
# the bot service. Launched detached (systemd-run --user) so it survives the
# very restart it orders.
LOG=/home/drewp/main-projects/ebi-agent-chat-relay/.ccdb-live-restart.log
say() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

say "deferred restart armed (feat/cd-folder-autocomplete: /cd autocomplete + sessions in control-center)"
sleep 75

deadline=$(( $(date +%s) + 480 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    running=$(curl -s -H "Authorization: Bearer $CCDB_API_SECRET" "$CCDB_API_URL/api/sessions" \
        | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print("?"); raise SystemExit
s=d.get("sessions",d) if isinstance(d,dict) else d
print(sum(1 for x in s if x.get("state")=="running"))' 2>/dev/null)
    say "in-flight sessions: $running"
    [ "$running" = "0" ] && break
    sleep 20
done

say "restarting ebi-agent-chat-relay.service"
systemctl --user restart ebi-agent-chat-relay.service
say "restart command returned $?"
