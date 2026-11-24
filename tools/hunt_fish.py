#!/usr/bin/env python3
"""Fishing Structure, Current Flow, Live River Gauges, Bathymetry & Solunar Bite Engine.

Dogfoods the Pluggable Sovereign Spatial Provider Engine (`adapters/registry.py`) for:
  - Live USGS NWIS stream gauges (CFS, stage ft, water temp F) with Global Open-Meteo Flood fallback
  - Underwater bathymetry interpolation from interactive depth soundings or sonar CSV/GPX/GeoJSON uploads
  - Directional water current flow vectors (river current, inflow/outflow, wind drift, eddies)
  - Fish structure inventory (deep holes, drop-offs, weed edges, rock piles, timber, riffle/pools, launches)
  - Guide-grade Solunar + Barometric + Wind-Shoreline Bite Index & Species Presentation Matrix
"""

import base64
import csv
import io
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters.registry import (
    fetch_stream_gauges_cascade,
    fetch_vector_layer_cascade,
    fetch_wi_dnr_lake_dossier,
)
from tools.hunt_db import get_conn, get_property_bundle, now_iso, parse_json_stdin
from tools.hunt_weather import deg_to_compass, fetch_hunt_weather

# Bathymetric underwater depth ramp (shallow shoal amber -> weedline teal -> breakline blue -> deep hole indigo)
_BATHY_STOPS: List[Tuple[float, Tuple[int, int, int]]] = [
    (0.00, (253, 230, 138)),  # #fde68a 0-3 ft shoal
    (0.25, (45, 212, 191)),   # #2dd4bf 4-8 ft weedline
    (0.55, (14, 165, 233)),   # #0ea5e9 9-16 ft drop-off / breakline
    (0.80, (37, 99, 235)),    # #2563eb 17-25 ft basin
    (1.00, (79, 70, 229)),    # #4f46e5 deep hole indigo
]


