#!/usr/bin/env python3
"""Topographic Elevation, Contour Generator, Thermal Drainage Vectors & LiDAR/GIS Importer.

Provides:
  1. Keyless 30m DEM elevation sampling (Open-Meteo Elevation API + USGS EPQS fallback)
  2. Automatic GeoJSON elevation contour generation (via matplotlib Agg)
  3. Exact slope % and uphill/downhill thermal drainage aspect vectors (via numpy.gradient)
  4. Automatic topographic landmark detection (ridge high points, low drainage pools, saddles)
  5. User-defined topographic landmark management (water sources, ridges, saddles, benches, etc.)
  6. LiDAR / Topo dataset importer (.asc ESRI ASCII DEM, .csv elevation points, .geojson, .gpx, .kml, .kmz)
"""

import base64
import csv
import io
import json
import math
import os
import ssl
import sys
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.hunt_db import get_conn, get_property_bundle, now_iso
from tools.hunt_weather import deg_to_compass

M_TO_FT = 3.280839895

# 5-stop hypsometric terrain color ramp (low valley/creek -> mid slope -> high ridge)
_HYPSOMETRIC_STOPS: List[Tuple[float, Tuple[int, int, int]]] = [
    (0.00, (2, 132, 199)),    # #0284c7 deep basin / creek blue
    (0.25, (20, 184, 166)),   # #14b8a6 low draw teal
    (0.50, (132, 204, 22)),   # #84cc16 mid-slope lime
    (0.75, (245, 158, 11)),   # #f59e0b upper-slope amber
    (1.00, (244, 63, 94)),    # #f43f5e high ridge coral
]


