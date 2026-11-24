#!/usr/bin/env bash
# hooks/context.sh — Injects compact active camp summary when hunting keywords match
set -euo pipefail
LODGE_DIR="${LODGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
MODULE_DIR="${MODULE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODULE_DATA_DIR="${MODULE_DATA_DIR:-$LODGE_DIR/.george/module_data/hunting-buddy}"
export MODULE_DIR MODULE_DATA_DIR

PY="$LODGE_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

"$PY" -c '
import sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import hunt_db
conn = hunt_db.get_conn()
props = hunt_db.list_properties(conn)
if not props:
    print("HUNTING BUDDY: No camps imported yet. Use hunt_import with lat/lon or GPX/KML/PDF.")
    sys.exit(0)
lines = ["HUNTING BUDDY ACTIVE CAMPS:"]
for p in props[:4]:
    b = hunt_db.get_property_bundle(conn, property_id=p["id"])
    wcnt = len(b["waypoints"]) if b else 0
    pcnt = len(b["parcels"]) if b else 0
    hcnt = len(b["hunters"]) if b else 0
    lmcnt = len(b.get("topo_landmarks") or []) if b else 0
    rtcnt = len(b.get("routes") or []) if b else 0
    fscnt = len(b.get("fish_structures") or []) if b else 0
    tinfo = ""
    if b and b.get("terrain") and b["terrain"].get("has_dem"):
        tr = b["terrain"]
        tinfo = f", DEM {tr.get(\"min_elevation_ft\")}–{tr.get(\"max_elevation_ft\")}ft ({tr.get(\"relief_ft\")}ft relief)"
    lines.append(f"- [{p[\"id\"]}] {p[\"name\"]} ({p[\"visibility\"]}) @ {p[\"center_lat\"]:.4f},{p[\"center_lon\"]:.4f} | {pcnt} parcels, {wcnt} pins, {rtcnt} traced streams/trails, {lmcnt} topo landmarks, {fscnt} fish structures{tinfo}, {hcnt} live GPS hunters")
lines.append("HUNTING BUDDY MAP RULE: Whenever asked to show, render, or post a hunting camp map image (including in Discord), ALWAYS call `hunt_render_map(property_id=<id>, basemap=\"hybrid\", publish_discord=true)` — NEVER draw a custom matplotlib or ASCII map from scratch.")
print("\n".join(lines))
' 2>/dev/null || true
