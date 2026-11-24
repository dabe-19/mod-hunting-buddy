#!/usr/bin/env python3
"""Linear Path & Stream Tracer, Public Hydrography/Road Sync & Offline Camp Pack Builder.

Manages:
  1. User-traced streams (with downstream flow direction), walking trails, ATV/driving roads,
     entry/exit routes, deer trails, and fishing current seams / drift lines.
  2. One-click public Hydrography (USGS NHD -> Global OSM Overpass), Trails/Roads (WI DNR ->
     Global OSM Overpass), and Fisheries overlays via the Pluggable Sovereign Provider Engine.
  3. Provider health/drift diagnostics, persistent user provider overrides, and Offline Camp Packs.
"""

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters.registry import (
    check_providers_health,
    delete_user_provider,
    fetch_vector_layer_cascade,
    list_providers_with_health,
    save_user_provider,
)
from tools.hunt_db import get_conn, get_property_bundle, now_iso, parse_json_stdin
from tools.hunt_weather import deg_to_compass

M_TO_YD = 1.0936132983


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * r * math.asin(min(1.0, math.sqrt(a)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compute_linestring_metrics(coords: List[List[float]]) -> Dict[str, Any]:
    """Compute geodesic length in yards and overall start->end bearing for GeoJSON [lon, lat] coords."""
    if not coords or len(coords) < 2:
        return {"length_yd": 0.0, "bearing_deg": 0.0, "bearing_compass": "N"}
    total_m = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = float(coords[i][0]), float(coords[i][1])
        lon2, lat2 = float(coords[i + 1][0]), float(coords[i + 1][1])
        total_m += _haversine_m(lat1, lon1, lat2, lon2)
    brg = _bearing_deg(float(coords[0][1]), float(coords[0][0]), float(coords[-1][1]), float(coords[-1][0]))
    return {
        "length_yd": round(total_m * M_TO_YD, 1),
        "bearing_deg": round(brg, 1),
        "bearing_compass": deg_to_compass(brg),
    }


def _normalize_coords(raw_coords: Any) -> List[List[float]]:
    if not isinstance(raw_coords, list):
        return []
    out: List[List[float]] = []
    for pt in raw_coords:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        a, b = float(pt[0]), float(pt[1])
        # Auto-fix [lat, lon] passed for Western Hemisphere
        if -90.0 <= a <= 90.0 and (b < -60.0 or b > 90.0) and a > 0:
            out.append([round(b, 6), round(a, 6)])
        else:
            out.append([round(a, 6), round(b, 6)])
    return out


def save_route(
    property_id: Optional[int],
    name: str,
    kind: str = "walking_trail",
    coordinates: Optional[List[List[float]]] = None,
    geom: Optional[Dict[str, Any]] = None,
    direction_mode: str = "",
    flow_speed: str = "",
    surface_type: str = "",
    notes: str = "",
    source: str = "user",
) -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return {"status": "error", "message": "No camp loaded. Create or select a camp first."}
    pid = int(bundle["property"]["id"])

    raw_c = coordinates
    if raw_c is None and isinstance(geom, dict):
        raw_c = geom.get("coordinates")
    coords = _normalize_coords(raw_c)
    if len(coords) < 2:
        return {"status": "error", "message": "A traced stream or path needs at least 2 points."}

    k = (kind or "walking_trail").strip().lower()
    if not direction_mode:
        direction_mode = "downstream" if k in ("stream", "creek", "current_seam", "drift_line") else (
            "one_way" if k in ("entry_route", "exit_route") else "bidirectional"
        )
    if not surface_type:
        surface_type = "water" if k in ("stream", "creek", "current_seam", "drift_line") else (
            "gravel" if k == "driving_road" else "dirt"
        )

    metrics = compute_linestring_metrics(coords)
    gj = {"type": "LineString", "coordinates": coords}
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO routes (property_id, name, kind, geom_geojson, notes, direction_mode, flow_speed, surface_type, length_yd, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            (name or k.replace("_", " ").title()).strip()[:120],
            k,
            json.dumps(gj),
            str(notes or "").strip()[:300],
            direction_mode,
            str(flow_speed or "").strip(),
            surface_type,
            metrics["length_yd"],
            source or "user",
            ts,
        ),
    )
    conn.commit()
    return {
        "status": "ok",
        "id": cur.lastrowid,
        "property_id": pid,
        "kind": k,
        "direction_mode": direction_mode,
        "length_yd": metrics["length_yd"],
        "bearing_deg": metrics["bearing_deg"],
        "bearing_compass": metrics["bearing_compass"],
    }


def list_routes(property_id: Optional[int] = None, kind: Optional[str] = None) -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return {"status": "ok", "property_id": None, "routes": []}
    pid = int(bundle["property"]["id"])
    rows = bundle.get("routes") or []
    out = []
    for r in rows:
        if kind and r.get("kind") != kind:
            continue
        coords = (r.get("geom") or {}).get("coordinates") or []
        m = compute_linestring_metrics(coords)
        item = dict(r)
        item["length_yd"] = item.get("length_yd") or m["length_yd"]
        item["bearing_deg"] = m["bearing_deg"]
        item["bearing_compass"] = m["bearing_compass"]
        out.append(item)
    return {"status": "ok", "property_id": pid, "routes": out}


def reverse_route(route_id: int) -> Dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT id, geom_geojson, kind FROM routes WHERE id = ?", (int(route_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Route {route_id} not found"}
    gj = json.loads(row["geom_geojson"])
    coords = gj.get("coordinates") or []
    coords.reverse()
    gj["coordinates"] = coords
    m = compute_linestring_metrics(coords)
    conn.execute("UPDATE routes SET geom_geojson = ? WHERE id = ?", (json.dumps(gj), int(route_id)))
    conn.commit()
    return {
        "status": "ok",
        "id": int(route_id),
        "bearing_deg": m["bearing_deg"],
        "bearing_compass": m["bearing_compass"],
    }