def elevation_to_color(norm: float) -> str:
    """Map normalized elevation [0.0..1.0] to a hex color along the hypsometric ramp."""
    t = max(0.0, min(1.0, float(norm)))
    for idx in range(len(_HYPSOMETRIC_STOPS) - 1):
        t0, c0 = _HYPSOMETRIC_STOPS[idx]
        t1, c1 = _HYPSOMETRIC_STOPS[idx + 1]
        if t <= t1:
            f = (t - t0) / max(1e-6, t1 - t0)
            r = int(round(c0[0] + (c1[0] - c0[0]) * f))
            g = int(round(c0[1] + (c1[1] - c0[1]) * f))
            b = int(round(c0[2] + (c1[2] - c0[2]) * f))
            return f"#{r:02x}{g:02x}{b:02x}"
    c = _HYPSOMETRIC_STOPS[-1][1]
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def _property_bbox(bundle: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat) tightly focused around camp parcels & pins."""
    prop = bundle["property"]
    c_lat = float(prop["center_lat"])
    c_lon = float(prop["center_lon"])
    lons, lats = [c_lon], [c_lat]

    for w in bundle.get("waypoints") or []:
        lons.append(float(w["lon"]))
        lats.append(float(w["lat"]))

    for p in bundle.get("parcels") or []:
        g = p.get("geom") or {}
        coords = g.get("coordinates") or []
        if g.get("type") == "Polygon" and coords:
            for pt in coords[0]:
                if len(pt) >= 2:
                    lons.append(float(pt[0]))
                    lats.append(float(pt[1]))
        elif g.get("type") == "MultiPolygon" and coords:
            for poly in coords:
                if poly and poly[0]:
                    for pt in poly[0]:
                        if len(pt) >= 2:
                            lons.append(float(pt[0]))
                            lats.append(float(pt[1]))

    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    # Focused buffer (~250m minimum) so a 64x64 LiDAR grid yields ~10-18m sample spacing on your land
    pad_lat = max(0.0020, (max_lat - min_lat) * 0.20)
    pad_lon = max(0.0028, (max_lon - min_lon) * 0.20)
    min_lat = min(min_lat - pad_lat, c_lat - 0.0032)
    max_lat = max(max_lat + pad_lat, c_lat + 0.0032)
    min_lon = min(min_lon - pad_lon, c_lon - 0.0042)
    max_lon = max(max_lon + pad_lon, c_lon + 0.0042)
    # Clamp max span to ~6 km so fine LiDAR micro-terrain stays crisp
    if (max_lat - min_lat) > 0.054:
        min_lat, max_lat = c_lat - 0.027, c_lat + 0.027
    if (max_lon - min_lon) > 0.072:
        min_lon, max_lon = c_lon - 0.036, c_lon + 0.036
    return (min_lon, min_lat, max_lon, max_lat)


def _fetch_usgs_3dep_dem(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    grid_n: int = 64,
) -> Optional[Tuple[List[float], List[float], np.ndarray]]:
    """Fetch high-resolution 1m/10m USGS 3DEP LiDAR elevation raster (F32 GeoTIFF) in one call."""
    try:
        from PIL import Image

        n = max(16, min(96, int(grid_n)))
        bbox = f"{min_lon:.6f},{min_lat:.6f},{max_lon:.6f},{max_lat:.6f}"
        q = urllib.parse.urlencode({
            "bbox": bbox,
            "bboxSR": "4326",
            "size": f"{n},{n}",
            "imageSR": "4326",
            "format": "tiff",
            "pixelType": "F32",
            "noDataInterpretation": "esriNoDataMatchAny",
            "interpolation": "+RSP_BilinearInterpolation",
            "f": "image",
        })
        url = f"https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?{q}"
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "BlueLodge-HuntingBuddy/1.8"})
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            raw = resp.read()
        img = Image.open(io.BytesIO(raw))
        arr = np.array(img, dtype=np.float64)
        if arr.ndim != 2 or arr.shape[0] < 4 or arr.shape[1] < 4:
            return None
        valid_mask = np.isfinite(arr) & (arr > -500.0) & (arr < 9000.0)
        if int(np.sum(valid_mask)) < (arr.size * 0.7):
            return None
        med = float(np.median(arr[valid_mask]))
        arr = np.where(valid_mask, arr, med)
        # Raster row 0 is north (max_lat); flip vertically so row 0 corresponds to min_lat (south)
        arr = arr[::-1, :]
        lats_1d = list(np.linspace(min_lat, max_lat, arr.shape[0]))
        lons_1d = list(np.linspace(min_lon, max_lon, arr.shape[1]))
        return lats_1d, lons_1d, arr
    except Exception:
        return None


def _fetch_open_meteo_elevations(lats: List[float], lons: List[float]) -> List[float]:
    """Batch-query Open-Meteo Copernicus 30m DEM (up to 100 pts per call)."""
    ctx = ssl.create_default_context()
    out: List[float] = []
    chunk = 95
    for start in range(0, len(lats), chunk):
        la_str = ",".join(f"{x:.5f}" for x in lats[start : start + chunk])
        lo_str = ",".join(f"{x:.5f}" for x in lons[start : start + chunk])
        url = f"https://api.open-meteo.com/v1/elevation?latitude={la_str}&longitude={lo_str}"
        req = urllib.request.Request(url, headers={"User-Agent": "BlueLodge-HuntingBuddy/1.7"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        elevs = data.get("elevation") or []
        out.extend(float(v) if v is not None else 0.0 for v in elevs)
    return out


def _compute_grid_products(
    lats_1d: List[float],
    lons_1d: List[float],
    elev_m_2d: np.ndarray,
    contour_step_ft: float = 0.0,
) -> Dict[str, Any]:
    """Compute elevation stats, hypsometric contours (GeoJSON), thermal vectors, and auto landmarks."""
    rows, cols = elev_m_2d.shape
    elev_ft_raw = elev_m_2d * M_TO_FT

    # Gentle Gaussian smoothing on high-res LiDAR grids suppresses micro-clutter while preserving ridges/draws
    elev_m_work = elev_m_2d
    elev_ft = elev_ft_raw
    if rows >= 24 and cols >= 24:
        try:
            from scipy.ndimage import gaussian_filter
            elev_m_work = gaussian_filter(elev_m_2d, sigma=0.85, mode="nearest")
            elev_ft = elev_m_work * M_TO_FT
        except Exception:
            pass

    mean_lat = float(np.mean(lats_1d))
    dy_m = abs(lats_1d[1] - lats_1d[0]) * 111320.0 if rows > 1 else 100.0
    dx_m = abs(lons_1d[1] - lons_1d[0]) * 111320.0 * max(0.2, math.cos(math.radians(mean_lat))) if cols > 1 else 100.0
    if dy_m < 0.5:
        dy_m = 10.0
    if dx_m < 0.5:
        dx_m = 10.0
    resolution_m = round(float(max(dx_m, dy_m)), 1)

    # Gradient: dZ/dLat (northward) and dZ/dLon (eastward)
    dz_dy, dz_dx = np.gradient(elev_m_work, dy_m, dx_m)
    if rows > 1 and lats_1d[1] < lats_1d[0]:
        dz_dy = -dz_dy

    slope_mag = np.sqrt(dz_dx ** 2 + dz_dy ** 2)
    slope_pct = slope_mag * 100.0
    # Uphill compass bearing (0=N, 90=E)
    uphill_deg = (np.degrees(np.arctan2(dz_dx, dz_dy)) + 360.0) % 360.0
    downhill_deg = (uphill_deg + 180.0) % 360.0

    min_ft = float(np.min(elev_ft_raw))
    max_ft = float(np.max(elev_ft_raw))
    relief_ft = max_ft - min_ft
    span_ft = max(1.0, relief_ft)

    # 1. Generate Fine GeoJSON Elevation Contours with Hypsometric Color Gradient & Index Flags
    contours_fc: Dict[str, Any] = {"type": "FeatureCollection", "features": []}
    used_step_ft = 0.0
    if relief_ft >= 1.5:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            lon_grid, lat_grid = np.meshgrid(lons_1d, lats_1d)
            if contour_step_ft and float(contour_step_ft) >= 1.0:
                step_ft = float(contour_step_ft)
            else:
                step_ft = (
                    1.0 if relief_ft < 18
                    else (2.0 if relief_ft < 45 else (5.0 if relief_ft < 120 else (10.0 if relief_ft < 280 else 20.0)))
                )
            # Keep total contour levels bounded (<= 36) so Leaflet remains snappy even with tight intervals
            while relief_ft / step_ft > 36.0:
                step_ft = step_ft * 2.0
            used_step_ft = round(step_ft, 1)

            lvl_start = math.floor(min_ft / step_ft) * step_ft + step_ft
            lvl_end = math.ceil(max_ft / step_ft) * step_ft
            levels = np.arange(lvl_start, lvl_end, step_ft)
            if len(levels) >= 1:
                index_mod = step_ft * (5.0 if step_ft in (1.0, 2.0, 10.0, 20.0) else 4.0)
                fig, ax = plt.subplots()
                cs = ax.contour(lon_grid, lat_grid, elev_ft, levels=levels)
                allsegs = getattr(cs, "allsegs", None)
                if allsegs is not None:
                    for lvl_val, seg_list in zip(cs.levels, allsegs):
                        elev_val = round(float(lvl_val), 1)
                        norm = max(0.0, min(1.0, (elev_val - min_ft) / span_ft))
                        col = elevation_to_color(norm)
                        is_index = abs((elev_val / index_mod) - round(elev_val / index_mod)) < 0.02
                        for seg in seg_list:
                            if len(seg) < 2:
                                continue
                            # Decimate very dense polyline vertices slightly (keep every point for short lines)
                            step_pt = 1 if len(seg) <= 45 else max(1, len(seg) // 45)
                            pts = seg[::step_pt]
                            if not np.array_equal(pts[-1], seg[-1]):
                                pts = np.vstack([pts, seg[-1]])
                            coords = [[round(float(pt[0]), 6), round(float(pt[1]), 6)] for pt in pts]
                            contours_fc["features"].append({
                                "type": "Feature",
                                "properties": {
                                    "elevation_ft": int(round(elev_val)) if abs(elev_val - round(elev_val)) < 0.05 else elev_val,
                                    "elev_norm": round(norm, 3),
                                    "is_index": bool(is_index),
                                    "color": col,
                                    "kind": "contour",
                                },
                                "geometry": {"type": "LineString", "coordinates": coords},
                            })
                plt.close(fig)
        except Exception:
            pass

    # 2. Sample Thermal Flow Vectors (clean ~6x6 grid of uphill/downhill arrows across camp)
    thermal_vectors: List[Dict[str, Any]] = []
    step_r = max(1, (rows - 2) // 6)
    step_c = max(1, (cols - 2) // 6)
    r_start = max(1, step_r // 2)
    c_start = max(1, step_c // 2)
    for r in range(r_start, rows - 1, step_r):
        for c in range(c_start, cols - 1, step_c):
            sp = float(slope_pct[r, c])
            if sp < 0.35 and relief_ft > 5.0:
                continue
            thermal_vectors.append({
                "lat": round(float(lats_1d[r]), 6),
                "lon": round(float(lons_1d[c]), 6),
                "elevation_ft": round(float(elev_ft[r, c]), 1),
                "slope_pct": round(sp, 1),
                "uphill_deg": round(float(uphill_deg[r, c]), 1),
                "downhill_deg": round(float(downhill_deg[r, c]), 1),
                "downhill_compass": deg_to_compass(float(downhill_deg[r, c])),
            })

    # 3. Auto-detect Key Topographic Landmarks (Peak Ridge, Low Thermal Pool, Terrain Saddle)
    auto_landmarks: List[Dict[str, Any]] = []
    max_idx = np.unravel_index(int(np.argmax(elev_ft)), elev_ft.shape)
    min_idx = np.unravel_index(int(np.argmin(elev_ft)), elev_ft.shape)
    auto_landmarks.append({
        "name": f"High Ridge ({round(max_ft)} ft)",
        "kind": "ridge",
        "lat": round(float(lats_1d[max_idx[0]]), 6),
        "lon": round(float(lons_1d[max_idx[1]]), 6),
        "elevation_ft": round(max_ft, 1),
        "notes": f"Highest terrain point on camp (+{round(relief_ft)} ft relief). Morning thermals pull scent up toward this ridge; strong evening vantage.",
    })
    auto_landmarks.append({
        "name": f"Low Drainage Basin ({round(min_ft)} ft)",
        "kind": "drainage",
        "lat": round(float(lats_1d[min_idx[0]]), 6),
        "lon": round(float(lons_1d[min_idx[1]]), 6),
        "elevation_ft": round(min_ft, 1),
        "notes": "Lowest drainage point on camp. Evening katabatic cooling pools dense air & human scent into this bottom.",
    })

    # Saddle detection using wider stride on high-res grids so true ridge saddles (not pixel noise) win
    stride = max(1, min(rows, cols) // 12)
    best_saddle = None
    best_saddle_score = 0.0
    for r in range(stride, rows - stride):
        for c in range(stride, cols - stride):
            z = float(elev_ft[r, c])
            curv_y = (float(elev_ft[r - stride, c]) + float(elev_ft[r + stride, c])) * 0.5 - z
            curv_x = (float(elev_ft[r, c - stride]) + float(elev_ft[r, c + stride])) * 0.5 - z
            if curv_x * curv_y < -0.4:
                s_score = abs(curv_x * curv_y)
                if s_score > best_saddle_score:
                    best_saddle_score = s_score
                    best_saddle = (r, c, z)
    if best_saddle:
        sr, sc, sz = best_saddle
        auto_landmarks.append({
            "name": f"Terrain Saddle ({round(sz)} ft)",
            "kind": "saddle",
            "lat": round(float(lats_1d[sr]), 6),
            "lon": round(float(lons_1d[sc]), 6),
            "elevation_ft": round(sz, 1),
            "notes": "Natural topographic saddle/pinch point between higher terrain. Classic low-energy whitetail travel funnel.",
        })

    # Compact grid_meta (<= 32x32) for fast bilinear point interpolation in SQLite
    save_rows, save_cols = rows, cols
    save_lats, save_lons, save_arr = lats_1d, lons_1d, elev_m_work
    if rows > 32 or cols > 32:
        r_idx = np.linspace(0, rows - 1, min(32, rows)).astype(int)
        c_idx = np.linspace(0, cols - 1, min(32, cols)).astype(int)
        save_arr = elev_m_work[np.ix_(r_idx, c_idx)]
        save_lats = [lats_1d[i] for i in r_idx]
        save_lons = [lons_1d[j] for j in c_idx]
        save_rows, save_cols = save_arr.shape

    return {
        "min_elevation_ft": round(min_ft, 1),
        "max_elevation_ft": round(max_ft, 1),
        "relief_ft": round(relief_ft, 1),
        "contour_step_ft": used_step_ft,
        "resolution_m": resolution_m,
        "avg_slope_pct": round(float(np.mean(slope_pct)), 1),
        "max_slope_pct": round(float(np.max(slope_pct)), 1),
        "dominant_downhill_deg": round(float(np.degrees(np.arctan2(np.mean(np.sin(np.radians(downhill_deg))), np.mean(np.cos(np.radians(downhill_deg))))) + 360.0) % 360.0, 1),
        "contours": contours_fc,
        "thermal_vectors": thermal_vectors,
        "auto_landmarks": auto_landmarks,
        "grid_meta": {
            "rows": save_rows,
            "cols": save_cols,
            "lats": [round(float(x), 6) for x in save_lats],
            "lons": [round(float(x), 6) for x in save_lons],
            "elev_m": [[round(float(v), 2) for v in row] for row in save_arr],
        },
    }


def _interpolate_point_from_grid(grid_meta: Dict[str, Any], lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Bilinearly interpolate elevation_ft, slope_pct, uphill_deg, downhill_deg at (lat, lon)."""
    lats = grid_meta.get("lats") or []
    lons = grid_meta.get("lons") or []
    elev_m = grid_meta.get("elev_m") or []
    if len(lats) < 2 or len(lons) < 2 or not elev_m:
        return None
    arr = np.array(elev_m, dtype=float)
    lats_a = np.array(lats, dtype=float)
    lons_a = np.array(lons, dtype=float)
    # Handle descending lat order if present
    if lats_a[0] > lats_a[-1]:
        lats_a = lats_a[::-1]
        arr = arr[::-1, :]
    if lons_a[0] > lons_a[-1]:
        lons_a = lons_a[::-1]
        arr = arr[:, ::-1]

    r_f = float(np.interp(lat, lats_a, np.arange(len(lats_a))))
    c_f = float(np.interp(lon, lons_a, np.arange(len(lons_a))))
    r0 = max(0, min(len(lats_a) - 2, int(math.floor(r_f))))
    c0 = max(0, min(len(lons_a) - 2, int(math.floor(c_f))))
    dr = max(0.0, min(1.0, r_f - r0))
    dc = max(0.0, min(1.0, c_f - c0))

    z00, z01 = arr[r0, c0], arr[r0, c0 + 1]
    z10, z11 = arr[r0 + 1, c0], arr[r0 + 1, c0 + 1]
    z_m = (1 - dr) * (1 - dc) * z00 + (1 - dr) * dc * z01 + dr * (1 - dc) * z10 + dr * dc * z11

    dy_m = max(1.0, abs(lats_a[r0 + 1] - lats_a[r0]) * 111320.0)
    dx_m = max(1.0, abs(lons_a[c0 + 1] - lons_a[c0]) * 111320.0 * max(0.2, math.cos(math.radians(lat))))
    dz_dy = ((z10 - z00) * (1 - dc) + (z11 - z01) * dc) / dy_m
    dz_dx = ((z01 - z00) * (1 - dr) + (z11 - z10) * dr) / dx_m
    slope_pct = math.hypot(dz_dx, dz_dy) * 100.0
    uphill_deg = (math.degrees(math.atan2(dz_dx, dz_dy)) + 360.0) % 360.0
    downhill_deg = (uphill_deg + 180.0) % 360.0

    return {
        "elevation_ft": round(float(z_m * M_TO_FT), 1),
        "slope_pct": round(float(slope_pct), 1),
        "uphill_deg": round(float(uphill_deg), 1),
        "uphill_compass": deg_to_compass(uphill_deg),
        "downhill_deg": round(float(downhill_deg), 1),
        "downhill_compass": deg_to_compass(downhill_deg),
    }


