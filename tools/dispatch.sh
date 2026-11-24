#!/usr/bin/env bash
# tools/dispatch.sh — Blue Lodge Native Tool Dispatcher for mod-hunting-buddy
set -euo pipefail

TOOL_NAME="${1:-}"
ARGS_JSON="${2:-}"
[ -z "$ARGS_JSON" ] && ARGS_JSON="{}"
WORKDIR="${3:-$PWD}"

LODGE_DIR="${LODGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
MODULE_DIR="${MODULE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODULE_DATA_DIR="${MODULE_DATA_DIR:-$LODGE_DIR/.george/module_data/hunting-buddy}"
export MODULE_DIR MODULE_DATA_DIR LODGE_DIR
mkdir -p "$MODULE_DATA_DIR"

PY="$LODGE_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

_maybe_publish() {
    local fpath="$1" caption="$2"
    [ -f "$fpath" ] || return 0
    if [ -f "$LODGE_DIR/lib/tools/native_tools.sh" ]; then
        local pub_dir="$LODGE_DIR/.george/artifacts/${GEORGE_DISCORD_CHANNEL:-local}"
        mkdir -p "$pub_dir"
        cp -f "$fpath" "$pub_dir/$(basename "$fpath")"
    fi
    if [ -n "${GEORGE_DISCORD_CHANNEL:-}" ] && [ -f "$LODGE_DIR/lib/comm/discord_bridge.sh" ]; then
        bash -c "source '$LODGE_DIR/lib/comm/discord_bridge.sh' && discord_send_file '$GEORGE_DISCORD_CHANNEL' '$fpath' '$caption'" >/dev/null 2>&1 || true
    fi
}

case "$TOOL_NAME" in
    hunt_import)
        printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_import.py"
        ;;
    hunt_export)
        res=$(printf '%s' "$ARGS_JSON" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_export
p = json.loads(sys.stdin.read() or "{}")
pub = bool(p.pop("publish_discord", False))
out = hunt_export.run_export(property_id=p.get("property_id"), fmt=p.get("fmt", "gpx"))
out["publish_discord"] = pub
out.pop("content", None)
print(json.dumps(out, indent=2))
')
        if printf '%s' "$res" | jq -e '.publish_discord == true and (.file_path // "") != ""' >/dev/null 2>&1; then
            fp=$(printf '%s' "$res" | jq -r '.file_path')
            _maybe_publish "$fp" "Hunting Buddy onX GPX/KML Export"
        fi
        printf '%s\n' "$res"
        ;;
    hunt_weather)
        printf '%s' "$ARGS_JSON" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db, hunt_weather
p = json.loads(sys.stdin.read() or "{}")
lat, lon = p.get("lat"), p.get("lon")
if lat is None or lon is None:
    conn = hunt_db.get_conn()
    b = hunt_db.get_property_bundle(conn, property_id=p.get("property_id"))
    if not b:
        print(json.dumps({"status": "error", "message": "Provide lat/lon or import a property first."}))
        sys.exit(1)
    lat, lon = b["property"]["center_lat"], b["property"]["center_lon"]
print(json.dumps(hunt_weather.fetch_hunt_weather(float(lat), float(lon), int(p.get("days", 3))), indent=2))
'
        ;;
    hunt_plan)
        printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_plan.py"
        ;;
    hunt_topo)
        printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_topo.py"
        ;;
    hunt_routes)
        printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    hunt_fish)
        printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    hunt_render_map)
        res=$(printf '%s' "$ARGS_JSON" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_render_map
p = json.loads(sys.stdin.read() or "{}")
pub = bool(p.pop("publish_discord", True))
out = hunt_render_map.render_tactical_map(**p)
out["publish_discord"] = pub
print(json.dumps(out, indent=2))
')
        if printf '%s' "$res" | jq -e '.publish_discord == true and (.png_path // "") != ""' >/dev/null 2>&1; then
            fp=$(printf '%s' "$res" | jq -r '.png_path')
            _maybe_publish "$fp" "Hunting Buddy Tactical Wind & Scent Map"
        fi
        printf '%s\n' "$res"
        ;;
    hunt_log)
        printf '%s' "$ARGS_JSON" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db
p = json.loads(sys.stdin.read() or "{}")
act = p.get("action")
conn = hunt_db.get_conn()
if act == "list_properties":
    print(json.dumps({"status": "ok", "properties": hunt_db.list_properties(conn, channel_id=p.get("channel_id", ""))}, indent=2))
elif act == "set_privacy":
    pid = p.get("property_id")
    if not pid:
        b = hunt_db.get_property_bundle(conn)
        pid = b["property"]["id"] if b else None
    if not pid:
        print(json.dumps({"status": "error", "message": "No property found"})); sys.exit(1)
    print(json.dumps(hunt_db.set_property_privacy(conn, int(pid), p.get("visibility", "public"), p.get("channel_id", "")), indent=2))
elif act == "add_waypoint":
    pid = p.get("property_id")
    if not pid:
        b = hunt_db.get_property_bundle(conn)
        pid = b["property"]["id"] if b else None
    if not pid:
        print(json.dumps({"status": "error", "message": "Import a property first"})); sys.exit(1)
    cur = conn.execute(
        "INSERT INTO waypoints (property_id, name, kind, lat, lon, assigned_hunter, ideal_winds, notes, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, p.get("name", "Stand"), p.get("kind", "stand"), float(p["lat"]), float(p["lon"]), p.get("assigned_hunter", ""), p.get("ideal_winds") or p.get("preferred_winds", ""), p.get("notes", ""), hunt_db.now_iso())
    )
    conn.commit()
    print(json.dumps({"status": "ok", "waypoint_id": cur.lastrowid, "property_id": pid}, indent=2))
elif act == "add_sighting":
    pid = p.get("property_id")
    if not pid:
        b = hunt_db.get_property_bundle(conn)
        pid = b["property"]["id"] if b else None
    cnt = p.get("count", 1)
    sp = p.get("species", "whitetail")
    nt = p.get("notes", "")
    summary = p.get("summary") or f"{cnt} {sp} {nt}".strip()
    cur = conn.execute(
        "INSERT INTO sightings (property_id, hunter, observed_at, summary) VALUES (?, ?, ?, ?)",
        (pid, p.get("assigned_hunter", ""), hunt_db.now_iso(), summary)
    )
    conn.commit()
    print(json.dumps({"status": "ok", "sighting_id": cur.lastrowid}, indent=2))
else:
    print(json.dumps({"status": "error", "message": f"Unknown hunt_log action: {act}"})); sys.exit(1)
'
        ;;
    hunt_mobile)
        action=$(printf '%s' "$ARGS_JSON" | jq -r '.action // "list_hunters"')
        if [ "$action" = "issue_token" ]; then
            printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_mobile.py" issue_token
        else
            printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_mobile.py" list
        fi
        ;;
    hunt_plan_save)
        printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" plan
        ;;
    hunt_annotate)
        printf '%s' "$ARGS_JSON" | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" annotate
        ;;
    *)
        printf '{"status":"error","message":"Unknown hunting-buddy tool: %s"}\n' "$TOOL_NAME"
        exit 1
        ;;
esac
