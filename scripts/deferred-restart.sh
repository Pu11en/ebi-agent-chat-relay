#!/bin/bash
# One-shot helper: wait for in-flight Discord sessions to finish, then restart
# the bot service. Launched detached (systemd-run --user) so it survives the
# very restart it orders.
LOG=/home/drewp/main-projects/ebi-agent-chat-relay/.ccdb-live-restart.log
say() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

say "deferred restart armed (fix/usable-product-20260929: sessions follow Discord)"
sleep 75

# The Jester snapshot is the read-only view of the same rows: it never mints a
# tag or renames a thread, so polling it while waiting is free of side effects.
deadline=$(( $(date +%s) + 480 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    running=$(curl -s -H "Authorization: Bearer $CCDB_API_SECRET" "$CCDB_API_URL/api/jester/sessions" \
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
