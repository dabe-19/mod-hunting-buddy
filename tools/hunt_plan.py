#!/usr/bin/env python3
"""Multi-Hunter Stand Wind, Thermal & Terrain Scorer + Shapely Downwind Scent-Cone Conflict Detector."""

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.hunt_db import get_conn, get_property_bundle
from tools.hunt_topo import sample_point_terrain
from tools.hunt_weather import deg_to_compass

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_TO_LL = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _blend_angle_deg(primary_deg: float, secondary_deg: float, weight_secondary: float) -> float:
    w1 = 1.0 - weight_secondary
    w2 = weight_secondary
    x = w1 * math.sin(math.radians(primary_deg)) + w2 * math.sin(math.radians(secondary_deg))
    y = w1 * math.cos(math.radians(primary_deg)) + w2 * math.cos(math.radians(secondary_deg))
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def scent_cone_polygon_m(
    lat: float,
    lon: float,
    wind_from_deg: float,
    wind_mph: float = 8.0,
    thermal_mode: str = "neutral",
    length_yards: float = 280.0,
    terrain_pt: Optional[Dict[str, Any]] = None,
) -> Polygon:
    """Build a metric (EPSG:3857) Gaussian wedge scent cone downwind of (lat, lon),
    blending real terrain uphill/downhill thermal drainage when DEM slope is available."""
    cx, cy = _TO_M.transform(lon, lat)
    downwind_deg = (wind_from_deg + 180.0) % 360.0

    # Blend terrain slope thermal pull under light-to-moderate winds (< 12 mph)
    if terrain_pt and wind_mph < 12.0 and float(terrain_pt.get("slope_pct") or 0.0) >= 1.5:
        pull_weight = max(0.15, min(0.55, (12.0 - wind_mph) / 16.0))
        if thermal_mode == "katabatic_drainage" and terrain_pt.get("downhill_deg") is not None:
            downwind_deg = _blend_angle_deg(downwind_deg, float(terrain_pt["downhill_deg"]), pull_weight)
        elif thermal_mode == "upslope_warming" and terrain_pt.get("uphill_deg") is not None:
            downwind_deg = _blend_angle_deg(downwind_deg, float(terrain_pt["uphill_deg"]), pull_weight)

    half_angle_deg = 24.0 if wind_mph < 5.0 else (18.0 if wind_mph < 10.0 else 13.0)
    if thermal_mode == "katabatic_drainage":
        half_angle_deg += 6.0
        length_yards *= 1.15
    elif thermal_mode == "upslope_warming":
        half_angle_deg += 3.0
    length_m = length_yards * 0.9144

    pts = [(cx, cy)]
    steps = 12
    for i in range(steps + 1):
        bearing = downwind_deg - half_angle_deg + (2.0 * half_angle_deg * i / steps)
        rad = math.radians(bearing)
        px = cx + length_m * math.sin(rad)
        py = cy + length_m * math.cos(rad)
        pts.append((px, py))
    pts.append((cx, cy))
    return Polygon(pts)