def depth_to_color(norm: float) -> str:
    t = max(0.0, min(1.0, float(norm)))
    for idx in range(len(_BATHY_STOPS) - 1):
        t0, c0 = _BATHY_STOPS[idx]
        t1, c1 = _BATHY_STOPS[idx + 1]
        if t <= t1:
            f = (t - t0) / max(1e-6, t1 - t0)
            r = int(round(c0[0] + (c1[0] - c0[0]) * f))
            g = int(round(c0[1] + (c1[1] - c0[1]) * f))
            b = int(round(c0[2] + (c1[2] - c0[2]) * f))
            return f"#{r:02x}{g:02x}{b:02x}"
    c = _BATHY_STOPS[-1][1]
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def _resolve_pid(property_id: Optional[int]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return None, None
    return int(bundle["property"]["id"]), bundle


def add_fish_structure(
    property_id: Optional[int],
    name: str,
    kind: str,
    lat: float,
    lon: float,
    depth_ft: Optional[float] = None,
    bottom_type: str = "",
    target_species: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    pid, _ = _resolve_pid(property_id)
    if pid is None:
        return {"status": "error", "message": "No camp loaded. Create or select a camp first."}
    conn = get_conn()
    d_val = float(depth_ft) if depth_ft not in (None, "") else None
    cur = conn.execute(
        """INSERT INTO fish_structures (property_id, name, kind, lat, lon, depth_ft, bottom_type, target_species, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            (name or kind.replace("_", " ").title()).strip()[:120],
            (kind or "drop_off").strip().lower(),
            float(lat),
            float(lon),
            d_val,
            str(bottom_type or "").strip()[:60],
            str(target_species or "").strip()[:120],
            str(notes or "").strip()[:300],
            now_iso(),
        ),
    )
    conn.commit()
    return {"status": "ok", "id": cur.lastrowid, "property_id": pid}


def delete_fish_structure(structure_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM fish_structures WHERE id = ?", (int(structure_id),))
    conn.commit()
    return {"status": "ok", "deleted": int(structure_id)}


def add_water_flow(
    property_id: Optional[int],
    label: str,
    lat: float,
    lon: float,
    flow_to_deg: float,
    speed_class: str = "moderate",
    flow_type: str = "river_current",
    notes: str = "",
) -> Dict[str, Any]:
    pid, _ = _resolve_pid(property_id)
    if pid is None:
        return {"status": "error", "message": "No camp loaded."}
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO water_flows (property_id, label, lat, lon, flow_to_deg, speed_class, flow_type, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            (label or f"{flow_type.replace('_', ' ').title()} → {deg_to_compass(float(flow_to_deg))}").strip()[:120],
            float(lat),
            float(lon),
            float(flow_to_deg) % 360.0,
            (speed_class or "moderate").strip().lower(),
            (flow_type or "river_current").strip().lower(),
            str(notes or "").strip()[:240],
            now_iso(),
        ),
    )
    conn.commit()
    return {"status": "ok", "id": cur.lastrowid, "property_id": pid, "flow_compass": deg_to_compass(float(flow_to_deg))}


def delete_water_flow(flow_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM water_flows WHERE id = ?", (int(flow_id),))
    conn.commit()
    return {"status": "ok", "deleted": int(flow_id)}


def add_depth_point(
    property_id: Optional[int],
    lat: float,
    lon: float,
    depth_ft: float,
    waterbody_name: str = "Camp Water",
    source: str = "user",
) -> Dict[str, Any]:
    pid, _ = _resolve_pid(property_id)
    if pid is None:
        return {"status": "error", "message": "No camp loaded."}
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (pid, (waterbody_name or "Camp Water").strip()[:100], float(lat), float(lon), max(0.0, float(depth_ft)), source, now_iso()),
    )
    conn.commit()
    cnt = conn.execute("SELECT COUNT(*) FROM bathymetry_points WHERE property_id = ?", (pid,)).fetchone()[0]
    return {"status": "ok", "id": cur.lastrowid, "property_id": pid, "total_soundings": int(cnt)}


def delete_depth_point(point_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM bathymetry_points WHERE id = ?", (int(point_id),))
    conn.commit()
    return {"status": "ok", "deleted": int(point_id)}


def _extract_polygon_rings(geom: Optional[Dict[str, Any]]) -> List[List[List[float]]]:
    if not isinstance(geom, dict):
        return []
    gt = geom.get("type")
    coords = geom.get("coordinates") or []
    if gt == "Polygon" and coords:
        return [coords[0]]
    if gt == "MultiPolygon" and coords:
        return [poly[0] for poly in coords if poly and poly[0]]
    return []


def _approx_polygon_acres(geom: Optional[Dict[str, Any]]) -> float:
    rings = _extract_polygon_rings(geom)
    total_m2 = 0.0
    r_earth = 6378137.0
    for ring in rings:
        if len(ring) < 3:
            continue
        lat0 = sum(float(c[1]) for c in ring) / len(ring)
        cos0 = math.cos(math.radians(lat0))
        area = 0.0
        for i in range(len(ring)):
            c1 = ring[i]
            c2 = ring[(i + 1) % len(ring)]
            x1 = math.radians(float(c1[0])) * r_earth * cos0
            y1 = math.radians(float(c1[1])) * r_earth
            x2 = math.radians(float(c2[0])) * r_earth * cos0
            y2 = math.radians(float(c2[1])) * r_earth
            area += (x1 * y2 - x2 * y1)
        total_m2 += abs(area) * 0.5
    return round(total_m2 / 4046.8564224, 2)


def _sample_ring_shoreline(ring: List[List[float]], max_pts: int = 120) -> List[Tuple[float, float]]:
    if not ring or len(ring) < 3:
        return []
    pts: List[Tuple[float, float]] = [(float(c[0]), float(c[1])) for c in ring if len(c) >= 2]
    if len(pts) <= max_pts:
        return pts
    step = max(1, len(pts) // max_pts)
    return pts[::step]


def save_contour_line(
    property_id: Optional[int],
    coordinates: List[List[float]],
    depth_ft: float,
    waterbody_name: str = "Camp Water",
) -> Dict[str, Any]:
    """Densify a user-traced underwater depth contour polyline into soundings and rebuild bathymetry."""
    pid, _ = _resolve_pid(property_id)
    if pid is None:
        return {"status": "error", "message": "No camp loaded."}
    if not coordinates or len(coordinates) < 2:
        return {"status": "error", "message": "Depth contour trace requires at least 2 points."}
    d_val = max(0.0, float(depth_ft))
    wb = (waterbody_name or "Camp Water").strip()[:100]
    conn = get_conn()
    ts = now_iso()
    added = 0
    for i in range(len(coordinates) - 1):
        lon1, lat1 = float(coordinates[i][0]), float(coordinates[i][1])
        lon2, lat2 = float(coordinates[i + 1][0]), float(coordinates[i + 1][1])
        seg_deg = math.hypot(lon2 - lon1, lat2 - lat1)
        steps = max(1, min(14, int(seg_deg / 0.00035) + 1))
        for s in range(steps):
            frac = s / float(steps)
            la = lat1 + (lat2 - lat1) * frac
            lo = lon1 + (lon2 - lon1) * frac
            conn.execute(
                "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, 'contour_trace', ?)",
                (pid, wb, round(la, 6), round(lo, 6), round(d_val, 2), ts),
            )
            added += 1
    # Final endpoint
    conn.execute(
        "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, 'contour_trace', ?)",
        (pid, wb, round(float(coordinates[-1][1]), 6), round(float(coordinates[-1][0]), 6), round(d_val, 2), ts),
    )
    added += 1
    conn.commit()
    res = rebuild_bathymetry(pid, wb)
    res["contour_points_added"] = added
    return res


def _find_matching_waterbody(bundle: Dict[str, Any], wb_name: Optional[str], pts_lon: List[float], pts_lat: List[float]) -> Optional[Dict[str, Any]]:
    wbs = bundle.get("waterbodies") or []
    if not wbs:
        return None
    if wb_name:
        low_target = wb_name.strip().lower()
        for wb in wbs:
            if (wb.get("name") or "").strip().lower() == low_target and wb.get("geom"):
                return wb
    # Fallback: check which waterbody polygon contains any sounding point
    try:
        from matplotlib.path import Path as MplPath
        if pts_lon and pts_lat:
            test_pts = np.column_stack([pts_lon, pts_lat])
            for wb in wbs:
                rings = _extract_polygon_rings(wb.get("geom"))
                for ring in rings:
                    if len(ring) >= 3 and np.any(MplPath(np.array([[c[0], c[1]] for c in ring])).contains_points(test_pts)):
                        return wb
    except Exception:
        pass
    return None


def rebuild_bathymetry(property_id: Optional[int], waterbody_name: Optional[str] = None) -> Dict[str, Any]:
    """Interpolate underwater bathymetry contours (shoreline = 0 ft & polygon-clipped when lake geometry exists)."""
    pid, bundle = _resolve_pid(property_id)
    if pid is None or not bundle:
        return {"status": "error", "message": "No camp loaded."}
    conn = get_conn()
    if waterbody_name:
        rows = conn.execute("SELECT lat, lon, depth_ft, waterbody_name FROM bathymetry_points WHERE property_id = ? AND waterbody_name = ?", (pid, waterbody_name)).fetchall()
    else:
        rows = conn.execute("SELECT lat, lon, depth_ft, waterbody_name FROM bathymetry_points WHERE property_id = ?", (pid,)).fetchall()

    if not rows:
        return {"status": "error", "message": "No depth soundings recorded yet. Drop depth points or trace a depth contour first."}

    wb_name = waterbody_name or rows[0]["waterbody_name"] or "Camp Water"
    pts_lat = [float(r["lat"]) for r in rows]
    pts_lon = [float(r["lon"]) for r in rows]
    pts_d = [float(r["depth_ft"]) for r in rows]

    matched_wb = _find_matching_waterbody(bundle, wb_name, pts_lon, pts_lat)
    rings = _extract_polygon_rings(matched_wb.get("geom")) if matched_wb else []

    if not rings and len(rows) < 4:
        return {"status": "error", "message": f"Need at least 4 depth soundings (or a synced/drawn lake shoreline + 1 sounding) to generate contours (currently {len(rows)})."}

    interp_lon = list(pts_lon)
    interp_lat = list(pts_lat)
    interp_d = list(pts_d)
    shoreline_seeded = False

    if rings:
        # Seed lake shoreline as 0.0 ft depth boundary so depth tapers naturally to shore
        for ring in rings:
            for slo, sla in _sample_ring_shoreline(ring, max_pts=110):
                interp_lon.append(slo)
                interp_lat.append(sla)
                interp_d.append(0.0)
        shoreline_seeded = True

    from scipy.interpolate import griddata
    grid_n = 44 if rings else 28
    min_la, max_la = min(interp_lat), max(interp_lat)
    min_lo, max_lo = min(interp_lon), max(interp_lon)
    # Clamp interpolation window gently around soundings on very large reservoirs so contours stay crisp
    if rings and (max_la - min_la) > 0.04:
        pad_la = max(0.004, (max(pts_lat) - min(pts_lat)) * 0.65)
        pad_lo = max(0.005, (max(pts_lon) - min(pts_lon)) * 0.65)
        min_la = max(min_la, min(pts_lat) - pad_la)
        max_la = min(max_la, max(pts_lat) + pad_la)
        min_lo = max(min_lo, min(pts_lon) - pad_lo)
        max_lo = min(max_lo, max(pts_lon) + pad_lo)

    lats_1d = list(np.linspace(min_la, max_la, grid_n))
    lons_1d = list(np.linspace(min_lo, max_lo, grid_n))
    glon, glat = np.meshgrid(lons_1d, lats_1d)
    z_lin = griddata((interp_lon, interp_lat), interp_d, (glon, glat), method="linear")
    z_near = griddata((interp_lon, interp_lat), interp_d, (glon, glat), method="nearest")
    z_grid = np.where(np.isnan(z_lin), z_near, z_lin)
    z_grid = np.clip(z_grid, 0.0, max(pts_d) * 1.05)

    # Strictly mask/clip grid cells outside the lake shoreline polygon so contours NEVER bleed onto dry land
    if rings:
        from matplotlib.path import Path as MplPath
        grid_pts = np.column_stack([glon.ravel(), glat.ravel()])
        inside_mask = np.zeros(grid_pts.shape[0], dtype=bool)
        for ring in rings:
            if len(ring) >= 3:
                inside_mask |= MplPath(np.array([[c[0], c[1]] for c in ring])).contains_points(grid_pts)
        inside_2d = inside_mask.reshape(z_grid.shape)
        if np.any(inside_2d):
            z_grid = np.where(inside_2d, z_grid, np.nan)

    max_d = float(np.max(pts_d))
    avg_d = float(np.mean(pts_d))
    step_ft = 2.0 if max_d <= 15.0 else (4.0 if max_d <= 35.0 else 5.0)
    levels = np.arange(step_ft, max_d, step_ft)
    contours_fc: Dict[str, Any] = {"type": "FeatureCollection", "features": []}

    if len(levels) >= 1:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        cs = ax.contour(glon, glat, z_grid, levels=levels)
        allsegs = getattr(cs, "allsegs", None)
        if allsegs is not None:
            for lvl_val, seg_list in zip(cs.levels, allsegs):
                dv = round(float(lvl_val), 1)
                norm = dv / max(1.0, max_d)
                col = depth_to_color(norm)
                for seg in seg_list:
                    if len(seg) < 2:
                        continue
                    coords = [[round(float(pt[0]), 6), round(float(pt[1]), 6)] for pt in seg]
                    contours_fc["features"].append({
                        "type": "Feature",
                        "properties": {
                            "depth_ft": int(round(dv)) if abs(dv - round(dv)) < 0.05 else dv,
                            "depth_norm": round(norm, 3),
                            "color": col,
                            "kind": "bathymetry_contour",
                        },
                        "geometry": {"type": "LineString", "coordinates": coords},
                    })
        plt.close(fig)

    conn.execute("DELETE FROM bathymetry_grids WHERE property_id = ? AND waterbody_name = ?", (pid, wb_name))
    conn.execute(
        """INSERT INTO bathymetry_grids (property_id, waterbody_name, source, max_depth_ft, avg_depth_ft, contours_geojson, grid_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            wb_name,
            "shoreline_clipped_soundings" if shoreline_seeded else "interpolated_soundings",
            round(max_d, 1),
            round(avg_d, 1),
            json.dumps(contours_fc),
            json.dumps({"rows": grid_n, "cols": grid_n, "shoreline_clipped": shoreline_seeded}),
            now_iso(),
        ),
    )
    conn.commit()
    return {
        "status": "ok",
        "property_id": pid,
        "waterbody_name": wb_name,
        "soundings_used": len(rows),
        "shoreline_clipped": shoreline_seeded,
        "max_depth_ft": round(max_d, 1),
        "avg_depth_ft": round(avg_d, 1),
        "contour_count": len(contours_fc["features"]),
        "contours": contours_fc,
    }


# ── Persistent Cached & User-Editable Lakes / Waterbodies Database (v2.2.0) ───

def sync_waterbodies(
    property_id: Optional[int] = None,
    min_lon: Optional[float] = None,
    min_lat: Optional[float] = None,
    max_lon: Optional[float] = None,
    max_lat: Optional[float] = None,
    fetch_dossiers: bool = True,
    download_pdfs: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Pull & persist lakes/reservoirs/ponds (WI DNR 24K -> USGS NHD -> OSM) + WI DNR dossiers & contour PDFs."""
    pid, bundle = _resolve_pid(property_id)
    if pid is None or not bundle:
        return {"status": "error", "message": "No camp loaded. Create or select a camp first."}

    prop = bundle["property"]
    if None not in (min_lon, min_lat, max_lon, max_lat):
        mlo, mla, xlo, xla = float(min_lon), float(min_lat), float(max_lon), float(max_lat)
        if (xla - mla) > 0.08:
            mid = (mla + xla) * 0.5
            mla, xla = mid - 0.04, mid + 0.04
        if (xlo - mlo) > 0.10:
            mid = (mlo + xlo) * 0.5
            mlo, xlo = mid - 0.05, mid + 0.05
        bbox = (mlo, mla, xlo, xla)
    else:
        la, lo = float(prop["center_lat"]), float(prop["center_lon"])
        bbox = (lo - 0.018, la - 0.014, lo + 0.018, la + 0.014)

    res = fetch_vector_layer_cascade("hydro_waterbodies", bbox, force_refresh=force_refresh, merge_all_matching=False)
    feats = res.get("features") or []

    # Group multi-part polygons by WBIC (preferred) or name
    grouped: Dict[str, Dict[str, Any]] = {}
    for f in feats:
        geom = f.get("geom") or {}
        rings = _extract_polygon_rings(geom)
        if not rings:
            continue
        wbic_raw = str(f.get("wbic") or "").strip()
        wbic = wbic_raw if wbic_raw and wbic_raw != "0" else ""
        nm = str(f.get("name") or "").strip()
        if not nm or nm.lower() == "unnamed":
            if wbic:
                nm = f"Unnamed Waterbody (WBIC {wbic})"
            else:
                continue
        gkey = f"wbic:{wbic}" if wbic else f"name:{nm.lower()}"
        ent = grouped.get(gkey)
        if not ent:
            wtype = "reservoir" if "reservoir" in nm.lower() else ("flowage" if "flowage" in nm.lower() else ("pond" if "pond" in nm.lower() else "lake"))
            acres_est = None
            if f.get("area_sqkm"):
                try:
                    acres_est = round(float(f["area_sqkm"]) * 247.105, 1)
                except Exception:
                    acres_est = None
            ent = {
                "name": nm,
                "wbic": wbic,
                "waterbody_type": f.get("waterbody_type") or wtype,
                "river_system": str(f.get("river_system") or "").strip(),
                "acres": acres_est,
                "source": f.get("provider_id") or "public_gis",
                "rings": [],
            }
            grouped[gkey] = ent
        ent["rings"].extend(rings)

    conn = get_conn()
    ts = now_iso()
    synced = 0
    dossiers_loaded = 0
    maps_cached = 0

    for ent in list(grouped.values())[:25]:
        rings = ent["rings"]
        geom_obj = {"type": "Polygon", "coordinates": [rings[0]]} if len(rings) == 1 else {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
        est_ac = ent["acres"] or _approx_polygon_acres(geom_obj)
        # Skip tiny unnamed puddles (<0.4 acres) to keep database clean
        if ent["name"].startswith("Unnamed") and est_ac < 0.4:
            continue

        existing = None
        if ent["wbic"]:
            existing = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? AND wbic = ?", (pid, ent["wbic"])).fetchone()
        if not existing:
            existing = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? AND LOWER(name) = LOWER(?)", (pid, ent["name"])).fetchone()

        # Preserve manual user edits! Only refresh geometry if user hasn't customized it
        if existing and int(existing["user_edited"] or 0) == 1:
            if not existing["geom_geojson"]:
                conn.execute("UPDATE waterbodies SET geom_geojson = ?, updated_at = ? WHERE id = ?", (json.dumps(geom_obj), ts, existing["id"]))
                conn.commit()
            synced += 1
            continue

        # Commit any prior write before invoking registry (which opens its own cache connection)
        conn.commit()
        dossier: Dict[str, Any] = {}
        if fetch_dossiers and ent["wbic"] and not ent["name"].startswith("Unnamed"):
            dossier = fetch_wi_dnr_lake_dossier(ent["wbic"], download_pdf=download_pdfs, force_refresh=force_refresh)
            if dossier.get("status") == "ok":
                dossiers_loaded += 1
                if dossier.get("contour_map_png_path") or dossier.get("contour_map_pdf_path"):
                    maps_cached += 1

        acres_final = dossier.get("acres") if dossier.get("acres") is not None else est_ac
        species_list = dossier.get("species") or []
        vals = (
            ent["name"],
            ent["waterbody_type"],
            ent["wbic"],
            ent["river_system"],
            dossier.get("county") or "",
            acres_final,
            dossier.get("max_depth_ft"),
            dossier.get("mean_depth_ft"),
            dossier.get("bottom_substrate") or "",
            dossier.get("water_clarity") or "",
            json.dumps(species_list),
            dossier.get("public_access") or "",
            dossier.get("contour_map_url") or "",
            dossier.get("contour_map_pdf_path") or "",
            dossier.get("contour_map_png_path") or "",
            dossier.get("dossier_url") or "",
            ent["source"],
            json.dumps(geom_obj),
            dossier.get("overview") or "",
            ts,
        )
        if existing:
            conn.execute(
                """UPDATE waterbodies SET
                   name=?, waterbody_type=?, wbic=?, river_system=?, county=?, acres=?, max_depth_ft=?, mean_depth_ft=?,
                   bottom_substrate=?, water_clarity=?, species_json=?, public_access=?, contour_map_url=?,
                   contour_map_pdf_path=?, contour_map_png_path=?, dossier_url=?, source=?, geom_geojson=?, notes=?, updated_at=?
                   WHERE id=?""",
                vals + (existing["id"],),
            )
        else:
            conn.execute(
                """INSERT INTO waterbodies
                   (property_id, name, waterbody_type, wbic, river_system, county, acres, max_depth_ft, mean_depth_ft,
                    bottom_substrate, water_clarity, species_json, public_access, contour_map_url,
                    contour_map_pdf_path, contour_map_png_path, dossier_url, source, user_edited, geom_geojson, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (pid,) + vals[:17] + (vals[17], vals[18], vals[19]),
            )
        synced += 1

    conn.commit()
    fresh_bundle = get_property_bundle(conn, property_id=pid)
    return {
        "status": "ok",
        "property_id": pid,
        "synced_count": synced,
        "dossiers_loaded": dossiers_loaded,
        "maps_cached": maps_cached,
        "providers_used": res.get("providers_used") or [],
        "offline_fallback": bool(res.get("offline_fallback")),
        "waterbodies": (fresh_bundle or {}).get("waterbodies") or [],
    }


def save_waterbody(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a custom user-defined pond/lake (e.g. Northwoods Pond) or edit any saved waterbody's dossier."""
    pid, _ = _resolve_pid(payload.get("property_id"))
    if pid is None:
        return {"status": "error", "message": "No camp loaded. Create or select a camp first."}

    conn = get_conn()
    ts = now_iso()
    wid = payload.get("id")
    name = str(payload.get("name") or "").strip()[:120]
    if not name and not wid:
        return {"status": "error", "message": "Waterbody name is required."}

    def _opt_float(v: Any) -> Optional[float]:
        if v in (None, ""):
            return None
        try:
            return float(v)
        except Exception:
            return None

    sp_raw = payload.get("species")
    if isinstance(sp_raw, str):
        species = [s.strip() for s in sp_raw.split(",") if s.strip()]
    elif isinstance(sp_raw, list):
        species = [str(s).strip() for s in sp_raw if str(s).strip()]
    else:
        species = None

    geom_obj = payload.get("geom")
    geom_str = json.dumps(geom_obj) if isinstance(geom_obj, dict) else None
    acres_val = _opt_float(payload.get("acres"))
    if acres_val is None and isinstance(geom_obj, dict):
        acres_val = _approx_polygon_acres(geom_obj)

    if wid:
        row = conn.execute("SELECT * FROM waterbodies WHERE id = ?", (int(wid),)).fetchone()
        if not row:
            return {"status": "error", "message": f"Waterbody {wid} not found."}
        conn.execute(
            """UPDATE waterbodies SET
               name=?, waterbody_type=?, wbic=?, river_system=?, county=?, acres=?, max_depth_ft=?, mean_depth_ft=?,
               bottom_substrate=?, water_clarity=?, species_json=?, public_access=?, contour_map_url=?,
               geom_geojson=?, notes=?, user_edited=1, updated_at=?
               WHERE id=?""",
            (
                name or row["name"],
                str(payload.get("waterbody_type") or row["waterbody_type"] or "lake").strip().lower(),
                str(payload.get("wbic") if payload.get("wbic") is not None else (row["wbic"] or "")).strip(),
                str(payload.get("river_system") if payload.get("river_system") is not None else (row["river_system"] or "")).strip(),
                str(payload.get("county") if payload.get("county") is not None else (row["county"] or "")).strip(),
                acres_val if acres_val is not None else row["acres"],
                _opt_float(payload.get("max_depth_ft")) if "max_depth_ft" in payload else row["max_depth_ft"],
                _opt_float(payload.get("mean_depth_ft")) if "mean_depth_ft" in payload else row["mean_depth_ft"],
                str(payload.get("bottom_substrate") if payload.get("bottom_substrate") is not None else (row["bottom_substrate"] or "")).strip()[:240],
                str(payload.get("water_clarity") if payload.get("water_clarity") is not None else (row["water_clarity"] or "")).strip()[:120],
                json.dumps(species) if species is not None else (row["species_json"] or "[]"),
                str(payload.get("public_access") if payload.get("public_access") is not None else (row["public_access"] or "")).strip()[:240],
                str(payload.get("contour_map_url") if payload.get("contour_map_url") is not None else (row["contour_map_url"] or "")).strip()[:400],
                geom_str if geom_str is not None else (row["geom_geojson"] or ""),
                str(payload.get("notes") if payload.get("notes") is not None else (row["notes"] or "")).strip()[:1000],
                ts,
                int(wid),
            ),
        )
        conn.commit()
        return {"status": "ok", "id": int(wid), "property_id": pid, "updated": True}

    cur = conn.execute(
        """INSERT INTO waterbodies
           (property_id, name, waterbody_type, wbic, river_system, county, acres, max_depth_ft, mean_depth_ft,
            bottom_substrate, water_clarity, species_json, public_access, contour_map_url,
            source, user_edited, geom_geojson, notes, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', 1, ?, ?, ?)""",
        (
            pid,
            name,
            str(payload.get("waterbody_type") or "pond").strip().lower(),
            str(payload.get("wbic") or "").strip(),
            str(payload.get("river_system") or "").strip(),
            str(payload.get("county") or "").strip(),
            acres_val,
            _opt_float(payload.get("max_depth_ft")),
            _opt_float(payload.get("mean_depth_ft")),
            str(payload.get("bottom_substrate") or "").strip()[:240],
            str(payload.get("water_clarity") or "").strip()[:120],
            json.dumps(species or []),
            str(payload.get("public_access") or "Private / Camp Access").strip()[:240],
            str(payload.get("contour_map_url") or "").strip()[:400],
            geom_str or "",
            str(payload.get("notes") or "").strip()[:1000],
            ts,
        ),
    )
    conn.commit()
    return {"status": "ok", "id": cur.lastrowid, "property_id": pid, "acres": acres_val}


def delete_waterbody(waterbody_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM waterbodies WHERE id = ?", (int(waterbody_id),))
    conn.commit()
    return {"status": "ok", "deleted": int(waterbody_id)}


def refresh_waterbody_dossier(waterbody_id: int) -> Dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Waterbody {waterbody_id} not found."}
    wbic = str(row["wbic"] or "").strip()
    if not wbic:
        return {"status": "error", "message": "This waterbody has no WBIC ID for official WI DNR lookup (you can still edit its dossier manually)."}
    dossier = fetch_wi_dnr_lake_dossier(wbic, download_pdf=True, force_refresh=True)
    if dossier.get("status") != "ok":
        return dossier
    conn.execute(
        """UPDATE waterbodies SET
           county=COALESCE(NULLIF(?, ''), county),
           acres=COALESCE(?, acres),
           max_depth_ft=COALESCE(?, max_depth_ft),
           mean_depth_ft=COALESCE(?, mean_depth_ft),
           bottom_substrate=COALESCE(NULLIF(?, ''), bottom_substrate),
           water_clarity=COALESCE(NULLIF(?, ''), water_clarity),
           species_json=CASE WHEN ? != '[]' THEN ? ELSE species_json END,
           public_access=COALESCE(NULLIF(?, ''), public_access),
           contour_map_url=COALESCE(NULLIF(?, ''), contour_map_url),
           contour_map_pdf_path=COALESCE(NULLIF(?, ''), contour_map_pdf_path),
           contour_map_png_path=COALESCE(NULLIF(?, ''), contour_map_png_path),
           dossier_url=COALESCE(NULLIF(?, ''), dossier_url),
           updated_at=?
           WHERE id=?""",
        (
            dossier.get("county") or "",
            dossier.get("acres"),
            dossier.get("max_depth_ft"),
            dossier.get("mean_depth_ft"),
            dossier.get("bottom_substrate") or "",
            dossier.get("water_clarity") or "",
            json.dumps(dossier.get("species") or []),
            json.dumps(dossier.get("species") or []),
            dossier.get("public_access") or "",
            dossier.get("contour_map_url") or "",
            dossier.get("contour_map_pdf_path") or "",
            dossier.get("contour_map_png_path") or "",
            dossier.get("dossier_url") or "",
            now_iso(),
            int(waterbody_id),
        ),
    )
    conn.commit()
    return {"status": "ok", "id": int(waterbody_id), "dossier": dossier}


def get_waterbody_map_preview(waterbody_id: int) -> Dict[str, Any]:
    """Return a locally cached DNR Contour Map PNG as a base64 data URI for offline UI preview."""
    conn = get_conn()
    row = conn.execute("SELECT id, name, wbic, contour_map_url, contour_map_pdf_path, contour_map_png_path FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
    if not row:
        return {"status": "error", "message": "Waterbody not found."}
    png_p = row["contour_map_png_path"] or ""
    if (not png_p or not os.path.isfile(png_p)) and row["wbic"]:
        refresh_waterbody_dossier(int(waterbody_id))
        row = conn.execute("SELECT id, name, wbic, contour_map_url, contour_map_pdf_path, contour_map_png_path FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
        png_p = row["contour_map_png_path"] or ""
    if png_p and os.path.isfile(png_p):
        with open(png_p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return {
            "status": "ok",
            "id": row["id"],
            "name": row["name"],
            "wbic": row["wbic"],
            "contour_map_url": row["contour_map_url"],
            "contour_map_pdf_path": row["contour_map_pdf_path"],
            "contour_map_png_path": png_p,
            "image_data_url": f"data:image/png;base64,{b64}",
        }
    return {
        "status": "no_map",
        "id": row["id"],
        "name": row["name"],
        "contour_map_url": row["contour_map_url"] or "",
        "message": "No official contour map PDF cached for this waterbody.",
    }


def _waterbody_bbox_sw_ne(geom: Optional[Dict[str, Any]]) -> Optional[List[List[float]]]:
    rings = _extract_polygon_rings(geom)
    all_lon, all_lat = [], []
    for ring in rings:
        for c in ring:
            if len(c) >= 2:
                all_lon.append(float(c[0]))
                all_lat.append(float(c[1]))
    if not all_lon or not all_lat:
        return None
    return [[round(min(all_lat), 6), round(min(all_lon), 6)], [round(max(all_lat), 6), round(max(all_lon), 6)]]


def _normalize_crop(crop: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Normalize a chart crop rectangle in [0..1] original image coordinates."""
    if not isinstance(crop, dict):
        return {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0}
    try:
        l = max(0.0, min(0.95, float(crop.get("left", 0.0))))
        t = max(0.0, min(0.95, float(crop.get("top", 0.0))))
        r = max(l + 0.05, min(1.0, float(crop.get("right", 1.0))))
        b = max(t + 0.05, min(1.0, float(crop.get("bottom", 1.0))))
        return {"left": round(l, 4), "top": round(t, 4), "right": round(r, 4), "bottom": round(b, 4)}
    except Exception:
        return {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0}


def _is_full_crop(crop: Optional[Dict[str, float]]) -> bool:
    c = _normalize_crop(crop)
    return c["left"] <= 0.002 and c["top"] <= 0.002 and c["right"] >= 0.998 and c["bottom"] >= 0.998


def save_waterbody_overlay(
    waterbody_id: int,
    bounds: Optional[List[List[float]]] = None,
    opacity: Optional[float] = None,
    visible: Optional[bool] = None,
    control_points: Optional[List[Dict[str, Any]]] = None,
    rotation_deg: Optional[float] = None,
    flip_h: Optional[bool] = None,
    flip_v: Optional[bool] = None,
    blend_mode: Optional[str] = None,
    crop: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Save georeferenced DNR contour chart overlay bounds, crop, opacity, rotation, mirror & blend mode."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Waterbody {waterbody_id} not found."}

    img_w, img_h = 0, 0
    png_p = row["contour_map_png_path"] or ""
    if png_p and os.path.isfile(png_p):
        try:
            from PIL import Image
            with Image.open(png_p) as im:
                img_w, img_h = im.size
        except Exception:
            pass

    prev_xf: Dict[str, Any] = {}
    if row["overlay_transform_json"]:
        try:
            prev_xf = json.loads(row["overlay_transform_json"]) or {}
        except Exception:
            prev_xf = {}

    cur_bounds = None
    if bounds and isinstance(bounds, list) and len(bounds) == 2:
        try:
            s, w = float(bounds[0][0]), float(bounds[0][1])
            n, e = float(bounds[1][0]), float(bounds[1][1])
            cur_bounds = [[round(min(s, n), 6), round(min(w, e), 6)], [round(max(s, n), 6), round(max(w, e), 6)]]
        except Exception:
            cur_bounds = None
    elif row["overlay_bounds_json"]:
        try:
            cur_bounds = json.loads(row["overlay_bounds_json"])
        except Exception:
            cur_bounds = None
    if cur_bounds is None and row["geom_geojson"]:
        try:
            cur_bounds = _waterbody_bbox_sw_ne(json.loads(row["geom_geojson"]))
        except Exception:
            cur_bounds = None

    op_val = float(opacity) if opacity is not None else float(row["overlay_opacity"] if row["overlay_opacity"] is not None else 0.78)
    op_val = max(0.05, min(1.0, op_val))
    vis_val = int(1 if visible else 0) if visible is not None else int(row["overlay_visible"] or 0)
    rot_val = float(rotation_deg) if rotation_deg is not None else float(prev_xf.get("rotation_deg") or 0.0)
    rot_val = ((rot_val + 180.0) % 360.0) - 180.0
    fh_val = bool(flip_h) if flip_h is not None else bool(prev_xf.get("flip_h", False))
    fv_val = bool(flip_v) if flip_v is not None else bool(prev_xf.get("flip_v", False))
    bm_val = str(blend_mode or prev_xf.get("blend_mode") or "cyan_transparent").strip()
    crop_val = _normalize_crop(crop if crop is not None else prev_xf.get("crop"))

    xform = {
        "img_w": img_w or int(prev_xf.get("img_w") or 0),
        "img_h": img_h or int(prev_xf.get("img_h") or 0),
        "bounds": cur_bounds,
        "rotation_deg": round(rot_val, 2),
        "flip_h": fh_val,
        "flip_v": fv_val,
        "blend_mode": bm_val,
        "crop": crop_val,
        "control_points": control_points if control_points is not None else (prev_xf.get("control_points") or []),
    }
    conn.execute(
        "UPDATE waterbodies SET overlay_bounds_json=?, overlay_opacity=?, overlay_visible=?, overlay_transform_json=?, updated_at=? WHERE id=?",
        (json.dumps(cur_bounds) if cur_bounds else "", round(op_val, 3), vis_val, json.dumps(xform), now_iso(), int(waterbody_id)),
    )
    conn.commit()
    return {
        "status": "ok",
        "id": int(waterbody_id),
        "overlay_bounds": cur_bounds,
        "overlay_opacity": round(op_val, 3),
        "overlay_visible": bool(vis_val),
        "overlay_transform": xform,
        "img_w": xform["img_w"],
        "img_h": xform["img_h"],
    }


def unpin_waterbody_overlay(waterbody_id: int) -> Dict[str, Any]:
    """Remove/unpin a saved DNR contour chart overlay from a waterbody."""
    conn = get_conn()
    conn.execute(
        "UPDATE waterbodies SET overlay_bounds_json='', overlay_visible=0, overlay_transform_json='', updated_at=? WHERE id=?",
        (now_iso(), int(waterbody_id)),
    )
    conn.commit()
    return {"status": "ok", "id": int(waterbody_id), "unpinned": True}


def clear_bathymetry(
    property_id: Optional[int],
    waterbody_name: Optional[str] = None,
    clear_points: bool = True,
) -> Dict[str, Any]:
    """Clear interpolated/estimated bathymetry contours (and optionally depth soundings) to redo cleanly."""
    pid, _ = _resolve_pid(property_id)
    if pid is None:
        return {"status": "error", "message": "No camp loaded."}
    conn = get_conn()
    if waterbody_name:
        g_del = conn.execute("DELETE FROM bathymetry_grids WHERE property_id = ? AND waterbody_name = ?", (pid, waterbody_name)).rowcount
        p_del = conn.execute("DELETE FROM bathymetry_points WHERE property_id = ? AND waterbody_name = ?", (pid, waterbody_name)).rowcount if clear_points else 0
    else:
        g_del = conn.execute("DELETE FROM bathymetry_grids WHERE property_id = ?", (pid,)).rowcount
        p_del = conn.execute("DELETE FROM bathymetry_points WHERE property_id = ?", (pid,)).rowcount if clear_points else 0
    conn.commit()
    return {
        "status": "ok",
        "property_id": pid,
        "waterbody_name": waterbody_name or "all",
        "grids_removed": int(g_del),
        "points_removed": int(p_del),
    }


def auto_align_waterbody_chart(waterbody_id: int, crop: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FFT shoreline chamfer matcher: aligns a scanned DNR lake contour chart to the GIS lake shoreline across rotations."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Waterbody {waterbody_id} not found."}
    png_p = row["contour_map_png_path"] or ""
    if not png_p or not os.path.isfile(png_p):
        return {"status": "error", "message": "No cached DNR contour PNG found for this waterbody."}
    if not row["geom_geojson"]:
        return {"status": "error", "message": "Waterbody has no shoreline polygon to match against."}

    geom = json.loads(row["geom_geojson"])
    rings = _extract_polygon_rings(geom)
    if not rings:
        return {"status": "error", "message": "Invalid waterbody polygon."}

    prev_xf: Dict[str, Any] = {}
    if row["overlay_transform_json"]:
        try:
            prev_xf = json.loads(row["overlay_transform_json"]) or {}
        except Exception:
            prev_xf = {}
    crop_box = _normalize_crop(crop if crop is not None else prev_xf.get("crop"))
    has_user_crop = not _is_full_crop(crop_box)

    from PIL import Image
    from scipy.ndimage import binary_dilation, binary_erosion, gaussian_filter
    from scipy.signal import fftconvolve
    from matplotlib.path import Path as MplPath

    # 1. Load chart grayscale, apply user crop if set, strip outer frame border & downsample for fast FFT correlation
    with Image.open(png_p) as im:
        im_l = im.convert("L")
        if has_user_crop:
            ow0, oh0 = im_l.size
            im_l = im_l.crop((
                int(round(crop_box["left"] * ow0)),
                int(round(crop_box["top"] * oh0)),
                int(round(crop_box["right"] * ow0)),
                int(round(crop_box["bottom"] * oh0)),
            ))
        orig_w, orig_h = im_l.size
        target_max = 360.0
        ds = min(1.0, target_max / max(orig_w, orig_h))
        sw = max(64, int(round(orig_w * ds)))
        sh = max(64, int(round(orig_h * ds)))
        small = np.array(im_l.resize((sw, sh), Image.Resampling.BILINEAR), dtype=np.float32)

    # Ink mask (dark contour/shoreline pixels), zeroing out outer frame border & bottom legend strip (lighter if pre-cropped)
    ink = (small < 185.0).astype(np.float32)
    by = max(2, int(sh * (0.015 if has_user_crop else 0.04)))
    bx = max(2, int(sw * (0.015 if has_user_crop else 0.03)))
    by_bot = max(2, int(sh * (0.015 if has_user_crop else 0.09)))
    ink[:by, :] = 0.0
    ink[sh - by_bot :, :] = 0.0
    ink[:, :bx] = 0.0
    ink[:, sw - bx :] = 0.0
    ink_blur = gaussian_filter(ink, sigma=2.0)

    # 2. Compute true GIS lake bounds in equirectangular meters
    all_lon = [float(c[0]) for r in rings for c in r if len(c) >= 2]
    all_lat = [float(c[1]) for r in rings for c in r if len(c) >= 2]
    lake_s, lake_n = min(all_lat), max(all_lat)
    lake_w, lake_e = min(all_lon), max(all_lon)
    lat_mid = (lake_s + lake_n) * 0.5
    cos_lat = max(0.2, math.cos(math.radians(lat_mid)))
    lake_w_m = max(50.0, (lake_e - lake_w) * 111320.0 * cos_lat)
    lake_h_m = max(50.0, (lake_n - lake_s) * 111320.0)
    lake_aspect = lake_w_m / lake_h_m

    # Rasterize GIS shoreline edge + lake interior + exterior collar
    # DNR contour charts have strong dark ink on the shoreline + interior contours and white paper immediately outside.
    base_res = 128
    if lake_aspect >= 1.0:
        tw, th = base_res, max(24, int(round(base_res / lake_aspect)))
    else:
        th, tw = base_res, max(24, int(round(base_res * lake_aspect)))
    glon_t, glat_t = np.meshgrid(np.linspace(lake_w, lake_e, tw), np.linspace(lake_n, lake_s, th))
    gpts = np.column_stack([glon_t.ravel(), glat_t.ravel()])
    inside = np.zeros(gpts.shape[0], dtype=bool)
    for ring in rings:
        if len(ring) >= 3:
            inside |= MplPath(np.array([[c[0], c[1]] for c in ring])).contains_points(gpts)
    in_2d = inside.reshape((th, tw))
    shore_2d = in_2d & (~binary_erosion(in_2d, iterations=2))
    out_2d = binary_dilation(in_2d, iterations=6) & (~in_2d)
    shore_img = Image.fromarray((shore_2d.astype(np.uint8) * 255))
    in_img = Image.fromarray((in_2d.astype(np.uint8) * 255))
    out_img = Image.fromarray((out_2d.astype(np.uint8) * 255))

    pil_ink = Image.fromarray(np.clip(ink_blur * 255.0, 0, 255).astype(np.uint8))

    def _eval_candidate(rot_deg: float, fracs: List[float]) -> Optional[Dict[str, Any]]:
        rot_pil = pil_ink.rotate(-rot_deg, expand=True, resample=Image.Resampling.BILINEAR)
        rot_arr = np.array(rot_pil, dtype=np.float32) / 255.0
        rh, rw = rot_arr.shape
        local_best: Optional[Dict[str, Any]] = None
        for frac in fracs:
            if lake_aspect >= (rw / max(1.0, rh)):
                mw = max(24, int(round(rw * frac)))
                mh = max(16, int(round(mw / lake_aspect)))
            else:
                mh = max(24, int(round(rh * frac)))
                mw = max(16, int(round(mh * lake_aspect)))
            if mw >= rw - 2 or mh >= rh - 2:
                continue
            t_shore = np.array(shore_img.resize((mw, mh), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
            t_in = np.array(in_img.resize((mw, mh), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
            t_out = np.array(out_img.resize((mw, mh), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
            tpl = 2.0 * t_shore + 1.0 * t_in - 1.2 * t_out
            tpl_norm = float(np.sqrt(np.sum(tpl * tpl)))
            if tpl_norm < 1.0:
                continue
            corr = fftconvolve(rot_arr, tpl[::-1, ::-1], mode="valid")
            idx = int(np.argmax(corr))
            cy, cx = divmod(idx, corr.shape[1])
            score = float(corr[cy, cx]) / tpl_norm
            if local_best is None or score > local_best["score"]:
                deg_per_px_lon = (lake_e - lake_w) / float(mw)
                deg_per_px_lat = (lake_n - lake_s) / float(mh)
                ov_w = lake_w - cx * deg_per_px_lon
                ov_e = ov_w + rw * deg_per_px_lon
                ov_n = lake_n + cy * deg_per_px_lat
                ov_s = ov_n - rh * deg_per_px_lat
                local_best = {
                    "rotation_deg": float(rot_deg),
                    "frac": float(frac),
                    "bounds": [[round(ov_s, 6), round(ov_w, 6)], [round(ov_n, 6), round(ov_e, 6)]],
                    "score": round(score, 4),
                }
        return local_best

    # Pass 1: Coarse scan across 4 cardinal orientations (0, 90, -90, 180)
    best_cfg: Optional[Dict[str, Any]] = None
    for rot_deg in (0.0, 90.0, -90.0, 180.0):
        cand = _eval_candidate(rot_deg, [0.55, 0.65, 0.75, 0.85, 0.94])
        if cand and (best_cfg is None or cand["score"] > best_cfg["score"]):
            best_cfg = cand

    # Pass 2: Fine scale + slight compass tilt refinement around the winning orientation
    if best_cfg is not None:
        base_rot = best_cfg["rotation_deg"]
        base_frac = best_cfg["frac"]
        fine_fracs = [f for f in (base_frac - 0.06, base_frac - 0.03, base_frac, base_frac + 0.03, base_frac + 0.06) if 0.45 <= f <= 0.97]
        for delta_rot in (-3.0, -1.5, 0.0, 1.5, 3.0):
            cand = _eval_candidate(base_rot + delta_rot, fine_fracs)
            if cand and cand["score"] > best_cfg["score"]:
                best_cfg = cand

    if not best_cfg:
        return {"status": "error", "message": "Could not compute shoreline auto-match."}

    return {
        "status": "ok",
        "id": int(waterbody_id),
        "name": row["name"],
        "rotation_deg": best_cfg["rotation_deg"],
        "bounds": best_cfg["bounds"],
        "score": best_cfg["score"],
        "message": f"Auto-matched shoreline at {best_cfg['rotation_deg']:+.1f}° rotation — refine with 2-Point Snap or Rotate/Drag handles if needed.",
    }


def _draw_calibration_grid(
    im_rgb,
    nx_min: float,
    nx_max: float,
    ny_min: float,
    ny_max: float,
    title_badge: str = "NORTH UP",
):
    """Draw subtle normalized (nx, ny) GPS grid lines & margin tick labels so vision reads exact coordinates."""
    from PIL import ImageDraw
    out = im_rgb.copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size

    # Choose step size based on tile span (0.10 for overview, 0.05 for zoomed quadrants)
    span = max(nx_max - nx_min, ny_max - ny_min)
    step = 0.05 if span <= 0.62 else 0.10

    # Vertical lines (nx)
    v_start = math.ceil((nx_min + 1e-6) / step) * step
    x_val = v_start
    while x_val < nx_max - 1e-6:
        px = int(round(((x_val - nx_min) / max(1e-6, nx_max - nx_min)) * w))
        if 12 <= px <= w - 12:
            draw.line([(px, 0), (px, h)], fill=(125, 200, 235), width=1)
            lbl = f"x={x_val:.2f}"
            draw.rectangle([px - 22, 2, px + 24, 16], fill=(240, 249, 255))
            draw.text((px - 20, 3), lbl, fill=(3, 105, 161))
            draw.rectangle([px - 22, h - 16, px + 24, h - 2], fill=(240, 249, 255))
            draw.text((px - 20, h - 15), lbl, fill=(3, 105, 161))
        x_val += step

    # Horizontal lines (ny)
    h_start = math.ceil((ny_min + 1e-6) / step) * step
    y_val = h_start
    while y_val < ny_max - 1e-6:
        py = int(round(((y_val - ny_min) / max(1e-6, ny_max - ny_min)) * h))
        if 18 <= py <= h - 18:
            draw.line([(0, py), (w, py)], fill=(125, 200, 235), width=1)
            lbl = f"y={y_val:.2f}"
            draw.rectangle([2, py - 7, 46, py + 7], fill=(240, 249, 255))
            draw.text((4, py - 6), lbl, fill=(3, 105, 161))
            draw.rectangle([w - 46, py - 7, w - 2, py + 7], fill=(240, 249, 255))
            draw.text((w - 44, py - 6), lbl, fill=(3, 105, 161))
        y_val += step

    # North-up badge in top-right corner
    badge = f"N^ | {title_badge}"
    bw = max(90, len(badge) * 7 + 14)
    draw.rectangle([w - bw - 8, 20, w - 8, 38], fill=(12, 74, 110))
    draw.text((w - bw - 2, 23), badge, fill=(240, 249, 255))
    return out


def prepare_chart_vision_tiles(
    property_id: Optional[int] = None,
    waterbody_id: Optional[int] = None,
    waterbody_name: Optional[str] = None,
    mask_noise: bool = True,
) -> Dict[str, Any]:
    """Prepare true-North rotated, cropped, noise-masked overview + 4 zoomed quadrant tiles with global (nx, ny) grids for VLM vision."""
    pid, _ = _resolve_pid(property_id)
    conn = get_conn()
    row = None
    if waterbody_id:
        row = conn.execute("SELECT * FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
    if not row and pid is not None and waterbody_name:
        row = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? AND LOWER(name) = LOWER(?)", (pid, waterbody_name)).fetchone()
    if not row and pid is not None:
        row = conn.execute(
            "SELECT * FROM waterbodies WHERE property_id = ? AND COALESCE(contour_map_png_path, '') != '' ORDER BY COALESCE(acres, 0) DESC LIMIT 1",
            (pid,),
        ).fetchone()
    if not row:
        return {"status": "error", "message": "No waterbody with a cached contour map found."}

    wb_id = int(row["id"])
    png_p = row["contour_map_png_path"] or ""
    if not png_p or not os.path.isfile(png_p):
        return {"status": "error", "message": f"No cached contour map PNG on disk for {row['name']}."}

    xf: Dict[str, Any] = {}
    if row["overlay_transform_json"]:
        try:
            xf = json.loads(row["overlay_transform_json"]) or {}
        except Exception:
            xf = {}

    bounds = None
    if row["overlay_bounds_json"]:
        try:
            bounds = json.loads(row["overlay_bounds_json"])
        except Exception:
            bounds = None

    rot_deg = float(xf.get("rotation_deg") or 0.0)
    fh = bool(xf.get("flip_h", False))
    fv = bool(xf.get("flip_v", False))
    crop_box = _normalize_crop(xf.get("crop"))
    auto_aligned = False

    # If the user hasn't pinned/aligned this chart yet, run FFT auto-match in memory so vision STILL gets a true North-up chart!
    if not bounds and row["geom_geojson"]:
        am = auto_align_waterbody_chart(wb_id, crop=crop_box)
        if am.get("status") == "ok" and am.get("bounds"):
            bounds = am["bounds"]
            rot_deg = float(am.get("rotation_deg") or 0.0)
            auto_aligned = True

    from PIL import Image
    from scipy.ndimage import binary_dilation
    from matplotlib.path import Path as MplPath

    with Image.open(png_p) as im_raw:
        im = im_raw.convert("RGB")
        ow0, oh0 = im.size
        if not _is_full_crop(crop_box):
            im = im.crop((
                int(round(crop_box["left"] * ow0)),
                int(round(crop_box["top"] * oh0)),
                int(round(crop_box["right"] * ow0)),
                int(round(crop_box["bottom"] * oh0)),
            ))
        if fh:
            im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if fv:
            im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if abs(rot_deg) > 0.05:
            im = im.rotate(-rot_deg, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))

    rw, rh = im.size

    # Softly fade out surrounding non-lake visual noise (legend, hypsographic depth-volume graphs, neighboring lakes)
    noise_masked = False
    lake_nx0, lake_nx1, lake_ny0, lake_ny1 = 0.0, 1.0, 0.0, 1.0
    if mask_noise and bounds and len(bounds) == 2 and row["geom_geojson"]:
        try:
            geom = json.loads(row["geom_geojson"])
            rings = _extract_polygon_rings(geom)
            if rings:
                ov_s, ov_w = float(bounds[0][0]), float(bounds[0][1])
                ov_n, ov_e = float(bounds[1][0]), float(bounds[1][1])
                all_lon = [float(c[0]) for r in rings for c in r if len(c) >= 2]
                all_lat = [float(c[1]) for r in rings for c in r if len(c) >= 2]
                # Tight active lake box in aligned [0..1] coords (with 6% margin)
                ln0 = (min(all_lon) - ov_w) / max(1e-9, ov_e - ov_w)
                ln1 = (max(all_lon) - ov_w) / max(1e-9, ov_e - ov_w)
                la0 = (ov_n - max(all_lat)) / max(1e-9, ov_n - ov_s)
                la1 = (ov_n - min(all_lat)) / max(1e-9, ov_n - ov_s)
                if 0.0 < ln1 and ln0 < 1.0 and 0.0 < la1 and la0 < 1.0:
                    lake_nx0 = max(0.0, ln0 - 0.06)
                    lake_nx1 = min(1.0, ln1 + 0.06)
                    lake_ny0 = max(0.0, la0 - 0.06)
                    lake_ny1 = min(1.0, la1 + 0.06)

                mw = 360
                mh = max(64, int(round(mw * (rh / max(1.0, rw)))))
                glon, glat = np.meshgrid(np.linspace(ov_w, ov_e, mw), np.linspace(ov_n, ov_s, mh))
                gpts = np.column_stack([glon.ravel(), glat.ravel()])
                inside = np.zeros(gpts.shape[0], dtype=bool)
                for ring in rings:
                    if len(ring) >= 3:
                        inside |= MplPath(np.array([[c[0], c[1]] for c in ring])).contains_points(gpts)
                mask_2d = inside.reshape((mh, mw))
                if int(np.sum(mask_2d)) >= 25:
                    # Dilate generously so shoreline contour labels & boat landings right along the bank stay sharp
                    dil_iter = max(8, int(round(min(mw, mh) * 0.055)))
                    keep_2d = binary_dilation(mask_2d, iterations=dil_iter)
                    keep_pil = Image.fromarray((keep_2d.astype(np.uint8) * 255)).resize((rw, rh), Image.Resampling.BILINEAR)
                    keep_f = (np.array(keep_pil, dtype=np.float32) / 255.0)[..., None]
                    arr = np.array(im, dtype=np.float32)
                    # Outside lake corridor -> softly washed to 88% white so graphs/legends/other lakes cannot distract vision
                    ghost = arr * 0.12 + 255.0 * 0.88
                    blended = np.clip(arr * keep_f + ghost * (1.0 - keep_f), 0, 255).astype(np.uint8)
                    im = Image.fromarray(blended, mode="RGB")
                    noise_masked = True
        except Exception:
            pass

    out_dir = os.path.join(os.path.dirname(os.path.abspath(png_p)), f"vision_tiles_wb{wb_id}")
    os.makedirs(out_dir, exist_ok=True)

    # Save North-up overview with global (nx, ny) calibration grid
    ov_scale = min(1.0, 1400.0 / max(rw, rh))
    ov_im = im.resize((max(64, int(round(rw * ov_scale))), max(64, int(round(rh * ov_scale)))), Image.Resampling.LANCZOS)
    max_d_txt = f"MAX {int(round(float(row['max_depth_ft'])))}ft" if row["max_depth_ft"] else "OVERVIEW"
    ov_gridded = _draw_calibration_grid(ov_im, 0.0, 1.0, 0.0, 1.0, f"{row['name'][:22]} ({max_d_txt})")
    overview_path = os.path.join(out_dir, "overview_north_up.png")
    ov_gridded.save(overview_path, format="PNG", optimize=True)

    # Generate 4 zoomed overlapping quadrant tiles covering the active lake area with GLOBAL (nx, ny) grid coordinates!
    mx = (lake_nx0 + lake_nx1) * 0.5
    my = (lake_ny0 + lake_ny1) * 0.5
    ov_x = (lake_nx1 - lake_nx0) * 0.08
    ov_y = (lake_ny1 - lake_ny0) * 0.08
    quads = [
        ("nw", "North-West Section", lake_nx0, min(1.0, mx + ov_x), lake_ny0, min(1.0, my + ov_y)),
        ("ne", "North-East Section", max(0.0, mx - ov_x), lake_nx1, lake_ny0, min(1.0, my + ov_y)),
        ("sw", "South-West Section", lake_nx0, min(1.0, mx + ov_x), max(0.0, my - ov_y), lake_ny1),
        ("se", "South-East Section", max(0.0, mx - ov_x), lake_nx1, max(0.0, my - ov_y), lake_ny1),
    ]
    tiles: List[Dict[str, Any]] = []
    for qid, qname, qx0, qx1, qy0, qy1 in quads:
        px0 = max(0, int(round(qx0 * rw)))
        px1 = min(rw, int(round(qx1 * rw)))
        py0 = max(0, int(round(qy0 * rh)))
        py1 = min(rh, int(round(qy1 * rh)))
        if px1 - px0 < 40 or py1 - py0 < 40:
            continue
        sub = im.crop((px0, py0, px1, py1))
        tw, th = sub.size
        t_scale = min(1.0, 1280.0 / max(tw, th))
        if t_scale < 0.99:
            sub = sub.resize((max(64, int(round(tw * t_scale))), max(64, int(round(th * t_scale)))), Image.Resampling.LANCZOS)
        # Actual global normalized bounds of this exact pixel crop
        gx0, gx1 = px0 / float(rw), px1 / float(rw)
        gy0, gy1 = py0 / float(rh), py1 / float(rh)
        sub_gridded = _draw_calibration_grid(sub, gx0, gx1, gy0, gy1, f"{qname} ({max_d_txt})")
        tp = os.path.join(out_dir, f"tile_{qid}.png")
        sub_gridded.save(tp, format="PNG", optimize=True)
        tiles.append({
            "id": qid,
            "region": qname,
            "png_path": tp,
            "nx_min": round(gx0, 4),
            "nx_max": round(gx1, 4),
            "ny_min": round(gy0, 4),
            "ny_max": round(gy1, 4),
        })

    return {
        "status": "ok",
        "waterbody_id": wb_id,
        "waterbody_name": row["name"],
        "wbic": row["wbic"] or "",
        "max_depth_ft": row["max_depth_ft"],
        "acres": row["acres"],
        "coord_space": "aligned",
        "rotation_deg": round(rot_deg, 2),
        "auto_aligned_in_memory": auto_aligned,
        "has_user_crop": not _is_full_crop(crop_box),
        "noise_masked": noise_masked,
        "has_saved_georef": bool(row["overlay_bounds_json"]),
        "overlay_bounds": bounds,
        "overview_png": overview_path,
        "tiles": tiles,
        "instructions": "ALL images are rotated TRUE NORTH UP (top=North, bottom=South, left=West, right=East) with non-lake noise faded out and exact global x=nx, y=ny coordinate labels stamped along all borders. Read ONLY printed depth numbers inside the shoreline (never exceed max_depth_ft!) and pass coord_space='aligned' to hunt_fish(action='extract_depths_from_chart').",
    }


def _chart_xy_to_latlon(
    item: Dict[str, Any],
    bounds: List[List[float]],
    img_w: int = 0,
    img_h: int = 0,
    rotation_deg: float = 0.0,
    flip_h: bool = False,
    flip_v: bool = False,
    crop: Optional[Dict[str, Any]] = None,
    coord_space: str = "raw",
) -> Optional[Tuple[float, float]]:
    """Map normalized (nx, ny) in [0..1] to exact GPS — either direct North-up 'aligned' coords or 'raw' scan coords."""
    if item.get("lat") is not None and item.get("lon") is not None:
        return float(item["lat"]), float(item["lon"])
    if not bounds or len(bounds) != 2:
        return None
    south, west = float(bounds[0][0]), float(bounds[0][1])
    north, east = float(bounds[1][0]), float(bounds[1][1])

    # Direct North-up aligned coordinates (from prepare_chart_vision_tiles grid & zoomed quadrants!)
    use_aligned = (str(coord_space or "").strip().lower() == "aligned") or (
        item.get("aligned_nx") is not None and item.get("aligned_ny") is not None
    )
    if use_aligned:
        ax = item.get("aligned_nx") if item.get("aligned_nx") is not None else item.get("nx")
        ay = item.get("aligned_ny") if item.get("aligned_ny") is not None else item.get("ny")
        if ax is None or ay is None:
            return None
        tx = max(0.0, min(1.0, float(ax)))
        ty = max(0.0, min(1.0, float(ay)))
        lon = west + tx * (east - west)
        lat = north - ty * (north - south)
        return round(lat, 6), round(lon, 6)

    nx = item.get("nx")
    ny = item.get("ny")
    if nx is None and item.get("px") is not None and img_w > 0:
        nx = float(item["px"]) / float(img_w)
    if ny is None and item.get("py") is not None and img_h > 0:
        ny = float(item["py"]) / float(img_h)
    if nx is None or ny is None:
        return None
    nx_f = max(0.0, min(1.0, float(nx)))
    ny_f = max(0.0, min(1.0, float(ny)))

    cbox = _normalize_crop(crop)
    cw_frac = max(0.05, cbox["right"] - cbox["left"])
    ch_frac = max(0.05, cbox["bottom"] - cbox["top"])
    cnx = max(0.0, min(1.0, (nx_f - cbox["left"]) / cw_frac))
    cny = max(0.0, min(1.0, (ny_f - cbox["top"]) / ch_frac))

    w = (float(img_w) if img_w > 0 else 1000.0) * cw_frac
    h = (float(img_h) if img_h > 0 else 1000.0) * ch_frac
    dx = (cnx - 0.5) * w
    dy = (cny - 0.5) * h
    if flip_h:
        dx = -dx
    if flip_v:
        dy = -dy
    rad = math.radians(float(rotation_deg or 0.0))
    c, s = math.cos(rad), math.sin(rad)
    rx = dx * c - dy * s
    ry = dx * s + dy * c
    rot_w = abs(w * c) + abs(h * s)
    rot_h = abs(w * s) + abs(h * c)
    tx = 0.5 + (rx / max(1e-6, rot_w))
    ty = 0.5 + (ry / max(1e-6, rot_h))
    lon = west + tx * (east - west)
    lat = north - ty * (north - south)
    return round(lat, 6), round(lon, 6)


def extract_depths_from_chart(
    property_id: Optional[int],
    waterbody_id: Optional[int] = None,
    waterbody_name: Optional[str] = None,
    samples: Optional[List[Dict[str, Any]]] = None,
    contour_lines: Optional[List[Dict[str, Any]]] = None,
    structures: Optional[List[Dict[str, Any]]] = None,
    clear_prior_vision: bool = False,
    coord_space: str = "raw",
) -> Dict[str, Any]:
    """Convert agent-extracted chart coordinates (aligned or raw nx, ny in 0..1) into exact GPS depths & fish structures."""
    pid, bundle = _resolve_pid(property_id)
    if pid is None or not bundle:
        return {"status": "error", "message": "No camp loaded."}
    conn = get_conn()
    wb = None
    if waterbody_id:
        wb = conn.execute("SELECT * FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
    if not wb and waterbody_name:
        wb = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? AND LOWER(name) = LOWER(?)", (pid, waterbody_name)).fetchone()
    if not wb:
        wb = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? ORDER BY COALESCE(acres, 0) DESC LIMIT 1", (pid,)).fetchone()
    if not wb:
        return {"status": "error", "message": "No saved waterbody found for chart extraction."}

    wb_name = wb["name"]
    wb_max_depth = float(wb["max_depth_ft"]) if wb["max_depth_ft"] not in (None, "", 0) else None
    max_allowed_depth = (wb_max_depth * 1.12) if (wb_max_depth and wb_max_depth > 0) else None

    bounds = None
    img_w, img_h = 0, 0
    rot_deg, fh, fv = 0.0, False, False
    crop_box = _normalize_crop(None)
    if wb["overlay_bounds_json"]:
        try:
            bounds = json.loads(wb["overlay_bounds_json"])
        except Exception:
            bounds = None
    if wb["overlay_transform_json"]:
        try:
            xf = json.loads(wb["overlay_transform_json"])
            img_w = int(xf.get("img_w") or 0)
            img_h = int(xf.get("img_h") or 0)
            rot_deg = float(xf.get("rotation_deg") or 0.0)
            fh = bool(xf.get("flip_h", False))
            fv = bool(xf.get("flip_v", False))
            crop_box = _normalize_crop(xf.get("crop"))
        except Exception:
            pass
    geom = None
    if wb["geom_geojson"]:
        try:
            geom = json.loads(wb["geom_geojson"])
        except Exception:
            geom = None
    if bounds is None and geom:
        # Try auto-align bounds if available, else lake bbox
        am = auto_align_waterbody_chart(int(wb["id"]), crop=crop_box)
        if am.get("status") == "ok" and am.get("bounds"):
            bounds = am["bounds"]
            rot_deg = float(am.get("rotation_deg") or 0.0)
        else:
            bounds = _waterbody_bbox_sw_ne(geom)
    if bounds is None:
        return {"status": "error", "message": f"Waterbody '{wb_name}' has no aligned chart bounds or polygon."}

    rings = _extract_polygon_rings(geom)
    poly_paths = []
    if rings:
        try:
            from matplotlib.path import Path as MplPath
            poly_paths = [MplPath(np.array([[c[0], c[1]] for c in ring])) for ring in rings if len(ring) >= 3]
        except Exception:
            poly_paths = []

    def _inside_water(la: float, lo: float) -> bool:
        if not poly_paths:
            return True
        return any(bool(pp.contains_point((lo, la))) for pp in poly_paths)

    ts = now_iso()
    if clear_prior_vision:
        conn.execute("DELETE FROM bathymetry_points WHERE property_id = ? AND waterbody_name = ? AND source = 'chart_vision'", (pid, wb_name))

    pts_added = 0
    pts_rejected = 0
    pts_over_max = 0
    structs_added = 0

    for item in (samples or []):
        if not isinstance(item, dict) or item.get("depth_ft") is None:
            continue
        dv = max(0.0, float(item["depth_ft"]))
        if max_allowed_depth is not None and dv > max_allowed_depth:
            pts_over_max += 1
            continue
        pos = _chart_xy_to_latlon(item, bounds, img_w, img_h, rot_deg, fh, fv, crop_box, coord_space)
        if not pos:
            continue
        la, lo = pos
        if not _inside_water(la, lo):
            pts_rejected += 1
            continue
        conn.execute(
            "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, 'chart_vision', ?)",
            (pid, wb_name, la, lo, dv, ts),
        )
        pts_added += 1

    for cl in (contour_lines or []):
        if not isinstance(cl, dict) or cl.get("depth_ft") is None:
            continue
        dv = max(0.0, float(cl["depth_ft"]))
        if max_allowed_depth is not None and dv > max_allowed_depth:
            pts_over_max += 1
            continue
        for pt in (cl.get("points") or []):
            if not isinstance(pt, dict):
                continue
            pos = _chart_xy_to_latlon(pt, bounds, img_w, img_h, rot_deg, fh, fv, crop_box, coord_space)
            if not pos:
                continue
            la, lo = pos
            if not _inside_water(la, lo):
                pts_rejected += 1
                continue
            conn.execute(
                "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, 'chart_vision', ?)",
                (pid, wb_name, la, lo, dv, ts),
            )
            pts_added += 1

    for st in (structures or []):
        if not isinstance(st, dict) or not st.get("name"):
            continue
        pos = _chart_xy_to_latlon(st, bounds, img_w, img_h, rot_deg, fh, fv, crop_box, coord_space)
        if not pos:
            continue
        la, lo = pos
        st_name = str(st["name"]).strip()[:120]
        st_depth = float(st["depth_ft"]) if st.get("depth_ft") not in (None, "") else None
        if st_depth is not None and max_allowed_depth is not None and st_depth > max_allowed_depth:
            st_depth = wb_max_depth
        # Avoid duplicate structure rows on re-runs
        dup = conn.execute(
            "SELECT id FROM fish_structures WHERE property_id = ? AND LOWER(name) = LOWER(?)",
            (pid, st_name),
        ).fetchone()
        if not dup:
            conn.execute(
                """INSERT INTO fish_structures (property_id, name, kind, lat, lon, depth_ft, bottom_type, target_species, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    st_name,
                    str(st.get("kind") or "drop_off").strip().lower(),
                    la,
                    lo,
                    st_depth,
                    str(st.get("bottom_type") or "").strip()[:60],
                    str(st.get("target_species") or "").strip()[:120],
                    str(st.get("notes") or "Extracted from georeferenced contour chart").strip()[:300],
                    ts,
                ),
            )
            structs_added += 1
        # Also anchor the structure's real depth into bathymetry_points if inside water so deep holes/humps shape the contour grid!
        if st_depth is not None and st_depth > 0 and _inside_water(la, lo):
            conn.execute(
                "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, 'chart_vision', ?)",
                (pid, wb_name, la, lo, st_depth, ts),
            )
            pts_added += 1

    conn.commit()
    bathy_res = rebuild_bathymetry(pid, wb_name) if pts_added > 0 else {"status": "ok", "contour_count": 0}
    return {
        "status": "ok",
        "property_id": pid,
        "waterbody_id": int(wb["id"]),
        "waterbody_name": wb_name,
        "coord_space": coord_space,
        "used_georeferenced_overlay": bool(wb["overlay_bounds_json"]),
        "official_max_depth_ft": wb_max_depth,
        "points_added": pts_added,
        "points_outside_water_skipped": pts_rejected,
        "points_exceeding_max_depth_rejected": pts_over_max,
        "structures_added": structs_added,
        "contour_count": bathy_res.get("contour_count", 0),
        "max_depth_ft": bathy_res.get("max_depth_ft"),
        "shoreline_clipped": bathy_res.get("shoreline_clipped", False),
    }


def estimate_lake_bathymetry(
    property_id: Optional[int],
    waterbody_id: Optional[int] = None,
    waterbody_name: Optional[str] = None,
    max_depth_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Generate a 1-click shoreline-distance synthetic bathymetry estimate (clearly tagged as estimated)."""
    pid, _ = _resolve_pid(property_id)
    if pid is None:
        return {"status": "error", "message": "No camp loaded."}
    conn = get_conn()
    wb = None
    if waterbody_id:
        wb = conn.execute("SELECT * FROM waterbodies WHERE id = ?", (int(waterbody_id),)).fetchone()
    if not wb and waterbody_name:
        wb = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? AND LOWER(name) = LOWER(?)", (pid, waterbody_name)).fetchone()
    if not wb:
        wb = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? ORDER BY COALESCE(acres, 0) DESC LIMIT 1", (pid,)).fetchone()
    if not wb or not wb["geom_geojson"]:
        return {"status": "error", "message": "Sync or draw a lake/pond shoreline polygon first before estimating bathymetry."}

    geom = json.loads(wb["geom_geojson"])
    rings = _extract_polygon_rings(geom)
    if not rings:
        return {"status": "error", "message": "Waterbody has no valid shoreline polygon."}

    wb_name = wb["name"]
    max_d = float(max_depth_override) if max_depth_override not in (None, "") else float(wb["max_depth_ft"] or 15.0)
    max_d = max(4.0, min(300.0, max_d))

    all_lon = [float(c[0]) for r in rings for c in r if len(c) >= 2]
    all_lat = [float(c[1]) for r in rings for c in r if len(c) >= 2]
    min_lo, max_lo = min(all_lon), max(all_lon)
    min_la, max_la = min(all_lat), max(all_lat)
    pad_lo = max(0.0002, (max_lo - min_lo) * 0.04)
    pad_la = max(0.0002, (max_la - min_la) * 0.04)

    grid_n = 72
    lons_1d = np.linspace(min_lo - pad_lo, max_lo + pad_lo, grid_n)
    lats_1d = np.linspace(min_la - pad_la, max_la + pad_la, grid_n)
    glon, glat = np.meshgrid(lons_1d, lats_1d)

    from matplotlib.path import Path as MplPath
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    grid_pts = np.column_stack([glon.ravel(), glat.ravel()])
    inside = np.zeros(grid_pts.shape[0], dtype=bool)
    for ring in rings:
        if len(ring) >= 3:
            inside |= MplPath(np.array([[c[0], c[1]] for c in ring])).contains_points(grid_pts)
    inside_2d = inside.reshape((grid_n, grid_n))
    if int(np.sum(inside_2d)) < 8:
        return {"status": "error", "message": "Waterbody polygon too small to rasterize."}

    # Distance from shoreline -> smooth basin profile scaled to known max_depth_ft
    dist = distance_transform_edt(inside_2d)
    d_max = float(np.max(dist))
    if d_max <= 0:
        return {"status": "error", "message": "Could not compute shoreline distance."}
    norm_d = np.clip(dist / d_max, 0.0, 1.0)
    z_grid = (norm_d ** 0.85) * max_d
    z_grid = gaussian_filter(z_grid, sigma=0.8)
    z_grid = np.where(inside_2d, np.clip(z_grid, 0.0, max_d), np.nan)

    valid_z = z_grid[inside_2d]
    avg_d = float(np.mean(valid_z)) if valid_z.size else (max_d * 0.35)
    step_ft = 2.0 if max_d <= 15.0 else (4.0 if max_d <= 35.0 else 5.0)
    levels = np.arange(step_ft, max_d * 0.98, step_ft)
    contours_fc: Dict[str, Any] = {"type": "FeatureCollection", "features": []}

    if len(levels) >= 1:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        cs = ax.contour(glon, glat, z_grid, levels=levels)
        allsegs = getattr(cs, "allsegs", None)
        if allsegs is not None:
            for lvl_val, seg_list in zip(cs.levels, allsegs):
                dv = round(float(lvl_val), 1)
                norm = dv / max(1.0, max_d)
                col = depth_to_color(norm)
                for seg in seg_list:
                    if len(seg) < 2:
                        continue
                    coords = [[round(float(pt[0]), 6), round(float(pt[1]), 6)] for pt in seg]
                    contours_fc["features"].append({
                        "type": "Feature",
                        "properties": {
                            "depth_ft": int(round(dv)) if abs(dv - round(dv)) < 0.05 else dv,
                            "depth_norm": round(norm, 3),
                            "color": col,
                            "kind": "bathymetry_contour",
                            "estimated": True,
                        },
                        "geometry": {"type": "LineString", "coordinates": coords},
                    })
        plt.close(fig)

    conn.execute("DELETE FROM bathymetry_grids WHERE property_id = ? AND waterbody_name = ?", (pid, wb_name))
    conn.execute(
        """INSERT INTO bathymetry_grids (property_id, waterbody_name, source, max_depth_ft, avg_depth_ft, contours_geojson, grid_json, updated_at)
           VALUES (?, ?, 'estimated_shoreline_model', ?, ?, ?, ?, ?)""",
        (
            pid,
            wb_name,
            round(max_d, 1),
            round(avg_d, 1),
            json.dumps(contours_fc),
            json.dumps({"rows": grid_n, "cols": grid_n, "shoreline_clipped": True, "estimated": True}),
            now_iso(),
        ),
    )
    conn.commit()
    return {
        "status": "ok",
        "property_id": pid,
        "waterbody_id": int(wb["id"]),
        "waterbody_name": wb_name,
        "estimated": True,
        "max_depth_ft": round(max_d, 1),
        "avg_depth_ft": round(avg_d, 1),
        "contour_count": len(contours_fc["features"]),
        "note": "Estimated from shoreline geometry & known max depth — drop real soundings or extract from an aligned DNR chart to refine.",
    }


def import_depth_dataset(
    property_id: Optional[int],
    filename: str,
    file_content_b64: str,
    waterbody_name: str = "Camp Water",
) -> Dict[str, Any]:
    """Import fish-finder / sonar CSV (lat,lon,depth_ft|depth_m) or GeoJSON depth points/contours."""
    pid, _ = _resolve_pid(property_id)
    if pid is None:
        return {"status": "error", "message": "No camp loaded."}
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    text = base64.b64decode(file_content_b64).decode("utf-8", errors="replace")
    conn = get_conn()
    ts = now_iso()
    wb = (waterbody_name or os.path.splitext(os.path.basename(filename or "Camp Water"))[0]).strip()[:100]
    added = 0

    if ext == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return {"status": "error", "message": "Empty CSV file."}
        low = {f.lower().strip(): f for f in reader.fieldnames}
        lat_k = low.get("lat") or low.get("latitude") or low.get("y")
        lon_k = low.get("lon") or low.get("lng") or low.get("longitude") or low.get("x")
        d_k = low.get("depth_ft") or low.get("depth") or low.get("depth_m") or low.get("z")
        if not (lat_k and lon_k and d_k):
            return {"status": "error", "message": "Sonar CSV requires lat, lon, and depth_ft (or depth_m/depth) columns."}
        is_meters = "m" in d_k.lower() and "ft" not in d_k.lower()
        for row in reader:
            try:
                la = float(row[lat_k])
                lo = float(row[lon_k])
                dv = abs(float(row[d_k])) * (3.28084 if is_meters else 1.0)
                conn.execute(
                    "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, wb, la, lo, round(dv, 2), f"sonar_csv:{os.path.basename(filename)}", ts),
                )
                added += 1
            except Exception:
                continue
        conn.commit()
        if added >= 4:
            res = rebuild_bathymetry(pid, wb)
            res["points_imported"] = added
            return res
        return {"status": "ok", "points_imported": added, "message": f"Imported {added} depth point(s). Add at least 4 total to build contours."}

    if ext in ("geojson", "json"):
        gj = json.loads(text)
        feats = gj.get("features") if isinstance(gj, dict) and gj.get("type") == "FeatureCollection" else [gj]
        contour_feats = []
        for f in (feats or [])[:400]:
            g = f.get("geometry") or {}
            pr = f.get("properties") or {}
            raw_d = pr.get("depth_ft") or pr.get("DEPTH_FT") or pr.get("depth") or pr.get("DEPTH") or pr.get("CONTOUR")
            try:
                dv = abs(float(raw_d)) if raw_d is not None else None
            except Exception:
                dv = None
            if g.get("type") == "Point" and dv is not None and len(g.get("coordinates") or []) >= 2:
                conn.execute(
                    "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, wb, float(g["coordinates"][1]), float(g["coordinates"][0]), round(dv, 2), f"geojson:{os.path.basename(filename)}", ts),
                )
                added += 1
            elif g.get("type") in ("LineString", "MultiLineString"):
                contour_feats.append({"type": "Feature", "properties": {**pr, "depth_ft": dv, "color": depth_to_color(min(1.0, (dv or 5.0) / 30.0))}, "geometry": g})
        conn.commit()
        if contour_feats:
            depths = [f["properties"]["depth_ft"] for f in contour_feats if f["properties"].get("depth_ft") is not None]
            max_d = max(depths) if depths else 20.0
            conn.execute("DELETE FROM bathymetry_grids WHERE property_id = ? AND waterbody_name = ?", (pid, wb))
            conn.execute(
                "INSERT INTO bathymetry_grids (property_id, waterbody_name, source, max_depth_ft, avg_depth_ft, contours_geojson, grid_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, '{}', ?)",
                (pid, wb, f"geojson:{os.path.basename(filename)}", max_d, round(max_d * 0.45, 1), json.dumps({"type": "FeatureCollection", "features": contour_feats}), ts),
            )
            conn.commit()
            return {"status": "ok", "waterbody_name": wb, "contour_count": len(contour_feats), "points_imported": added}
        if added >= 4:
            res = rebuild_bathymetry(pid, wb)
            res["points_imported"] = added
            return res
        return {"status": "ok", "points_imported": added}

    if ext == "gpx":
        import re as _re
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            try:
                # Tolerate real-world fish-finder GPX exports that omit xmlns declarations for gpxtpx:/etc.
                cleaned_xml = _re.sub(r"<(/?)[A-Za-z0-9_.-]+:([A-Za-z0-9_.-]+)", r"<\1\2", text)
                root = ET.fromstring(cleaned_xml)
            except Exception as exc:
                return {"status": "error", "message": f"Invalid GPX XML: {exc}"}
        for elem in root.iter():
            tag_low = elem.tag.split("}")[-1].lower()
            if tag_low not in ("wpt", "trkpt", "rtept"):
                continue
            la_s = elem.attrib.get("lat")
            lo_s = elem.attrib.get("lon")
            if not (la_s and lo_s):
                continue
            d_ft = None
            for child in elem.iter():
                ctag = child.tag.split("}")[-1].lower()
                if ctag in ("depth", "waterdepth", "depth_ft", "depth_m") and child.text:
                    try:
                        val = abs(float(child.text.strip()))
                        # Garmin/Lowrance GPX <depth> is typically meters unless tagged depth_ft
                        d_ft = val if "ft" in ctag else (val * 3.28084)
                        break
                    except Exception:
                        pass
            if d_ft is None:
                continue
            try:
                conn.execute(
                    "INSERT INTO bathymetry_points (property_id, waterbody_name, lat, lon, depth_ft, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, wb, float(la_s), float(lo_s), round(d_ft, 2), f"sonar_gpx:{os.path.basename(filename)}", ts),
                )
                added += 1
            except Exception:
                continue
        conn.commit()
        if added >= 1:
            res = rebuild_bathymetry(pid, wb)
            res["points_imported"] = added
            return res
        return {"status": "error", "message": "No <depth> tags found in GPX track/waypoints."}

    return {"status": "error", "message": f"Unsupported bathymetry format '.{ext}'. Use .csv, .gpx, or .geojson."}


def fetch_camp_gauges(property_id: Optional[int] = None, lat: Optional[float] = None, lon: Optional[float] = None, force_refresh: bool = False) -> Dict[str, Any]:
    if lat is None or lon is None:
        _, bundle = _resolve_pid(property_id)
        if not bundle:
            return {"status": "error", "message": "No camp loaded."}
        lat = float(bundle["property"]["center_lat"])
        lon = float(bundle["property"]["center_lon"])
    return fetch_stream_gauges_cascade(float(lat), float(lon), force_refresh=force_refresh)


def _compute_bite_index(cur_wx: Dict[str, Any], gauges: List[Dict[str, Any]]) -> Tuple[int, str, List[str]]:
    """Guide-grade 0-100 Solunar, Barometric, Sky & Flow Fish Activity Score."""
    score = 52
    factors: List[str] = []

    p_trend = cur_wx.get("pressure_trend") or "steady"
    p_inhg = float(cur_wx.get("pressure_inhg") or 30.0)
    cc = float(cur_wx.get("cloud_cover_pct") or 50.0)
    w_mph = float(cur_wx.get("wind_mph") or 7.0)
    moon_pct = float(((cur_wx.get("moon") or {}).get("illumination_pct")) or 50.0)
    temp_f = float(cur_wx.get("temp_f") or 55.0)

    if p_trend == "falling":
        score += 18
        factors.append(f"Falling pre-front barometer ({p_inhg:.2f} inHg) triggers aggressive feeding")
    elif p_trend == "steady" and 29.85 <= p_inhg <= 30.20:
        score += 10
        factors.append(f"Stable barometer sweet spot ({p_inhg:.2f} inHg)")
    elif p_trend == "rising" and p_inhg >= 30.25:
        score -= 8
        factors.append("Post-front high pressure — fish hug tight to cover/bottom")

    if 45.0 <= cc <= 95.0:
        score += 10
        factors.append(f"Overcast/broken sky ({cc:.0f}%) lowers light penetration & extends shallow bite")
    elif cc < 15.0:
        score -= 5
        factors.append("Bluebird sun — target shade, weed edges & deeper breaklines")

    if 5.0 <= w_mph <= 14.0:
        score += 9
        factors.append(f"Ideal {w_mph:.0f} mph walleye/bass chop stacks baitfish on windward banks")
    elif w_mph > 20.0:
        score -= 10
        factors.append(f"Heavy wind ({w_mph:.0f} mph) limits open-water boat control — fish sheltered leeward pockets")

    if moon_pct >= 88.0 or moon_pct <= 12.0:
        score += 8
        factors.append(f"Strong solunar phase ({moon_pct:.0f}% moon)")

    if temp_f <= 38.0:
        score -= 6
        factors.append(f"Cold air ({temp_f:.0f}°F) — slow down presentations near deeper wintering holes")

    if gauges and gauges[0].get("cfs") is not None:
        factors.append(f"Nearest gauge: {gauges[0]['site_name'][:28]} ({gauges[0]['cfs']} CFS)")

    score = max(10, min(100, int(round(score))))
    rating = "PEAK" if score >= 80 else ("HIGH" if score >= 65 else ("MODERATE" if score >= 45 else "TOUGH"))
    return score, rating, factors[:4]


def _species_presentation_matrix(cur_wx: Dict[str, Any]) -> List[Dict[str, str]]:
    """Practical fishing-guide presentation cues derived from live temp, sky, wind & pressure."""
    temp_f = float(cur_wx.get("temp_f") or 55.0)
    cc = float(cur_wx.get("cloud_cover_pct") or 50.0)
    p_trend = cur_wx.get("pressure_trend") or "steady"
    is_cold = temp_f < 48.0
    is_bright = cc < 30.0
    return [
        {
            "species": "Trout (Streams & Creeks)",
            "zone": "Deep tailouts, undercut banks & slow current seams" if is_cold else "Riffle-to-pool transitions, boulder eddies & shaded runs",
            "presentation": "Slow dead-drifted nymphs, small inline spinners, or 1/16 oz marabou jigs near bottom" if is_cold else "Upstream cast spinners, drifted nymphs/streamers across current seams",
        },
        {
            "species": "Walleye",
            "zone": "Wind-blown rocky points & 8–18 ft breaklines" if not is_bright else "Deeper 14–24 ft drop-offs, basin edges & mud/gravel transitions",
            "presentation": "1/8–1/4 oz jig + minnow or slow paddle-tail swimbait hugging bottom" if p_trend != "falling" else "Pitching crankbaits or Rippin' Raps along windward breaklines",
        },
        {
            "species": "Smallmouth & Largemouth Bass",
            "zone": "Rock piles, current eddies & main-lake points (smallies) / deep green weed edges & timber (largemouth)",
            "presentation": "Ned rig, hair jig, or tube dragged slowly along rock seams" if is_cold else "Spinnerbaits, chatterbaits, or swim jigs along windward cover & eddies",
        },
        {
            "species": "Northern Pike & Musky",
            "zone": "Remaining green weed edges, creek mouths & sharp drop-offs adjacent to panfish basins",
            "presentation": "Slow-rolled large swimbaits, suspending jerkbaits, or live sucker rigs on edge of deep holes",
        },
    ]


def analyze_fishing_spot(
    property_id: Optional[int] = None,
    wind_deg: Optional[float] = None,
    wind_mph: Optional[float] = None,
) -> Dict[str, Any]:
    """Comprehensive flow, bathymetry, structure, river-gauge & solunar fishing analysis."""
    pid, bundle = _resolve_pid(property_id)
    if pid is None or not bundle:
        return {"status": "error", "message": "No camp loaded. Create or select a camp first."}

    prop = bundle["property"]
    lat, lon = float(prop["center_lat"]), float(prop["center_lon"])

    try:
        wx = fetch_hunt_weather(lat, lon, days=2)
        cur_wx = wx.get("current_conditions") or {}
    except Exception:
        wx, cur_wx = {}, {}

    eff_wdeg = float(wind_deg) if wind_deg is not None else float(cur_wx.get("wind_deg") or 315.0)
    eff_wmph = float(wind_mph) if wind_mph is not None else float(cur_wx.get("wind_mph") or 8.0)
    windward_bank_deg = (eff_wdeg + 180.0) % 360.0
    windward_bank = deg_to_compass(windward_bank_deg)
    leeward_bank = deg_to_compass(eff_wdeg)

    g_res = fetch_stream_gauges_cascade(lat, lon)
    gauges = g_res.get("gauges") or []
    bite_score, bite_rating, bite_factors = _compute_bite_index(cur_wx, gauges)

    structures = bundle.get("fish_structures") or []
    flows = bundle.get("water_flows") or []
    streams = [r for r in (bundle.get("routes") or []) if (r.get("kind") or "").lower() in ("stream", "creek", "current_seam", "drift_line")]
    bathy_grids = bundle.get("bathymetry_grids") or []
    bathy_pts = bundle.get("bathymetry_points") or []
    waterbodies = bundle.get("waterbodies") or []

    ranked_spots = []
    for st in structures:
        s_score = 72
        cues = []
        sk = (st.get("kind") or "").lower()
        if sk in ("drop_off", "eddy_seam", "riffle_pool", "point_bar"):
            s_score += 12
            cues.append(f"High-percentage ambush structure ({sk.replace('_', ' ')})")
        elif sk in ("deep_hole", "submerged_timber", "rock_pile", "weed_edge"):
            s_score += 9
            cues.append(f"Primary holding cover ({sk.replace('_', ' ')})")
        if st.get("depth_ft") is not None:
            cues.append(f"Mapped depth: {st['depth_ft']} ft ({st.get('bottom_type') or 'mixed bottom'})")
        if cur_wx.get("pressure_trend") == "falling" and sk in ("point_bar", "weed_edge", "riffle_pool"):
            s_score += 8
            cues.append("Pre-front falling barometer pushes active feeders onto this shallow/mid edge")
        elif cur_wx.get("cloud_cover_pct", 50) < 25 and sk in ("deep_hole", "drop_off", "submerged_timber"):
            s_score += 7
            cues.append("Bright sky favors deeper/shaded cover here")
        ranked_spots.append({
            "id": st["id"],
            "name": st["name"],
            "kind": sk,
            "lat": st["lat"],
            "lon": st["lon"],
            "depth_ft": st.get("depth_ft"),
            "bottom_type": st.get("bottom_type"),
            "target_species": st.get("target_species"),
            "score": min(100, s_score),
            "cues": cues,
            "notes": st.get("notes") or "",
        })
    ranked_spots.sort(key=lambda x: -x["score"])

    return {
        "status": "ok",
        "property_id": pid,
        "property_name": prop["name"],
        "bite_index": {"score": bite_score, "rating": bite_rating, "factors": bite_factors},
        "wind_drift": {
            "wind_from_deg": round(eff_wdeg),
            "wind_from_compass": deg_to_compass(eff_wdeg),
            "wind_mph": round(eff_wmph, 1),
            "windward_baitfish_shore": windward_bank,
            "sheltered_leeward_shore": leeward_bank,
            "tactic": f"Wind blows from {deg_to_compass(eff_wdeg)} toward the {windward_bank} shoreline — baitfish & warmer surface water stack on the {windward_bank} bank/points; use the {leeward_bank} bank for calm boat/casting shelter.",
        },
        "current_weather": cur_wx,
        "nearest_gauges": gauges[:4],
        "gauge_providers": g_res.get("providers_used") or [],
        "traced_streams": [{"id": r["id"], "name": r["name"], "kind": r["kind"], "length_yd": r.get("length_yd"), "flow_speed": r.get("flow_speed")} for r in streams],
        "water_flows": flows,
        "bathymetry_summary": [
            {"waterbody_name": bg["waterbody_name"], "max_depth_ft": bg["max_depth_ft"], "avg_depth_ft": bg["avg_depth_ft"], "contour_count": len(((bg.get("contours") or {}).get("features")) or [])}
            for bg in bathy_grids
        ],
        "waterbodies": [
            {
                "id": wb["id"],
                "name": wb["name"],
                "waterbody_type": wb.get("waterbody_type"),
                "wbic": wb.get("wbic"),
                "river_system": wb.get("river_system"),
                "acres": wb.get("acres"),
                "max_depth_ft": wb.get("max_depth_ft"),
                "mean_depth_ft": wb.get("mean_depth_ft"),
                "bottom_substrate": wb.get("bottom_substrate"),
                "water_clarity": wb.get("water_clarity"),
                "species": wb.get("species") or [],
                "public_access": wb.get("public_access"),
                "contour_map_url": wb.get("contour_map_url"),
                "contour_map_png_path": wb.get("contour_map_png_path"),
                "overlay_bounds": wb.get("overlay_bounds"),
                "has_georeferenced_chart": bool(wb.get("overlay_bounds") and wb.get("has_cached_map")),
                "notes": wb.get("notes"),
                "user_edited": wb.get("user_edited"),
            }
            for wb in waterbodies
        ],
        "depth_soundings_count": len(bathy_pts),
        "ranked_structures": ranked_spots,
        "species_matrix": _species_presentation_matrix(cur_wx),
    }


if __name__ == "__main__":
    p = parse_json_stdin()
    act = str(p.get("action") or "analyze").strip().lower()
    if act == "gauges":
        out = fetch_camp_gauges(p.get("property_id"), p.get("lat"), p.get("lon"), bool(p.get("force_refresh", False)))
    elif act == "add_structure":
        out = add_fish_structure(p.get("property_id"), p.get("name", ""), p.get("kind", "drop_off"), float(p["lat"]), float(p["lon"]), p.get("depth_ft"), p.get("bottom_type", ""), p.get("target_species", ""), p.get("notes", ""))
    elif act == "delete_structure":
        out = delete_fish_structure(int(p["id"]))
    elif act == "add_flow":
        out = add_water_flow(p.get("property_id"), p.get("label", ""), float(p["lat"]), float(p["lon"]), float(p.get("flow_to_deg", 180)), p.get("speed_class", "moderate"), p.get("flow_type", "river_current"), p.get("notes", ""))
    elif act == "delete_flow":
        out = delete_water_flow(int(p["id"]))
    elif act == "add_depth":
        out = add_depth_point(p.get("property_id"), float(p["lat"]), float(p["lon"]), float(p["depth_ft"]), p.get("waterbody_name", "Camp Water"))
    elif act == "delete_depth":
        out = delete_depth_point(int(p["id"]))
    elif act == "save_contour_line":
        out = save_contour_line(p.get("property_id"), p.get("coordinates") or [], float(p.get("depth_ft", 10.0)), p.get("waterbody_name", "Camp Water"))
    elif act == "rebuild_bathymetry":
        out = rebuild_bathymetry(p.get("property_id"), p.get("waterbody_name"))
    elif act == "import_depth":
        out = import_depth_dataset(p.get("property_id"), p.get("filename", "sonar.csv"), p.get("file_content_b64", ""), p.get("waterbody_name", "Camp Water"))
    elif act == "sync_waterbodies":
        out = sync_waterbodies(
            p.get("property_id"), p.get("min_lon"), p.get("min_lat"), p.get("max_lon"), p.get("max_lat"),
            bool(p.get("fetch_dossiers", True)), bool(p.get("download_pdfs", True)), bool(p.get("force_refresh", False)),
        )
    elif act == "save_waterbody":
        out = save_waterbody(p)
    elif act == "delete_waterbody":
        out = delete_waterbody(int(p["id"]))
    elif act == "refresh_waterbody_dossier":
        out = refresh_waterbody_dossier(int(p["id"]))
    elif act == "get_waterbody_map":
        out = get_waterbody_map_preview(int(p["id"]))
    elif act == "save_waterbody_overlay":
        out = save_waterbody_overlay(
            int(p["id"]), p.get("bounds"), p.get("opacity"), p.get("visible"), p.get("control_points"),
            p.get("rotation_deg"), p.get("flip_h"), p.get("flip_v"), p.get("blend_mode"), p.get("crop"),
        )
    elif act == "unpin_waterbody_overlay":
        out = unpin_waterbody_overlay(int(p["id"]))
    elif act == "auto_align_chart":
        out = auto_align_waterbody_chart(int(p["id"]), p.get("crop"))
    elif act == "prepare_chart_tiles":
        out = prepare_chart_vision_tiles(
            p.get("property_id"), p.get("waterbody_id") or p.get("id"), p.get("waterbody_name"),
            bool(p.get("mask_noise", True)),
        )
    elif act == "clear_bathymetry":
        out = clear_bathymetry(p.get("property_id"), p.get("waterbody_name"), bool(p.get("clear_points", True)))
    elif act == "extract_depths_from_chart":
        out = extract_depths_from_chart(
            p.get("property_id"), p.get("waterbody_id"), p.get("waterbody_name"),
            p.get("samples"), p.get("contour_lines"), p.get("structures"),
            bool(p.get("clear_prior_vision", False)), str(p.get("coord_space") or "raw"),
        )
    elif act == "estimate_bathymetry":
        out = estimate_lake_bathymetry(p.get("property_id"), p.get("waterbody_id"), p.get("waterbody_name"), p.get("max_depth_ft"))
    else:
        out = analyze_fishing_spot(p.get("property_id"), p.get("wind_deg"), p.get("wind_mph"))
    print(json.dumps(out, indent=2))
