#!/usr/bin/env bash
# tools/web_action.sh — Backend JSON action handler for Hunting Buddy Web UI & Mobile GPS Bridge
set -euo pipefail

ACTION="${1:-get_state}"
BODY="$(cat || true)"
[ -z "$BODY" ] && BODY="{}"

LODGE_DIR="${LODGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
MODULE_DIR="${MODULE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODULE_DATA_DIR="${MODULE_DATA_DIR:-$LODGE_DIR/.george/module_data/hunting-buddy}"
export MODULE_DIR MODULE_DATA_DIR LODGE_DIR
mkdir -p "$MODULE_DATA_DIR"

PY="$LODGE_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

case "$ACTION" in
    mobile_beacon)
        printf '%s' "$BODY" | "$PY" "$MODULE_DIR/tools/hunt_mobile.py" beacon
        ;;
    issue_beacon_token)
        printf '%s' "$BODY" | "$PY" "$MODULE_DIR/tools/hunt_mobile.py" issue_token
        ;;
    list_hunters)
        printf '%s' "$BODY" | "$PY" "$MODULE_DIR/tools/hunt_mobile.py" list
        ;;
    get_state)
        printf '%s' "$BODY" | "$PY" -c '
import json, os, re, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db, hunt_mobile
p = json.loads(sys.stdin.read() or "{}")
conn = hunt_db.get_conn()
props = hunt_db.list_properties(conn, channel_id=p.get("channel_id", ""))
bundle = hunt_db.get_property_bundle(conn, property_id=p.get("property_id"), channel_id=p.get("channel_id", ""))
hunters = hunt_mobile.list_hunters(bundle["property"]["id"] if bundle else None).get("hunters", [])
agent_name = os.environ.get("LODGE_NODE_NAME") or os.environ.get("AGENT_NAME") or ""
if not agent_name:
    conf_p = os.path.join(os.environ.get("LODGE_DIR", ""), ".george", "configs", "lodge.conf")
    if os.path.isfile(conf_p):
        try:
            txt = open(conf_p, "r", encoding="utf-8", errors="ignore").read()
            m = re.search(r"^\s*LODGE_NODE_NAME\s*=\s*[\x27\"]?([^\x27\"\r\n#]+)", txt, re.M)
            if m:
                agent_name = m.group(1).strip()
        except Exception:
            pass
agent_name = (agent_name[:1].upper() + agent_name[1:]) if agent_name else "Agent"
print(json.dumps({
    "status": "ok",
    "agent_name": agent_name,
    "properties": props,
    "active": bundle,
    "hunters": hunters,
}, indent=2))
'
        ;;
    import_data)
        printf '%s' "$BODY" | "$PY" -c '
import base64, json, os, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_import
p = json.loads(sys.stdin.read() or "{}")
fpath = p.get("file_path")
fcontent = None
fext = None
if p.get("boundary_geojson"):
    gj = p["boundary_geojson"]
    if isinstance(gj, dict) and gj.get("type") == "Polygon":
        gj = {
            "type": "Feature",
            "properties": {
                "owner_name": p.get("property_name") or p.get("name") or "My Hunting Camp",
                "parcel_pin": "drawn_camp_bounds",
                "acres": float(p.get("acres") or 0.0),
                "access_status": "owned",
                "plss_desc": "User-drawn camp boundary",
            },
            "geometry": gj,
        }
    fcontent = json.dumps(gj)
    fext = "geojson"
elif p.get("file_content_b64") and p.get("filename"):
    up_dir = os.path.join(os.environ["MODULE_DATA_DIR"], "uploads")
    os.makedirs(up_dir, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in os.path.basename(p["filename"]))
    fpath = os.path.join(up_dir, safe)
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(p["file_content_b64"]))
res = hunt_import.import_property(
    name=p.get("property_name") or p.get("name") or "My Hunting Camp",
    file_path=fpath,
    file_content=fcontent,
    file_ext=fext,
    lat=p.get("lat"),
    lon=p.get("lon"),
    fetch_state_parcels=bool(p.get("fetch_state_parcels", True)),
    visibility=p.get("visibility", "public"),
    channel_id=p.get("channel_id", ""),
)
print(json.dumps(res, indent=2))
'
        ;;
    save_waypoint)
        printf '%s' "$BODY" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db