def delete_route(route_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM routes WHERE id = ?", (int(route_id),))
    conn.commit()
    return {"status": "ok", "deleted": int(route_id)}


def _resolve_bbox(
    property_id: Optional[int],
    min_lon: Optional[float],
    min_lat: Optional[float],
    max_lon: Optional[float],
    max_lat: Optional[float],
) -> Tuple[Optional[int], Tuple[float, float, float, float]]:
    if None not in (min_lon, min_lat, max_lon, max_lat):
        # Clamp oversized viewports gently so public Overpass/NHD APIs stay fast and polite
        mlo, mla, xlo, xla = float(min_lon), float(min_lat), float(max_lon), float(max_lat)
        if (xla - mla) > 0.08:
            mid = (mla + xla) * 0.5
            mla, xla = mid - 0.04, mid + 0.04
        if (xlo - mlo) > 0.10:
            mid = (mlo + xlo) * 0.5
            mlo, xlo = mid - 0.05, mid + 0.05
        return property_id, (mlo, mla, xlo, xla)

    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        raise ValueError("Provide a bounding box or load a camp first.")
    p = bundle["property"]
    la, lo = float(p["center_lat"]), float(p["center_lon"])
    return int(p["id"]), (lo - 0.018, la - 0.014, lo + 0.018, la + 0.014)


def sync_public_hydro_and_roads(
    property_id: Optional[int] = None,
    min_lon: Optional[float] = None,
    min_lat: Optional[float] = None,
    max_lon: Optional[float] = None,
    max_lat: Optional[float] = None,
    include_streams: bool = True,
    include_roads: bool = True,
    include_fisheries: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Pull & cache nationwide/global streams (USGS NHD -> OSM), trails/roads (WI DNR -> OSM) & fisheries."""
    try:
        pid, bbox = _resolve_bbox(property_id, min_lon, min_lat, max_lon, max_lat)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    streams: List[Dict[str, Any]] = []
    roads: List[Dict[str, Any]] = []
    fisheries: List[Dict[str, Any]] = []
    providers_used: List[str] = []
    offline_fallbacks: List[str] = []
    warnings: List[str] = []

    if include_streams:
        res_s = fetch_vector_layer_cascade("hydro_streams", bbox, force_refresh=force_refresh, merge_all_matching=False)
        streams = res_s.get("features") or []
        providers_used.extend(res_s.get("providers_used") or [])
        if res_s.get("offline_fallback"):
            offline_fallbacks.append("hydro_streams")
        if res_s.get("warnings"):
            warnings.extend(res_s["warnings"])

    if include_roads:
        res_r = fetch_vector_layer_cascade("roads_trails", bbox, force_refresh=force_refresh, merge_all_matching=True)
        roads = res_r.get("features") or []
        providers_used.extend(res_r.get("providers_used") or [])
        if res_r.get("offline_fallback"):
            offline_fallbacks.append("roads_trails")
        if res_r.get("warnings"):
            warnings.extend(res_r["warnings"])

    if include_fisheries:
        res_f = fetch_vector_layer_cascade("fisheries", bbox, force_refresh=force_refresh, merge_all_matching=True)
        fisheries = res_f.get("features") or []
        providers_used.extend(res_f.get("providers_used") or [])
        if res_f.get("offline_fallback"):
            offline_fallbacks.append("fisheries")

    return {
        "status": "ok",
        "property_id": pid,
        "bbox": list(bbox),
        "streams": streams,
        "roads": roads,
        "fisheries": fisheries,
        "counts": {
            "streams": len(streams),
            "roads": len(roads),
            "fisheries": len(fisheries),
        },
        "providers_used": sorted(set(providers_used)),
        "offline_fallbacks": offline_fallbacks,
        "warnings": warnings[:6],
    }


def build_offline_camp_pack(property_id: Optional[int] = None) -> Dict[str, Any]:
    """Pre-cache satellite tiles (z13-z15), LiDAR DEM, streams, roads, fisheries, parcels & weather locally."""
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return {"status": "error", "message": "No camp loaded. Create or select a camp first."}
    prop = bundle["property"]
    pid = int(prop["id"])
    la, lo = float(prop["center_lat"]), float(prop["center_lon"])
    bbox = (lo - 0.014, la - 0.010, lo + 0.014, la + 0.010)

    summary: Dict[str, Any] = {"property_id": pid, "camp_name": prop["name"], "cached": {}}

    # 1. LiDAR / DEM Elevation
    try:
        from tools.hunt_topo import fetch_property_dem
        dem = fetch_property_dem(pid, grid_n=64)
        summary["cached"]["dem"] = bool(dem.get("has_dem"))
    except Exception as exc:
        summary["cached"]["dem_error"] = str(exc)

    # 2. Public Streams, Roads & Fisheries
    try:
        hr = sync_public_hydro_and_roads(pid, bbox[0], bbox[1], bbox[2], bbox[3], True, True, True, force_refresh=False)
        summary["cached"]["streams"] = hr.get("counts", {}).get("streams", 0)
        summary["cached"]["roads"] = hr.get("counts", {}).get("roads", 0)
        summary["cached"]["fisheries"] = hr.get("counts", {}).get("fisheries", 0)
    except Exception as exc:
        summary["cached"]["hydro_error"] = str(exc)

    # 2b. Sync & Cache Public Waterbodies (Lakes/Flowages) + Official DNR Dossiers & Contour Maps
    try:
        from tools.hunt_fish import sync_waterbodies
        wb_res = sync_waterbodies(pid, bbox[0], bbox[1], bbox[2], bbox[3], fetch_dossiers=True, download_pdfs=True, force_refresh=False)
        summary["cached"]["waterbodies"] = wb_res.get("synced_count", 0)
        summary["cached"]["lake_maps_cached"] = wb_res.get("maps_cached", 0)
    except Exception as exc:
        summary["cached"]["waterbodies_error"] = str(exc)

    # 3. Parcels & Public Lands
    try:
        from tools.hunt_gis_cache import fetch_viewport_gis
        gis = fetch_viewport_gis(bbox[0], bbox[1], bbox[2], bbox[3], property_id=pid)
        summary["cached"]["parcels"] = len(gis.get("parcels") or [])
        summary["cached"]["public_lands"] = len(gis.get("public_lands") or [])
    except Exception as exc:
        summary["cached"]["gis_error"] = str(exc)

    # 4. Pre-cache Esri Imagery + Labels tiles (z13..z15) for camp
    tiles_ok = 0
    try:
        from tools.hunt_render_map import _deg2tile, _load_or_fetch_tile
        for z in (13, 14, 15):
            x0, y1 = _deg2tile(bbox[1], bbox[0], z)
            x1, y0 = _deg2tile(bbox[3], bbox[2], z)
            if (x1 - x0 + 1) * (y1 - y0 + 1) > 25:
                continue
            for tx in range(x0, x1 + 1):
                for ty in range(y0, y1 + 1):
                    if _load_or_fetch_tile("esri_imagery", z, tx, ty, mode="RGB") is not None:
                        tiles_ok += 1
                    _load_or_fetch_tile("esri_labels", z, tx, ty, mode="RGBA")
    except Exception:
        pass
    summary["cached"]["tiles_warmed"] = tiles_ok

    info = list_providers_with_health()
    summary["offline_cache"] = info.get("offline_cache", {})
    summary["status"] = "ok"
    summary["packed_at"] = now_iso()
    return summary


if __name__ == "__main__":
    p = parse_json_stdin()
    act = str(p.get("action") or "list").strip().lower()
    if act == "save":
        out = save_route(
            property_id=p.get("property_id"),
            name=p.get("name", ""),
            kind=p.get("kind", "walking_trail"),
            coordinates=p.get("coordinates"),
            geom=p.get("geom"),
            direction_mode=p.get("direction_mode", ""),
            flow_speed=p.get("flow_speed", ""),
            surface_type=p.get("surface_type", ""),
            notes=p.get("notes", ""),
            source=p.get("source", "user"),
        )
    elif act == "reverse":
        out = reverse_route(int(p["id"]))
    elif act == "delete":
        out = delete_route(int(p["id"]))
    elif act == "sync_hydro":
        out = sync_public_hydro_and_roads(
            property_id=p.get("property_id"),
            min_lon=p.get("min_lon"),
            min_lat=p.get("min_lat"),
            max_lon=p.get("max_lon"),
            max_lat=p.get("max_lat"),
            include_streams=bool(p.get("include_streams", True)),
            include_roads=bool(p.get("include_roads", True)),
            include_fisheries=bool(p.get("include_fisheries", True)),
            force_refresh=bool(p.get("force_refresh", False)),
        )
    elif act == "providers_list":
        out = list_providers_with_health()
    elif act == "providers_health":
        out = check_providers_health(layer=p.get("layer"))
    elif act == "provider_save":
        out = save_user_provider(p.get("provider") or p)
    elif act == "provider_delete":
        out = delete_user_provider(str(p.get("provider_id") or p.get("id") or ""))
    elif act == "offline_pack":
        out = build_offline_camp_pack(property_id=p.get("property_id"))
    else:
        out = list_routes(property_id=p.get("property_id"), kind=p.get("kind"))
    print(json.dumps(out, indent=2))