def scent_cone_geojson(
    lat: float,
    lon: float,
    wind_from_deg: float,
    wind_mph: float = 8.0,
    thermal_mode: str = "neutral",
    length_yards: float = 280.0,
    terrain_pt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    poly_m = scent_cone_polygon_m(lat, lon, wind_from_deg, wind_mph, thermal_mode, length_yards, terrain_pt=terrain_pt)
    coords = []
    for x, y in poly_m.exterior.coords:
        lo, la = _TO_LL.transform(x, y)
        coords.append([round(lo, 6), round(la, 6)])
    return {"type": "Polygon", "coordinates": [coords]}


def _landmark_center(geom: Optional[Dict[str, Any]]) -> Optional[tuple]:
    if not geom or not geom.get("coordinates"):
        return None
    gt = geom.get("type")
    c = geom["coordinates"]
    if gt == "Point" and len(c) >= 2:
        return (float(c[1]), float(c[0]))
    if gt == "LineString" and len(c) >= 1:
        m = c[len(c) // 2]
        return (float(m[1]), float(m[0]))
    if gt == "Polygon" and c and c[0]:
        ring = c[0]
        return (sum(float(p[1]) for p in ring) / len(ring), sum(float(p[0]) for p in ring) / len(ring))
    return None


def evaluate_hunt_plan(
    property_id: Optional[int] = None,
    wind_deg: float = 315.0,
    wind_mph: float = 9.0,
    thermal_mode: str = "neutral",
    channel_id: str = "",
) -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id, channel_id=channel_id)
    if not bundle:
        return {"status": "error", "message": "No property loaded. Import coordinates or GIS data first."}

    pid = bundle["property"]["id"]
    wind_comp = deg_to_compass(wind_deg)
    scent_comp = deg_to_compass((wind_deg + 180.0) % 360.0)
    wps = bundle["waypoints"]
    stands = [w for w in wps if w["kind"] in ("stand", "blind")]
    sensitive = [w for w in wps if w["kind"] in ("bedding", "trail_cam", "funnel", "food_plot")]
    landmarks = bundle.get("topo_landmarks") or []
    routes = bundle.get("routes") or []
    terrain = bundle.get("terrain")

    # Pre-project traced routes (streams, trails, roads, deer trails) to metric LineStrings
    proj_routes = []
    for rt in routes:
        g = rt.get("geom") or {}
        if g.get("type") == "LineString" and len(g.get("coordinates") or []) >= 2:
            pts_m = [_TO_M.transform(float(c[0]), float(c[1])) for c in g["coordinates"]]
            try:
                ls = LineString(pts_m)
                # Compute overall downstream / travel bearing
                p0, p1 = g["coordinates"][0], g["coordinates"][-1]
                brg = (math.degrees(math.atan2(
                    math.sin(math.radians(p1[0] - p0[0])) * math.cos(math.radians(p1[1])),
                    math.cos(math.radians(p0[1])) * math.sin(math.radians(p1[1])) - math.sin(math.radians(p0[1])) * math.cos(math.radians(p1[1])) * math.cos(math.radians(p1[0] - p0[0]))
                )) + 360.0) % 360.0
                proj_routes.append({**rt, "_line": ls, "_bearing_deg": round(brg, 1)})
            except Exception:
                continue

    stand_scores = []
    cones = {}
    for s in stands:
        t_pt = sample_point_terrain(pid, float(s["lat"]), float(s["lon"]))
        sx0, sy0 = _TO_M.transform(float(s["lon"]), float(s["lat"]))
        stand_pt = Point(sx0, sy0)

        # Under evening/dawn katabatic drainage, align scent with nearby traced stream corridor if no strong slope DEM overrides
        eff_t_pt = dict(t_pt) if t_pt else None
        nearby_routes = []
        near_stream_hit = False
        near_deer_trail_hit = False
        for pr in proj_routes:
            dist_yd = stand_pt.distance(pr["_line"]) / 0.9144
            rk = (pr.get("kind") or "").lower()
            rname = pr.get("name") or rk.replace("_", " ").title()
            if dist_yd <= 160.0:
                nearby_routes.append(f"{rname} [{rk.replace('_', ' ')}] ({round(dist_yd)} yd)")
                if rk in ("stream", "creek") and dist_yd <= 130.0:
                    near_stream_hit = True
                    if thermal_mode == "katabatic_drainage" and eff_t_pt is None:
                        eff_t_pt = {
                            "elevation_ft": 0.0,
                            "slope_pct": 2.5,
                            "downhill_deg": pr["_bearing_deg"],
                            "downhill_compass": deg_to_compass(pr["_bearing_deg"]),
                            "uphill_deg": (pr["_bearing_deg"] + 180.0) % 360.0,
                            "uphill_compass": deg_to_compass((pr["_bearing_deg"] + 180.0) % 360.0),
                        }
                elif rk == "deer_trail" and dist_yd <= 100.0:
                    near_deer_trail_hit = True

        cone = scent_cone_polygon_m(s["lat"], s["lon"], wind_deg, wind_mph, thermal_mode, terrain_pt=eff_t_pt)
        cones[s["id"]] = cone
        ideal = [x.strip().upper() for x in (s.get("ideal_winds") or "").split(",") if x.strip()]
        bad = [x.strip().upper() for x in (s.get("bad_winds") or "").split(",") if x.strip()]
        score = 70
        reasons = []
        if wind_comp in ideal or any(wind_comp.startswith(iw) for iw in ideal):
            score += 25
            reasons.append(f"Matches ideal wind ({wind_comp})")
        if wind_comp in bad or any(wind_comp.startswith(bw) for bw in bad):
            score -= 45
            reasons.append(f"Matches BAD wind ({wind_comp})")
        for sens in sensitive:
            sx, sy = _TO_M.transform(sens["lon"], sens["lat"])
            if cone.contains(Point(sx, sy)):
                score -= 25
                reasons.append(f"Scent blows directly over {sens['kind']} '{sens['name']}'")

        # Evaluate traced streams, walking/entry paths, driving roads & deer trails
        for pr in proj_routes:
            dist_yd = stand_pt.distance(pr["_line"]) / 0.9144
            rk = (pr.get("kind") or "").lower()
            rname = pr.get("name") or rk.replace("_", " ").title()
            if rk in ("stream", "creek") and dist_yd <= 120.0:
                score += 6
                reasons.append(f"Stream corridor '{rname}' ({round(dist_yd)} yd, flows → {deg_to_compass(pr['_bearing_deg'])})")
            elif rk == "deer_trail" and dist_yd <= 90.0:
                score += 8
                reasons.append(f"Intercepts deer trail '{rname}' ({round(dist_yd)} yd)")
            elif rk in ("walking_trail", "entry_route", "entry") and dist_yd <= 100.0:
                score += 3
                reasons.append(f"Quiet foot access via '{rname}' ({round(dist_yd)} yd)")
            elif rk in ("driving_road", "atv_road", "road") and dist_yd <= 140.0:
                score -= 5
                reasons.append(f"Near road '{rname}' ({round(dist_yd)} yd) — monitor human/vehicle pressure")
            if rk == "deer_trail" and cone.intersects(pr["_line"]):
                score -= 10
                reasons.append(f"Scent cone crosses deer trail '{rname}'")
        if near_stream_hit and near_deer_trail_hit:
            score += 6
            reasons.append("Prime creek-crossing deer pinch funnel")

        # Evaluate topographic & habitat landmarks (water sources, saddles, ridges, drainages)
        nearby_lm = []
        for lm in landmarks:
            ctr = _landmark_center(lm.get("geom"))
            if not ctr:
                continue
            lx, ly = _TO_M.transform(ctr[1], ctr[0])
            dist_yd = math.hypot(lx - sx0, ly - sy0) / 0.9144
            if dist_yd <= 140.0 and lm["kind"] in ("water_source", "saddle", "bench", "food_plot"):
                nearby_lm.append(f"{lm['name']} ({round(dist_yd)} yd)")
                if lm["kind"] in ("saddle", "water_source"):
                    score += 8
                    reasons.append(f"Near {lm['kind'].replace('_', ' ')} '{lm['name']}' ({round(dist_yd)} yd)")
            if lm["kind"] in ("water_source", "drainage", "thick_cover", "food_plot") and cone.contains(Point(lx, ly)):
                if thermal_mode == "katabatic_drainage" and lm["kind"] in ("drainage", "water_source"):
                    score -= 12
                    reasons.append(f"Evening katabatic thermal drains into {lm['kind'].replace('_', ' ')} '{lm['name']}'")

        if t_pt:
            reasons.append(
                f"Elev {t_pt['elevation_ft']} ft · {t_pt['slope_pct']}% slope (downhill → {t_pt['downhill_compass']})"
            )

        stand_scores.append({
            "waypoint_id": s["id"],
            "name": s["name"],
            "assigned_hunter": s.get("assigned_hunter", ""),
            "score": max(0, min(100, score)),
            "verdict": "OPTIMAL" if score >= 80 else ("CAUTION" if score >= 50 else "BUSTED"),
            "reasons": reasons,
            "terrain": t_pt,
            "nearby_landmarks": nearby_lm[:4],
            "nearby_routes": nearby_routes[:5],
            "cone_geojson": scent_cone_geojson(s["lat"], s["lon"], wind_deg, wind_mph, thermal_mode, terrain_pt=eff_t_pt),
        })

    # Hunter-to-hunter downwind conflicts
    conflicts = []
    for a in stands:
        cone_a = cones[a["id"]]
        for b in stands:
            if a["id"] == b["id"]:
                continue
            bx, by = _TO_M.transform(b["lon"], b["lat"])
            if cone_a.contains(Point(bx, by)):
                conflicts.append({
                    "upwind_stand": a["name"],
                    "upwind_hunter": a.get("assigned_hunter", ""),
                    "downwind_stand": b["name"],
                    "downwind_hunter": b.get("assigned_hunter", ""),
                    "severity": "HIGH",
                    "detail": f"Scent from '{a['name']}' blows directly into '{b['name']}' on {wind_comp} {wind_mph} mph wind.",
                })

    stand_scores.sort(key=lambda x: -x["score"])
    return {
        "status": "ok",
        "property_id": pid,
        "property_name": bundle["property"]["name"],
        "wind": {"from_deg": wind_deg, "from_compass": wind_comp, "mph": wind_mph, "scent_to": scent_comp, "thermal_mode": thermal_mode},
        "has_dem": bool(terrain and terrain.get("has_dem")),
        "landmark_count": len(landmarks),
        "stand_scores": stand_scores,
        "conflicts": conflicts,
    }


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(evaluate_hunt_plan(**payload), indent=2))