p = json.loads(sys.stdin.read() or "{}")
conn = hunt_db.get_conn()
wid = p.get("id")
if wid:
    conn.execute(
        "UPDATE waypoints SET name=?, kind=?, lat=?, lon=?, assigned_hunter=?, ideal_winds=?, notes=?, updated_at=? WHERE id=?",
        (p.get("name", "Stand"), p.get("kind", "stand"), float(p["lat"]), float(p["lon"]), p.get("assigned_hunter", ""), p.get("ideal_winds") or p.get("preferred_winds", ""), p.get("notes", ""), hunt_db.now_iso(), int(wid))
    )
    conn.commit()
    print(json.dumps({"status": "ok", "id": int(wid)}))
else:
    pid = p.get("property_id")
    if not pid:
        b = hunt_db.get_property_bundle(conn)
        pid = b["property"]["id"] if b else None
    if not pid:
        # Auto-bootstrap a camp at the first dropped pin so new users never hit a dead end
        lat_v, lon_v = float(p["lat"]), float(p["lon"])
        bbox = [lon_v - 0.016, lat_v - 0.012, lon_v + 0.016, lat_v + 0.012]
        cname = p.get("property_name") or p.get("name") or "My Hunting Camp"
        cur_p = conn.execute(
            "INSERT INTO properties (name, state, center_lat, center_lon, bbox_json, channel_id, visibility, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cname, "WI", lat_v, lon_v, json.dumps(bbox), p.get("channel_id", ""), "public", hunt_db.now_iso()),
        )
        pid = cur_p.lastrowid
    cur = conn.execute(
        "INSERT INTO waypoints (property_id, name, kind, lat, lon, assigned_hunter, ideal_winds, notes, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(pid), p.get("name", "Stand"), p.get("kind", "stand"), float(p["lat"]), float(p["lon"]), p.get("assigned_hunter", ""), p.get("ideal_winds") or p.get("preferred_winds", ""), p.get("notes", ""), hunt_db.now_iso())
    )
    conn.commit()
    print(json.dumps({"status": "ok", "id": cur.lastrowid, "property_id": int(pid)}))
'
        ;;
    delete_waypoint)
        printf '%s' "$BODY" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db
p = json.loads(sys.stdin.read() or "{}")
conn = hunt_db.get_conn()
conn.execute("DELETE FROM waypoints WHERE id = ?", (int(p["id"]),))
conn.commit()
print(json.dumps({"status": "ok", "deleted": int(p["id"])}))
'
        ;;
    set_privacy)
        printf '%s' "$BODY" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db
p = json.loads(sys.stdin.read() or "{}")
conn = hunt_db.get_conn()
print(json.dumps(hunt_db.set_property_privacy(conn, int(p["property_id"]), p.get("visibility", "public"), p.get("channel_id", ""))))
'
        ;;
    weather)
        printf '%s' "$BODY" | "$PY" -c '
import json, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db, hunt_weather
p = json.loads(sys.stdin.read() or "{}")
lat, lon = p.get("lat"), p.get("lon")
if lat is None or lon is None:
    conn = hunt_db.get_conn()
    b = hunt_db.get_property_bundle(conn, property_id=p.get("property_id"))
    if not b:
        print(json.dumps({"status": "error", "message": "No property loaded"})); sys.exit(1)
    lat, lon = b["property"]["center_lat"], b["property"]["center_lon"]
try:
    res = hunt_weather.fetch_hunt_weather(float(lat), float(lon), int(p.get("days", 3)), bool(p.get("force_refresh", False)))