def sample_point_terrain(property_id: int, lat: float, lon: float) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT grid_json FROM terrain_grids WHERE property_id = ? ORDER BY updated_at DESC LIMIT 1",
        (int(property_id),),
    ).fetchone()
    if not row:
        return None
    try:
        g = json.loads(row["grid_json"])
    except Exception:
        return None
    return _interpolate_point_from_grid(g, lat, lon)


def get_terrain_summary(property_id: Optional[int] = None) -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return {"status": "error", "message": "No camp loaded."}
    pid = bundle["property"]["id"]
    row = conn.execute(
        "SELECT * FROM terrain_grids WHERE property_id = ? ORDER BY updated_at DESC LIMIT 1",
        (pid,),
    ).fetchone()
    landmarks = [dict(r) for r in conn.execute("SELECT * FROM topo_landmarks WHERE property_id = ? ORDER BY id DESC", (pid,)).fetchall()]
    for lm in landmarks:
        try:
            lm["geom"] = json.loads(lm["geom_geojson"])
        except Exception:
            lm["geom"] = None

    if not row:
        return {
            "status": "ok",
            "property_id": pid,
            "has_dem": False,
            "landmarks": landmarks,
            "message": "No elevation grid fetched yet. Click 'Fetch Topo/DEM' or upload a LiDAR/topo dataset.",
        }

    summary = json.loads(row["summary_json"] or "{}")
    contours = json.loads(row["contours_geojson"] or '{"type":"FeatureCollection","features":[]}')
    vectors = json.loads(row["thermal_vectors_json"] or "[]")
    return {
        "status": "ok",
        "property_id": pid,
        "has_dem": True,
        "source": row["source"],
        "updated_at": row["updated_at"],
        "min_elevation_ft": summary.get("min_elevation_ft"),
        "max_elevation_ft": summary.get("max_elevation_ft"),
        "relief_ft": summary.get("relief_ft"),
        "contour_step_ft": summary.get("contour_step_ft"),
        "resolution_m": summary.get("resolution_m"),
        "avg_slope_pct": summary.get("avg_slope_pct"),
        "max_slope_pct": summary.get("max_slope_pct"),
        "dominant_downhill_deg": summary.get("dominant_downhill_deg"),
        "dominant_downhill_compass": deg_to_compass(float(summary.get("dominant_downhill_deg", 180.0))),
        "contours": contours,
        "thermal_vectors": vectors,
        "landmarks": landmarks,
    }


