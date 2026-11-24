#!/usr/bin/env bash
# cron/wind_shift_watch.sh — Checks forecast wind shift for active hunting camps
# Schedule suggestion: 0 5,13 * * * (5 AM & 1 PM pre-sit checks)
set -euo pipefail
LODGE_DIR="${LODGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -d "$LODGE_DIR/.george" ] || LODGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
MODULE_DIR="${MODULE_DIR:-$LODGE_DIR/.george/modules/hunting-buddy}"
[ -d "$MODULE_DIR/tools" ] || MODULE_DIR="$LODGE_DIR/.george/modules/hunting-buddy"
MODULE_DATA_DIR="${MODULE_DATA_DIR:-$LODGE_DIR/.george/module_data/hunting-buddy}"
export MODULE_DIR MODULE_DATA_DIR LODGE_DIR
PY="$LODGE_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

[ -d "$MODULE_DIR" ] || exit 0
"$PY" - "$MODULE_DIR" <<'PY'
import sys

mod_dir = sys.argv[1]
sys.path.insert(0, f"{mod_dir}/tools")
import hunt_db, hunt_weather, hunt_plan

conn = hunt_db.get_conn()
for p in hunt_db.list_properties(conn):
    pname = p.get("name") or f"Property {p.get('id')}"
    try:
        w = hunt_weather.fetch_hunt_weather(p["center_lat"], p["center_lon"], days=1)
        hourly = w.get("hourly") or []
        if not hourly:
            continue
        cur = hourly[0]
        plan = hunt_plan.evaluate_hunt_plan(p["id"], cur["wind_deg"], cur["wind_mph"], cur["thermal_mode"])
        conflicts = plan.get("conflicts") or []
        if conflicts:
            wcomp = cur.get("wind_compass", "")
            wmph = cur.get("wind_mph", 0)
            print(f"[HUNT ALERT] {pname}: {len(conflicts)} downwind conflict(s) on {wcomp} {wmph} mph wind")
    except Exception as exc:
        print(f"[HUNT WATCH] {pname}: {exc}")
PY