except Exception as exc:
    res = {"status": "error", "message": str(exc)}
print(json.dumps(res))
'
        ;;
    plan)
        printf '%s' "$BODY" | "$PY" "$MODULE_DIR/tools/hunt_plan.py"
        ;;
    render_map|post_discord_map)
        res=$(printf '%s' "$BODY" | "$PY" "$MODULE_DIR/tools/hunt_render_map.py")
        if [ "$ACTION" = "post_discord_map" ]; then
            fp=$(printf '%s' "$res" | jq -r '.png_path // empty')
            chan=$(printf '%s' "$BODY" | jq -r '.channel_id // empty')
            if [ -n "$fp" ] && [ -f "$fp" ] && [ -f "$LODGE_DIR/lib/comm/discord_bridge.sh" ]; then
                [ -z "$chan" ] && chan=$(jq -r '.channels[0] // empty' "$LODGE_DIR/.george/discord_config.json" 2>/dev/null || true)
                if [ -n "$chan" ]; then
                    bash -c "source '$LODGE_DIR/lib/comm/discord_bridge.sh' && discord_send_file '$chan' '$fp' 'Hunting Buddy Tactical Map Card'" >/dev/null 2>&1 || true
                fi
            fi
        fi
        printf '%s\n' "$res"
        ;;
    export_gpx)
        printf '%s' "$BODY" | "$PY" "$MODULE_DIR/tools/hunt_export.py"
        ;;
    fetch_viewport_gis)
        printf '%s' "$BODY" | "$PY" "$MODULE_DIR/tools/hunt_gis_cache.py"
        ;;
    fetch_dem)
        printf '%s' "$BODY" | jq '. + {"action":"fetch_dem"}' | "$PY" "$MODULE_DIR/tools/hunt_topo.py"
        ;;
    topo_summary)
        printf '%s' "$BODY" | jq '. + {"action":"summary"}' | "$PY" "$MODULE_DIR/tools/hunt_topo.py"
        ;;
    add_landmark)
        printf '%s' "$BODY" | jq '. + {"action":"add_landmark"}' | "$PY" "$MODULE_DIR/tools/hunt_topo.py"
        ;;
    delete_landmark)
        printf '%s' "$BODY" | jq '. + {"action":"delete_landmark"}' | "$PY" "$MODULE_DIR/tools/hunt_topo.py"
        ;;
    import_topo_dataset)
        printf '%s' "$BODY" | jq '. + {"action":"import_dataset"}' | "$PY" "$MODULE_DIR/tools/hunt_topo.py"
        ;;
    save_route)
        printf '%s' "$BODY" | jq '. + {"action":"save"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    list_routes)
        printf '%s' "$BODY" | jq '. + {"action":"list"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    reverse_route)
        printf '%s' "$BODY" | jq '. + {"action":"reverse"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    delete_route)
        printf '%s' "$BODY" | jq '. + {"action":"delete"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    sync_hydro_roads)
        printf '%s' "$BODY" | jq '. + {"action":"sync_hydro"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    providers_list)
        printf '%s' "$BODY" | jq '. + {"action":"providers_list"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    providers_health)
        printf '%s' "$BODY" | jq '. + {"action":"providers_health"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    provider_save)
        printf '%s' "$BODY" | jq '. + {"action":"provider_save"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    provider_delete)
        printf '%s' "$BODY" | jq '. + {"action":"provider_delete"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    offline_pack)
        printf '%s' "$BODY" | jq '. + {"action":"offline_pack"}' | "$PY" "$MODULE_DIR/tools/hunt_routes.py"
        ;;
    fish_analyze)
        printf '%s' "$BODY" | jq '. + {"action":"analyze"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    fish_gauges)
        printf '%s' "$BODY" | jq '. + {"action":"gauges"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    add_fish_structure)
        printf '%s' "$BODY" | jq '. + {"action":"add_structure"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    delete_fish_structure)
        printf '%s' "$BODY" | jq '. + {"action":"delete_structure"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    add_water_flow)
        printf '%s' "$BODY" | jq '. + {"action":"add_flow"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    delete_water_flow)
        printf '%s' "$BODY" | jq '. + {"action":"delete_flow"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    add_depth_point)
        printf '%s' "$BODY" | jq '. + {"action":"add_depth"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    delete_depth_point)
        printf '%s' "$BODY" | jq '. + {"action":"delete_depth"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    rebuild_bathymetry)
        printf '%s' "$BODY" | jq '. + {"action":"rebuild_bathymetry"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    import_depth_dataset)
        printf '%s' "$BODY" | jq '. + {"action":"import_depth"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    save_contour_line)
        printf '%s' "$BODY" | jq '. + {"action":"save_contour_line"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    sync_waterbodies)
        printf '%s' "$BODY" | jq '. + {"action":"sync_waterbodies"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    save_waterbody)
        printf '%s' "$BODY" | jq '. + {"action":"save_waterbody"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    delete_waterbody)
        printf '%s' "$BODY" | jq '. + {"action":"delete_waterbody"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    refresh_waterbody_dossier)
        printf '%s' "$BODY" | jq '. + {"action":"refresh_waterbody_dossier"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    get_waterbody_map)
        printf '%s' "$BODY" | jq '. + {"action":"get_waterbody_map"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    save_waterbody_overlay)
        printf '%s' "$BODY" | jq '. + {"action":"save_waterbody_overlay"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    unpin_waterbody_overlay)
        printf '%s' "$BODY" | jq '. + {"action":"unpin_waterbody_overlay"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    auto_align_chart)
        printf '%s' "$BODY" | jq '. + {"action":"auto_align_chart"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    prepare_chart_tiles)
        printf '%s' "$BODY" | jq '. + {"action":"prepare_chart_tiles"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    clear_bathymetry)
        printf '%s' "$BODY" | jq '. + {"action":"clear_bathymetry"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    extract_depths_from_chart)
        printf '%s' "$BODY" | jq '. + {"action":"extract_depths_from_chart"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    estimate_bathymetry)
        printf '%s' "$BODY" | jq '. + {"action":"estimate_bathymetry"}' | "$PY" "$MODULE_DIR/tools/hunt_fish.py"
        ;;
    list_plans)
        printf '%s' "$BODY" | jq '. + {"action":"list"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" plan
        ;;
    get_plan)
        printf '%s' "$BODY" | jq '. + {"action":"get"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" plan
        ;;
    save_plan)
        printf '%s' "$BODY" | jq '. + {"action":"save"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" plan
        ;;
    update_plan)
        printf '%s' "$BODY" | jq '. + {"action":"update"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" plan
        ;;
    save_plan_followup)
        printf '%s' "$BODY" | jq '. + {"action":"followup"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" plan
        ;;
    delete_plan)
        printf '%s' "$BODY" | jq '. + {"action":"delete"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" plan
        ;;
    list_annotations)
        printf '%s' "$BODY" | jq '. + {"action":"list"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" annotate
        ;;
    add_annotation)
        printf '%s' "$BODY" | jq '. + {"action":"add"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" annotate
        ;;
    toggle_annotation)
        printf '%s' "$BODY" | jq '. + {"action":"toggle"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" annotate
        ;;
    toggle_plan_annotations)
        printf '%s' "$BODY" | jq '. + {"action":"toggle_plan"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" annotate
        ;;
    delete_annotation)
        printf '%s' "$BODY" | jq '. + {"action":"delete"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" annotate
        ;;
    clear_annotations)
        printf '%s' "$BODY" | jq '. + {"action":"clear"}' | "$PY" "$MODULE_DIR/tools/hunt_plan_store.py" annotate
        ;;
    *)
        printf '{"status":"error","message":"Unknown action: %s"}\n' "$ACTION"
        exit 1
        ;;
esac
