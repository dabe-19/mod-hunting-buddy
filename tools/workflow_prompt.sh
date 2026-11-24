#!/usr/bin/env bash
# tools/workflow_prompt.sh — Native Blue Lodge Monitored Workflow Prompt Builder for mod-hunting-buddy
# Usage: workflow_prompt.sh <workflow_name> <sandbox_dir> <params_json_path>
set -euo pipefail

WORKFLOW="${1:-}"
SANDBOX_DIR="${2:-}"
PARAMS_FILE="${3:-}"

python3 - "$WORKFLOW" "$SANDBOX_DIR" "$PARAMS_FILE" <<'PY'
import json, os, sys

workflow, sandbox_dir, params_path = sys.argv[1:4]
params = {}
if params_path and os.path.isfile(params_path):
    try:
        params = json.load(open(params_path, "r", encoding="utf-8"))
    except Exception:
        params = {}

images = params.get("images") or []
result_path = os.path.join(sandbox_dir, "result.json")

if workflow == "camp_tactical_briefing":
    camp = str(params.get("camp") or params.get("property_name") or "").strip()
    prop_id = params.get("property_id")
    wind_deg = params.get("wind_deg", 315)
    wind_mph = params.get("wind_mph", 8)
    thermal = str(params.get("thermal_mode") or "neutral")
    print(f"""Execute the Hunting Buddy Sovereign Camp Tactical Briefing workflow.

INPUTS:
- Camp / Property: {camp or '(active camp)'} (property_id: {prop_id})
- Current Wind Override: {wind_deg}° @ {wind_mph} mph (thermal: {thermal})
- Output artifact path: {result_path}

MANDATORY EXECUTION STEPS (use your native `+hunting` tools):
1. Batch `hunt_plan` (property_id={prop_id or 1}, wind_deg={wind_deg}, wind_mph={wind_mph}, thermal_mode="{thermal}") and `hunt_weather` (property_id={prop_id or 1}, days=2) in Turn 1 to evaluate every stand's scent cone, conflicts, 48-hour wind shifts, and Wisconsin DNR legal shooting light window.
2. Call `hunt_plan_save` (action="save", property_id={prop_id or 1}) to permanently archive this tactical hunting plan in Hunting Buddy's SQLite ledger AND place removable map annotations directly on the live Leaflet map! Include:
   - `title`, `wind_deg`, `wind_mph`, `thermal_mode`, `shooting_light`, `top_stands`, `blown_stands`, `briefing_markdown` (clear Markdown tactical briefing), AND
   - `annotations`: 2–5 removable tactical map objects derived from the real parcel/stand coordinates (e.g., `stand_pick` Point for top stand, `approach_route` LineString showing upwind stealth entry, `avoid_zone` Polygon or `bedding_zone` Polygon / `pinch_point` Point with concise `notes`).
3. Call `hunt_render_map` (property_id={prop_id or 1}, wind_deg={wind_deg}, wind_mph={wind_mph}, thermal_mode="{thermal}", publish_discord=false) so the static tactical map PNG also includes your newly saved annotations.
4. Write structured JSON to `{result_path}` via `file_write` with keys:
   `workflow`, `plan_id`, `camp_name`, `wind_summary`, `shooting_light`, `top_stands` (ranked list with safety rationale), `blown_stands` (list to avoid), `annotations` (the map objects you placed), `map_path`, and `briefing_markdown`.
5. Call `milestone_complete` and deliver a concise deer-camp tactical briefing suitable for hunters heading out before first light.""")

elif workflow == "map_image_analysis":
    if not images:
        sys.stderr.write("map_image_analysis requires at least 1 uploaded map or trail-cam photo\n")
        sys.exit(1)
    primary = images[0]
    camp = str(params.get("camp") or "Field Map Analysis").strip()
    prop_id = params.get("property_id")
    print(f"""Execute the Hunting Buddy Map & Trail-Cam Vision Analysis workflow.

INPUTS:
- Primary photo: {primary}
- Extra photos: {json.dumps(images[1:])}
- Camp / Property context: {camp} (property_id: {prop_id})
- Output artifact path: {result_path}

MANDATORY EXECUTION STEPS:
1. Call `vision_analyze` on `{primary}` to inspect the aerial/topo map screenshot, parcel lines, pinch points, timber edges, water/creeks, bedding cover, or trail-cam buck/deer activity.
2. If coordinates, parcel IDs, or stand locations are visible or requested for property_id={prop_id or 'none'}, cross-reference with `hunt_plan`, `hunt_weather`, or `hunt_import`.
3. Call `hunt_plan_save` (action="save", property_id={prop_id or 1}) to preserve your map analysis briefing AND attach removable GeoJSON map annotations (`stand_pick`, `approach_route`, `deer_corridor`, `bedding_zone`, `pinch_point`, `avoid_zone`) onto the live Leaflet map.
4. Write structured JSON to `{result_path}` via `file_write` with keys:
   `workflow`, `plan_id`, `camp_name`, `visual_findings`, `recommended_stand_spots` (list), `access_routes` (list), `annotations` (list), `wind_notes`, and `briefing_markdown`.
5. Call `milestone_complete` and deliver a crisp tactical summary of the map/photo analysis.""")

else:
    sys.stderr.write(f"Unknown hunting-buddy workflow: {workflow}\n")
    sys.exit(1)
PY
