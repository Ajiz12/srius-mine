#!/usr/bin/env bash
# Status read-only Srius mine bot
cd "$(dirname "$0")"
echo "=== PM2 ==="
pm2 describe srius-mine 2>/dev/null | grep -E "status|uptime|restarts|pid" || echo "tidak terdaftar"
echo
echo "=== 25 log terakhir ==="
tail -25 bot.log 2>/dev/null || echo "(belum ada log)"
echo
echo "=== Live API check ==="
python3 - <<'PY'
import json, sys
sys.path.insert(0, '.')
import srius_mine as b
acc = b.load_account()
if not acc:
    print("belum ada akun")
    sys.exit()
s, r = b.status(acc)
if s == 200:
    d = r["body"]["data"]
    print(f"userCode   : {acc['userCode']}")
    print(f"status     : {d.get('miningStatus')}")
    print(f"rate       : {d.get('ratePerMinute')} SST/menit")
    print(f"per cycle  : {d.get('projectedRewardPerCycle')} SST")
    print(f"total      : {d.get('totalMiningReward')} SST")
    print(f"cycle ends : {d.get('largeCycleEndsAt')}")
else:
    print("API:", s, json.dumps(r)[:300])
PY