def _save_terrain_products(conn, pid: int, source: str, products: Dict[str, Any], seed_auto_landmarks: bool = True) -> None:
    ts = now_iso()
    summary = {
        "min_elevation_ft": products["min_elevation_ft"],
        "max_elevation_ft": products["max_elevation_ft"],
        "relief_ft": products["relief_ft"],
        "contour_step_ft": products.get("contour_step_ft"),
        "resolution_m": products.get("resolution_m"),
        "avg_slope_pct": products["avg_slope_pct"],
        "max_slope_pct": products["max_slope_pct"],
        "dominant_downhill_deg": products["dominant_downhill_deg"],
    }
    conn.execute("DELETE FROM terrain_grids WHERE property_id = ?", (pid,))
    conn.execute(
        """INSERT INTO terrain_grids (property_id, source, summary_json, grid_json, contours_geojson, thermal_vectors_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            source,
            json.dumps(summary),
            json.dumps(products["grid_meta"]),
            json.dumps(products["contours"]),
            json.dumps(products["thermal_vectors"]),
            ts,
        ),
    )
    if seed_auto_landmarks:
        conn.execute("DELETE FROM topo_landmarks WHERE property_id = ? AND source = 'auto_dem'", (pid,))
        for lm in products.get("auto_landmarks") or []:
            geom = {"type": "Point", "coordinates": [lm["lon"], lm["lat"]]}
            conn.execute(
                """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
                   VALUES (?, ?, ?, 'Point', ?, ?, 'auto_dem', ?, ?)""",
                (pid, lm["name"], lm["kind"], json.dumps(geom), lm.get("elevation_ft"), lm.get("notes", ""), ts),
            )
    conn.commit()


def fetch_property_dem(
    property_id: Optional[int] = None,
    grid_n: int = 64,
    contour_step_ft: float = 0.0,
) -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return {"status": "error", "message": "No camp loaded. Create or select a camp first."}
    pid = bundle["property"]["id"]
    min_lon, min_lat, max_lon, max_lat = _property_bbox(bundle)

    # Resolve elevation through the Pluggable Sovereign Spatial Provider Cascade
    # (User Custom LiDAR -> USGS 3DEP 1m LiDAR -> Open-Meteo Copernicus 30m Global)
    from adapters.registry import fetch_elevation_cascade

    try:
        source_tag, lats_1d, lons_1d, elev_m_2d = fetch_elevation_cascade(
            (min_lon, min_lat, max_lon, max_lat),
            grid_n=max(32, min(96, int(grid_n))),
        )
    except Exception as exc:
        return {"status": "error", "message": f"Elevation fetch failed: {exc}"}

    products = _compute_grid_products(lats_1d, lons_1d, elev_m_2d, contour_step_ft=float(contour_step_ft or 0.0))
    _save_terrain_products(conn, pid, source_tag, products, seed_auto_landmarks=True)
    return get_terrain_summary(pid)


def add_topo_landmark(
    property_id: Optional[int],
    name: str,
    kind: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    geom: Optional[Dict[str, Any]] = None,
    elevation_ft: Optional[float] = None,
    notes: str = "",
    source: str = "user",
) -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return {"status": "error", "message": "No camp loaded."}
    pid = bundle["property"]["id"]
    if geom is None:
        if lat is None or lon is None:
            return {"status": "error", "message": "Provide lat/lon or GeoJSON geometry."}
        geom = {"type": "Point", "coordinates": [float(lon), float(lat)]}
    gtype = geom.get("type", "Point")
    if elevation_ft is None and lat is not None and lon is not None:
        pt_t = sample_point_terrain(pid, float(lat), float(lon))
        if pt_t:
            elevation_ft = pt_t["elevation_ft"]

    cur = conn.execute(
        """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (pid, name or kind.replace("_", " ").title(), kind, gtype, json.dumps(geom), elevation_ft, source, notes, now_iso()),
    )
    conn.commit()
    return {"status": "ok", "id": cur.lastrowid, "property_id": pid, "elevation_ft": elevation_ft}


