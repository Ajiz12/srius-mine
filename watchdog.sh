#!/usr/bin/env bash
# Restart srius-mine kalau mati ATAU hang (log tidak update > 15 menit)
cd "$(dirname "$0")"
LOG=bot.log
STALE_MIN=15

if ! pm2 describe srius-mine 2>/dev/null | grep -q "online"; then
  echo "$(date '+%F %T') srius-mine mati — restart" >> watchdog.log
  pm2 restart srius-mine >> watchdog.log 2>&1
  exit 0
fi

# cek staleness log
if [ -f "$LOG" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
  if [ "$age" -gt $((STALE_MIN * 60)) ]; then
    echo "$(date '+%F %T') srius-mine hang (log ${age}s tidak update) — restart" >> watchdog.log
    pm2 restart srius-mine >> watchdog.log 2>&1
  fi
fi