def delete_topo_landmark(landmark_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM topo_landmarks WHERE id = ?", (int(landmark_id),))
    conn.commit()
    return {"status": "ok", "deleted": int(landmark_id)}


def _parse_esri_ascii_dem(text: str) -> Tuple[List[float], List[float], np.ndarray]:
    """Parse ESRI ASCII Grid (.asc) LiDAR/DEM raster into (lats, lons, elev_m_2d)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    hdr: Dict[str, float] = {}
    data_start = 0
    for idx, ln in enumerate(lines[:10]):
        parts = ln.split()
        if len(parts) == 2 and parts[0].lower() in ("ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value"):
            hdr[parts[0].lower()] = float(parts[1])
            data_start = idx + 1
        else:
            break
    ncols = int(hdr.get("ncols", 0))
    nrows = int(hdr.get("nrows", 0))
    cell = float(hdr.get("cellsize", 0.0001))
    x0 = hdr.get("xllcorner", hdr.get("xllcenter", 0.0))
    y0 = hdr.get("yllcorner", hdr.get("yllcenter", 0.0))
    nodata = hdr.get("nodata_value", -9999.0)
    if ncols < 2 or nrows < 2:
        raise ValueError("Invalid ESRI ASCII DEM header (need ncols/nrows >= 2)")

    vals = []
    for ln in lines[data_start:]:
        vals.extend(float(x) for x in ln.split())
    arr = np.array(vals[: nrows * ncols], dtype=float).reshape((nrows, ncols))
    valid = arr[arr != nodata]
    fill = float(np.median(valid)) if len(valid) else 0.0
    arr[arr == nodata] = fill
    # ASCII grid row 0 is north (top); flip to ascending lat
    arr = arr[::-1, :]
    lats = [y0 + i * cell for i in range(nrows)]
    lons = [x0 + j * cell for j in range(ncols)]
    # Downsample huge LiDAR rasters to crisp 24x24 grid for fast browser Leaflet contour rendering
    if nrows > 24 or ncols > 24:
        r_idx = np.linspace(0, nrows - 1, min(24, nrows)).astype(int)
        c_idx = np.linspace(0, ncols - 1, min(24, ncols)).astype(int)
        arr = arr[np.ix_(r_idx, c_idx)]
        lats = [lats[i] for i in r_idx]
        lons = [lons[j] for j in c_idx]
    return lats, lons, arr


def _parse_csv_elevation_points(text: str) -> Tuple[List[float], List[float], np.ndarray]:
    """Parse CSV elevation point cloud (lat, lon, elev_m or elev_ft) onto a regular grid."""
    reader = csv.DictReader(io.StringIO(text))
    pts_lat, pts_lon, pts_z = [], [], []
    is_feet = False
    if reader.fieldnames:
        low_fields = {f.lower().strip(): f for f in reader.fieldnames}
        lat_k = low_fields.get("lat") or low_fields.get("latitude") or low_fields.get("y")
        lon_k = low_fields.get("lon") or low_fields.get("lng") or low_fields.get("longitude") or low_fields.get("x")
        z_k = low_fields.get("elev_m") or low_fields.get("elevation_m") or low_fields.get("elevation") or low_fields.get("z") or low_fields.get("elev_ft") or low_fields.get("elevation_ft")
        if z_k and "ft" in z_k.lower():
            is_feet = True
        if not (lat_k and lon_k and z_k):
            raise ValueError("CSV requires lat, lon, and elevation (elev_m / elev_ft / z) columns.")
        for row in reader:
            try:
                la = float(row[lat_k])
                lo = float(row[lon_k])
                zv = float(row[z_k])
                if is_feet:
                    zv = zv / M_TO_FT
                pts_lat.append(la)
                pts_lon.append(lo)
                pts_z.append(zv)
            except Exception:
                continue
    if len(pts_z) < 4:
        raise ValueError("CSV needs at least 4 valid elevation points.")

    from scipy.interpolate import griddata
    grid_n = 16
    lats_1d = list(np.linspace(min(pts_lat), max(pts_lat), grid_n))
    lons_1d = list(np.linspace(min(pts_lon), max(pts_lon), grid_n))
    glon, glat = np.meshgrid(lons_1d, lats_1d)
    z_grid = griddata((pts_lon, pts_lat), pts_z, (glon, glat), method="linear")
    z_near = griddata((pts_lon, pts_lat), pts_z, (glon, glat), method="nearest")
    z_grid = np.where(np.isnan(z_grid), z_near, z_grid)
    return lats_1d, lons_1d, z_grid


def import_topo_dataset(
    property_id: Optional[int],
    filename: str,
    file_content_b64: str,
    default_kind: str = "water_source",
) -> Dict[str, Any]:
    """Import LiDAR/DEM (.asc, .csv) or topo landmark overlays (.geojson, .gpx, .kml, .kmz)."""
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id)
    if not bundle:
        return {"status": "error", "message": "No camp loaded. Create a camp first."}
    pid = bundle["property"]["id"]
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    raw_bytes = base64.b64decode(file_content_b64)

    if ext in ("asc", "grd"):
        lats, lons, elev_m = _parse_esri_ascii_dem(raw_bytes.decode("utf-8", errors="replace"))
        products = _compute_grid_products(lats, lons, elev_m)
        _save_terrain_products(conn, pid, f"lidar_asc:{os.path.basename(filename)}", products, seed_auto_landmarks=True)
        res = get_terrain_summary(pid)
        res["imported_type"] = "dem_grid"
        res["filename"] = filename
        return res

    if ext == "csv":
        lats, lons, elev_m = _parse_csv_elevation_points(raw_bytes.decode("utf-8", errors="replace"))
        products = _compute_grid_products(lats, lons, elev_m)
        _save_terrain_products(conn, pid, f"lidar_csv:{os.path.basename(filename)}", products, seed_auto_landmarks=True)
        res = get_terrain_summary(pid)
        res["imported_type"] = "dem_grid"
        res["filename"] = filename
        return res

    # Vector topo/hydrography datasets (.geojson, .json, .gpx, .kml, .kmz)
    from tools.hunt_import import parse_gpx, parse_kml

    text = ""
    if ext == "kmz":
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
            kmls = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if kmls:
                text = zf.read(kmls[0]).decode("utf-8", errors="replace")
                ext = "kml"
    else:
        text = raw_bytes.decode("utf-8", errors="replace")

    added = 0
    ts = now_iso()

    def _infer_kind(name_txt: str, props: Dict[str, Any]) -> str:
        raw_k = str(props.get("kind") or props.get("type") or "").lower()
        if raw_k in ("water_source", "ridge", "saddle", "drainage", "bench", "food_plot", "thick_cover", "access_gate", "contour"):
            return raw_k
        low = (name_txt + " " + json.dumps(props)).lower()
        if any(w in low for w in ("creek", "river", "pond", "water", "stream", "spring", "lake", "marsh", "wetland")):
            return "water_source"
        if any(w in low for w in ("saddle", "pinch", "funnel", "pass")):
            return "saddle"
        if any(w in low for w in ("ridge", "peak", "knob", "hill", "summit")):
            return "ridge"
        if any(w in low for w in ("draw", "drain", "valley", "ravine", "bottom", "hollow")):
            return "drainage"
        if any(w in low for w in ("bench", "terrace", "shelf")):
            return "bench"
        if any(w in low for w in ("contour", "elev")):
            return "contour"
        if any(w in low for w in ("swamp", "cedar", "thicket", "cover", "bed")):
            return "thick_cover"
        if any(w in low for w in ("field", "oak", "food", "crop", "corn", "bean", "clover")):
            return "food_plot"
        return default_kind

    if ext in ("geojson", "json"):
        gj = json.loads(text)
        feats = gj.get("features") if isinstance(gj, dict) and gj.get("type") == "FeatureCollection" else [gj]
        for f in (feats or [])[:250]:
            g = f.get("geometry") or {}
            pr = f.get("properties") or {}
            if not g.get("type"):
                continue
            nm = str(pr.get("name") or pr.get("label") or pr.get("GNIS_NAME") or f"{default_kind.replace('_', ' ').title()} {added + 1}")
            k = _infer_kind(nm, pr)
            elev = pr.get("elevation_ft") or pr.get("elev_ft") or pr.get("ELEV") or pr.get("CONTOUR")
            try:
                elev = float(elev) if elev is not None else None
            except Exception:
                elev = None
            conn.execute(
                """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, nm, k, g["type"], json.dumps(g), elev, f"upload:{os.path.basename(filename)}", str(pr.get("notes") or pr.get("description") or "")[:240], ts),
            )
            added += 1
    elif ext == "gpx":
        wpts, routes = parse_gpx(text)
        for w in wpts[:150]:
            k = _infer_kind(w["name"], w)
            g = {"type": "Point", "coordinates": [w["lon"], w["lat"]]}
            conn.execute(
                """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
                   VALUES (?, ?, ?, 'Point', ?, NULL, ?, ?, ?)""",
                (pid, w["name"], k, json.dumps(g), f"upload:{os.path.basename(filename)}", w.get("notes", ""), ts),
            )
            added += 1
        for r in routes[:50]:
            k = _infer_kind(r["name"], r)
            conn.execute(
                """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
                   VALUES (?, ?, ?, 'LineString', ?, NULL, ?, '', ?)""",
                (pid, r["name"], k, json.dumps(r["geom"]), f"upload:{os.path.basename(filename)}", ts),
            )
            added += 1
    elif ext == "kml":
        wpts, routes, polys = parse_kml(text)
        for w in wpts[:150]:
            k = _infer_kind(w["name"], w)
            g = {"type": "Point", "coordinates": [w["lon"], w["lat"]]}
            conn.execute(
                """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
                   VALUES (?, ?, ?, 'Point', ?, NULL, ?, ?, ?)""",
                (pid, w["name"], k, json.dumps(g), f"upload:{os.path.basename(filename)}", w.get("notes", ""), ts),
            )
            added += 1
        for r in routes[:50]:
            k = _infer_kind(r["name"], r)
            conn.execute(
                """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
                   VALUES (?, ?, ?, 'LineString', ?, NULL, ?, '', ?)""",
                (pid, r["name"], k, json.dumps(r["geom"]), f"upload:{os.path.basename(filename)}", ts),
            )
            added += 1
        for p in polys[:50]:
            k = _infer_kind(p.get("owner_name", ""), p)
            conn.execute(
                """INSERT INTO topo_landmarks (property_id, name, kind, geom_type, geom_geojson, elevation_ft, source, notes, created_at)
                   VALUES (?, ?, ?, 'Polygon', ?, NULL, ?, ?, ?)""",
                (pid, p.get("owner_name", "Topo Feature"), k, json.dumps(p["geom"]), f"upload:{os.path.basename(filename)}", p.get("plss_desc", ""), ts),
            )
            added += 1
    else:
        return {"status": "error", "message": f"Unsupported topo format '.{ext}'. Use .asc, .csv, .geojson, .gpx, .kml, or .kmz."}

    conn.commit()
    res = get_terrain_summary(pid)
    res["imported_type"] = "vector_landmarks"
    res["landmarks_added"] = added
    res["filename"] = filename
    return res


if __name__ == "__main__":
    p = json.loads(sys.stdin.read() or "{}")
    act = p.get("action", "summary")
    if act == "fetch_dem":
        out = fetch_property_dem(
            property_id=p.get("property_id"),
            grid_n=int(p.get("grid_n", 64)),
            contour_step_ft=float(p.get("contour_step_ft") or 0.0),
        )
    elif act == "add_landmark":
        out = add_topo_landmark(
            property_id=p.get("property_id"),
            name=p.get("name", ""),
            kind=p.get("kind", "water_source"),
            lat=p.get("lat"),
            lon=p.get("lon"),
            geom=p.get("geom"),
            elevation_ft=p.get("elevation_ft"),
            notes=p.get("notes", ""),
        )
    elif act == "delete_landmark":
        out = delete_topo_landmark(int(p["id"]))
    elif act == "import_dataset":
        out = import_topo_dataset(
            property_id=p.get("property_id"),
            filename=p.get("filename", "topo.geojson"),
            file_content_b64=p.get("file_content_b64", ""),
            default_kind=p.get("default_kind", "water_source"),
        )
    else:
        out = get_terrain_summary(property_id=p.get("property_id"))
    print(json.dumps(out, indent=2))
