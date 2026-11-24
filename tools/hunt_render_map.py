#!/usr/bin/env python3
"""High-DPI Georeferenced Tactical Hunting Map Renderer.

Renders the exact Hunting Buddy tactical map stack into a crisp PNG ready for Discord or offline print:
  - Esri World Imagery + Reference Labels (hybrid default) or USGS Aerial / USGS Topo tiles (disk-cached)
  - Property parcels & owner labels
  - USGS 3DEP 1m LiDAR hypsometric elevation-gradient contours (blue -> teal -> lime -> amber -> coral)
  - Thermal / slope drainage flow arrows (orange downhill in katabatic, teal uphill in upslope)
  - Topographic & water landmarks (ponds, creeks, ridges, saddles, benches)
  - Wind & thermal-blended scent cones (colorblind-safe vermilion)
  - Stands, blinds, trail cameras, bedding pins, removable plan annotations & live GPS hunters
  - Scale bar, compass rose & elevation gradient legend
"""

import io
import json
import math
import os
import shutil
import ssl
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon as MplPolygon, Rectangle
import numpy as np
from PIL import Image, ImageEnhance
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.hunt_db import DEFAULT_DATA_DIR, get_conn, get_property_bundle, now_iso
from tools.hunt_plan import evaluate_hunt_plan, scent_cone_polygon_m
from tools.hunt_topo import elevation_to_color, sample_point_terrain
from tools.hunt_weather import deg_to_compass

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_TO_LL = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _deg2tile(lat: float, lon: float, z: int) -> Tuple[int, int]:
    lat_rad = math.radians(lat)
    n = 2.0 ** z
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def _tile2lonlat(x: int, y: int, z: int) -> Tuple[float, float]:
    n = 2.0 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lon, lat


def _load_or_fetch_tile(layer: str, z: int, x: int, y: int, mode: str = "RGB") -> Optional[Image.Image]:
    cache_path = os.path.join(DEFAULT_DATA_DIR, "tile_cache", layer, str(z), str(x), f"{y}.tile")
    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
        try:
            return Image.open(cache_path).convert(mode)
        except Exception:
            pass
    urls = {
        "esri_imagery": f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "esri_labels": f"https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        "usgs_aerial": f"https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "usgs_topo": f"https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        "usgs_hybrid": f"https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}",
    }
    url = urls.get(layer, urls["esri_imagery"])
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "BlueLodge-HuntingBuddy/1.9"})
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            raw = r.read()
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(raw)
        return Image.open(io.BytesIO(raw)).convert(mode)
    except Exception:
        return None


def _draw_basemap_tiles(ax, basemap: str, min_x: float, max_x: float, min_y: float, max_y: float) -> Tuple[int, str]:
    """Stitch disk-cached satellite/topo tiles across viewport at crisp adaptive zoom (<= 49 tiles)."""
    lo1, la1 = _TO_LL.transform(min_x, min_y)
    lo2, la2 = _TO_LL.transform(max_x, max_y)

    # Pick highest crisp zoom (16 down to 12) where tile grid stays <= 49 tiles
    z = 15
    for cand_z in (16, 15, 14, 13, 12):
        tx0, ty1 = _deg2tile(la1, lo1, cand_z)
        tx1, ty0 = _deg2tile(la2, lo2, cand_z)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= 49:
            z = cand_z
            break
    x0, y1 = _deg2tile(la1, lo1, z)
    x1, y0 = _deg2tile(la2, lo2, z)

    bm = (basemap or "hybrid").strip().lower()
    if bm in ("hybrid", "esri_hybrid", "esri_imagery"):
        base_layer = "esri_imagery"
        overlay_layer = "esri_labels" if bm != "esri_imagery" else None
        source_lbl = f"Esri World Imagery Hybrid (z{z})"
    elif bm == "usgs_topo":
        base_layer = "usgs_topo"
        overlay_layer = None
        source_lbl = f"USGS Topo National Map (z{z})"
    else:
        base_layer = "usgs_aerial"
        overlay_layer = None
        source_lbl = f"USGS Aerial Imagery (z{z})"

    drawn = 0
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            tile = _load_or_fetch_tile(base_layer, z, tx, ty, mode="RGB")
            if tile is None and base_layer == "esri_imagery":
                tile = _load_or_fetch_tile("usgs_aerial", z, tx, ty, mode="RGB")
            if tile is not None:
                if base_layer != "usgs_topo":
                    tile = ImageEnhance.Brightness(tile).enhance(1.06)
                    tile = ImageEnhance.Contrast(tile).enhance(1.08)
                tl_lo, tl_la = _tile2lonlat(tx, ty, z)
                br_lo, br_la = _tile2lonlat(tx + 1, ty + 1, z)
                mx0, my1 = _TO_M.transform(tl_lo, tl_la)
                mx1, my0 = _TO_M.transform(br_lo, br_la)
                ax.imshow(np.asarray(tile), extent=[mx0, mx1, my0, my1], zorder=1)
                drawn += 1
                if overlay_layer:
                    lbl_tile = _load_or_fetch_tile(overlay_layer, z, tx, ty, mode="RGBA")
                    if lbl_tile is not None:
                        ax.imshow(np.asarray(lbl_tile), extent=[mx0, mx1, my0, my1], zorder=2, alpha=0.88)
    return z, (source_lbl if drawn > 0 else f"Vector Fallback (z{z})")


def render_tactical_map(
    property_id: Optional[int] = None,
    wind_deg: float = 315.0,
    wind_mph: float = 9.0,
    thermal_mode: str = "neutral",
    basemap: str = "hybrid",
    scenario_name: str = "Tactical Sit Plan",
    channel_id: str = "",
    view_bbox: Optional[Any] = None,
    **_extra: Any,
) -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id, channel_id=channel_id)
    if not bundle:
        return {"status": "error", "message": "Property not found"}

    prop = bundle["property"]
    pid = int(prop["id"])
    plan = evaluate_hunt_plan(pid, wind_deg, wind_mph, thermal_mode, channel_id)
    cx, cy = _TO_M.transform(prop["center_lon"], prop["center_lat"])

    # If the Web UI or workflow supplied an active map viewport bbox [min_lon, min_lat, max_lon, max_lat], frame it directly
    parsed_vbbox = None
    if isinstance(view_bbox, str) and view_bbox.strip().startswith("["):
        try:
            view_bbox = json.loads(view_bbox)
        except Exception:
            view_bbox = None
    if isinstance(view_bbox, (list, tuple)) and len(view_bbox) == 4:
        try:
            v_lo0, v_la0, v_lo1, v_la1 = [float(v) for v in view_bbox]
            if v_lo1 > v_lo0 and v_la1 > v_la0 and (v_lo1 - v_lo0) < 0.6 and (v_la1 - v_la0) < 0.6:
                vx0, vy0 = _TO_M.transform(v_lo0, v_la0)
                vx1, vy1 = _TO_M.transform(v_lo1, v_la1)
                parsed_vbbox = (min(vx0, vx1), max(vx0, vx1), min(vy0, vy1), max(vy0, vy1))
        except Exception:
            parsed_vbbox = None

    if parsed_vbbox is not None:
        min_x, max_x, min_y, max_y = parsed_vbbox
    else:
        # Compute metric viewport covering parcels, waypoints, waterbodies, topo landmarks & annotations
        xs: List[float] = [cx]
        ys: List[float] = [cy]
        for w in bundle.get("waypoints") or []:
            wx, wy = _TO_M.transform(w["lon"], w["lat"])
            xs.append(wx); ys.append(wy)
        for h in bundle.get("hunters") or []:
            hx, hy = _TO_M.transform(h["lon"], h["lat"])
            xs.append(hx); ys.append(hy)
        for lm in bundle.get("topo_landmarks") or []:
            g = lm.get("geom") or {}
            if g.get("type") == "Point" and len(g.get("coordinates") or []) >= 2:
                lx, ly = _TO_M.transform(g["coordinates"][0], g["coordinates"][1])
                xs.append(lx); ys.append(ly)
        for p in bundle.get("parcels") or []:
            g = p.get("geom") or {}
            if g.get("type") == "Polygon" and g.get("coordinates"):
                for c in g["coordinates"][0]:
                    px, py = _TO_M.transform(c[0], c[1])
                    xs.append(px); ys.append(py)
            elif g.get("type") == "MultiPolygon" and g.get("coordinates"):
                for poly in g["coordinates"]:
                    for c in poly[0]:
                        px, py = _TO_M.transform(c[0], c[1])
                        xs.append(px); ys.append(py)

        pad = 280.0
        min_x, max_x = min(xs) - pad, max(xs) + pad
        min_y, max_y = min(ys) - pad, max(ys) + pad
    if (max_x - min_x) < 1100:
        mid_x = (min_x + max_x) / 2.0
        min_x, max_x = mid_x - 550, mid_x + 550
    if (max_y - min_y) < 1100:
        mid_y = (min_y + max_y) / 2.0
        min_y, max_y = mid_y - 550, mid_y + 550

    fig, ax = plt.subplots(figsize=(11, 10), dpi=165, facecolor="#0c1017")
    ax.set_facecolor("#0f172a")

    # 1. Satellite / Hybrid / Topo Basemap Tiles
    z, src_lbl = _draw_basemap_tiles(ax, basemap, min_x, max_x, min_y, max_y)

    # 2. Parcel Polygons & Owner Labels
    p_colors = {
        "owned": ("#fbbf24", 0.12, 2.2),
        "permission": ("#2dd4bf", 0.12, 2.0),
        "public": ("#2dd4bf", 0.16, 2.0),
        "mfl_enrolled": ("#60a5fa", 0.12, 1.8),
        "neighbor": ("#fbbf24", 0.04, 1.1),
        "no_hunt": ("#ef4444", 0.18, 1.8),
    }
    for p in bundle.get("parcels") or []:
        g = p.get("geom") or {}
        rings = []
        if g.get("type") == "Polygon" and g.get("coordinates"):
            rings = [g["coordinates"][0]]
        elif g.get("type") == "MultiPolygon" and g.get("coordinates"):
            rings = [poly[0] for poly in g["coordinates"] if poly]
        ec, alpha, lw = p_colors.get(p.get("access_status", "neighbor"), ("#fbbf24", 0.05, 1.1))
        for ring in rings:
            pts_m = [_TO_M.transform(c[0], c[1]) for c in ring]
            if len(pts_m) >= 3:
                ax.add_patch(MplPolygon(pts_m, closed=True, facecolor=ec, alpha=alpha, edgecolor=ec, linewidth=lw, zorder=3))

    labelable = [p for p in (bundle.get("parcels") or []) if p.get("owner_name") and (p.get("acres") or 0) >= 15]
    labelable.sort(key=lambda p: p.get("acres", 0), reverse=True)
    for p in labelable[:10]:
        g = p.get("geom") or {}
        ring = g["coordinates"][0] if g.get("type") == "Polygon" and g.get("coordinates") else (
            g["coordinates"][0][0] if g.get("type") == "MultiPolygon" and g.get("coordinates") else []
        )
        if ring:
            pts_m = [_TO_M.transform(c[0], c[1]) for c in ring]
            rx = sum(pt[0] for pt in pts_m) / len(pts_m)
            ry = sum(pt[1] for pt in pts_m) / len(pts_m)
            if min_x < rx < max_x and min_y < ry < max_y:
                lbl = f"{p['owner_name'][:18]}\n{float(p.get('acres') or 0):.1f} ac"
                ax.text(rx, ry, lbl, color="#f8fafc", fontsize=6.5, ha="center", va="center",
                        bbox=dict(boxstyle="round,pad=0.16", facecolor="#0c1017", alpha=0.68, edgecolor="none"), zorder=4)

    # 3. USGS 3DEP 1m LiDAR Hypsometric Elevation Contours & Thermal Flow Arrows
    terrain = bundle.get("terrain")
    if terrain and terrain.get("has_dem"):
        min_el = float(terrain.get("min_elevation_ft") or 0.0)
        span_el = max(1.0, float(terrain.get("max_elevation_ft") or 1.0) - min_el)
        feats = ((terrain.get("contours") or {}).get("features")) or []
        for feat in feats:
            g = feat.get("geometry") or {}
            pr = feat.get("properties") or {}
            if g.get("type") == "LineString" and len(g.get("coordinates") or []) >= 2:
                pts_m = [_TO_M.transform(c[0], c[1]) for c in g["coordinates"]]
                norm = pr.get("elev_norm")
                if norm is None and pr.get("elevation_ft") is not None:
                    norm = (float(pr["elevation_ft"]) - min_el) / span_el
                col = pr.get("color") or elevation_to_color(float(norm if norm is not None else 0.5))
                is_idx = bool(pr.get("is_index"))
                ax.plot(
                    [pt[0] for pt in pts_m],
                    [pt[1] for pt in pts_m],
                    color=col,
                    linewidth=1.9 if is_idx else 1.0,
                    alpha=0.88 if is_idx else 0.66,
                    zorder=4.5,
                )
                if is_idx and pr.get("elevation_ft") is not None and len(pts_m) >= 4:
                    mid = pts_m[len(pts_m) // 2]
                    if min_x + 80 < mid[0] < max_x - 80 and min_y + 80 < mid[1] < max_y - 80:
                        ax.text(
                            mid[0], mid[1], f"{pr['elevation_ft']}ft",
                            color=col, fontsize=5.8, fontweight="bold", ha="center", va="center",
                            bbox=dict(boxstyle="round,pad=0.1", facecolor="#0c1017", alpha=0.65, edgecolor="none"),
                            zorder=4.8,
                        )

        # Thermal / slope flow arrows
        is_kat = thermal_mode == "katabatic_drainage"
        is_up = thermal_mode == "upslope_warming"
        arr_col = "#f97316" if is_kat else ("#2dd4bf" if is_up else "#94a3b8")
        arr_alpha = 0.85 if (is_kat or is_up) else 0.42
        for v in (terrain.get("thermal_vectors") or []):
            vx, vy = _TO_M.transform(v["lon"], v["lat"])
            if not (min_x < vx < max_x and min_y < vy < max_y):
                continue
            deg = float(v["uphill_deg"] if is_up else v["downhill_deg"])
            rad = math.radians(deg)
            alen = max(48.0, min(115.0, 48.0 + float(v.get("slope_pct") or 1.0) * 6.0))
            ax.annotate(
                "",
                xy=(vx + alen * math.sin(rad), vy + alen * math.cos(rad)),
                xytext=(vx, vy),
                arrowprops=dict(arrowstyle="->", color=arr_col, lw=1.6 if (is_kat or is_up) else 1.1, alpha=arr_alpha),
                zorder=5,
            )

    # 4. Topographic & Water Landmarks (Northwoods Pond, Ridges, Saddles, Drainages)
    lm_palette = {
        "water_source": ("#06b6d4", "o"),
        "ridge": ("#fbbf24", "^"),
        "saddle": ("#a78bfa", "D"),
        "drainage": ("#38bdf8", "v"),
        "bench": ("#2dd4bf", "s"),
        "food_plot": ("#84cc16", "p"),
        "thick_cover": ("#f97316", "h"),
        "access_gate": ("#f8fafc", "P"),
    }
    for lm in bundle.get("topo_landmarks") or []:
        g = lm.get("geom") or {}
        col, mkr = lm_palette.get(lm.get("kind") or "water_source", ("#06b6d4", "o"))
        lbl = lm.get("name") or (lm.get("kind") or "Landmark").replace("_", " ").title()
        elev_s = f" ({round(lm['elevation_ft'])}ft)" if lm.get("elevation_ft") is not None and "ft" not in lbl.lower() else ""
        if g.get("type") == "Point" and len(g.get("coordinates") or []) >= 2:
            lx, ly = _TO_M.transform(g["coordinates"][0], g["coordinates"][1])
            ax.scatter([lx], [ly], s=78, c=col, edgecolors="#0c1017", linewidths=1.5, marker=mkr, zorder=7.5)
            ax.text(lx + 16, ly - 16, f"{lbl}{elev_s}", color="#f8fafc", fontsize=7.0, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.16", facecolor="#0c1017", alpha=0.80, edgecolor=col, lw=0.8), zorder=8.5)
        elif g.get("type") == "LineString" and len(g.get("coordinates") or []) >= 2:
            pts_m = [_TO_M.transform(c[0], c[1]) for c in g["coordinates"]]
            ax.plot([pt[0] for pt in pts_m], [pt[1] for pt in pts_m], color=col, linewidth=2.2, alpha=0.88, zorder=6)
        elif g.get("type") == "Polygon" and g.get("coordinates"):
            pts_m = [_TO_M.transform(c[0], c[1]) for c in g["coordinates"][0]]
            if len(pts_m) >= 3:
                ax.add_patch(MplPolygon(pts_m, closed=True, facecolor=col, alpha=0.22, edgecolor=col, linewidth=1.8, zorder=5.5))

    # 5. Traced Streams (with downstream flow arrows), Walking Trails, ATV/Driving Roads & Deer Trails
    for r in bundle.get("routes") or []:
        g = r.get("geom") or {}
        if g.get("type") == "LineString" and len(g.get("coordinates") or []) >= 2:
            pts_m = [_TO_M.transform(c[0], c[1]) for c in g["coordinates"]]
            rk = (r.get("kind") or "walking_trail").lower()
            xs_r = [pt[0] for pt in pts_m]
            ys_r = [pt[1] for pt in pts_m]
            if rk in ("stream", "creek", "current_seam", "drift_line"):
                col = "#06b6d4" if rk in ("stream", "creek") else "#38bdf8"
                ax.plot(xs_r, ys_r, color=col, linewidth=2.6, linestyle="-" if rk in ("stream", "creek") else "--", alpha=0.92, zorder=5.8)
                # Periodic downstream flow arrowheads along traced stream
                step = max(1, len(pts_m) // 4)
                for idx in range(0, len(pts_m) - 1, step):
                    p_a, p_b = pts_m[idx], pts_m[idx + 1]
                    if math.hypot(p_b[0] - p_a[0], p_b[1] - p_a[1]) > 8.0:
                        ax.annotate("", xy=p_b, xytext=p_a,
                                    arrowprops=dict(arrowstyle="->", color="#67e8f9", lw=1.8, mutation_scale=12), zorder=6.1)
            elif rk in ("driving_road", "atv_road", "road"):
                ax.plot(xs_r, ys_r, color="#0c1017", linewidth=4.2, alpha=0.75, zorder=5.4)
                ax.plot(xs_r, ys_r, color="#e2e8f0" if rk == "driving_road" else "#f59e0b", linewidth=2.2,
                        linestyle="-" if rk == "driving_road" else "--", alpha=0.92, zorder=5.6)
            elif rk == "deer_trail":
                ax.plot(xs_r, ys_r, color="#fbbf24", linewidth=2.0, linestyle=":", alpha=0.95, zorder=5.7)
            else:
                ax.plot(xs_r, ys_r, color="#84cc16", linewidth=2.2, linestyle="--", alpha=0.92, zorder=5.7)
            if r.get("name"):
                mid = pts_m[len(pts_m) // 2]
                if min_x < mid[0] < max_x and min_y < mid[1] < max_y:
                    ax.text(mid[0], mid[1] + 14, r["name"][:22], color="#f8fafc", fontsize=6.4, fontweight="bold", ha="center",
                            bbox=dict(boxstyle="round,pad=0.14", facecolor="#0c1017", alpha=0.72, edgecolor="none"), zorder=6.2)

    # 5b. Saved Waterbodies (Lakes / Reservoirs / Ponds), Bathymetry Contours, Flows & Fish Structures
    for wb in bundle.get("waterbodies") or []:
        g = wb.get("geom") or {}
        rings = []
        if g.get("type") == "Polygon" and g.get("coordinates"):
            rings = [g["coordinates"][0]]
        elif g.get("type") == "MultiPolygon" and g.get("coordinates"):
            rings = [poly[0] for poly in g["coordinates"] if poly]
        all_pts_m = []
        for ring in rings:
            pts_m = [_TO_M.transform(c[0], c[1]) for c in ring]
            if len(pts_m) >= 3:
                ax.add_patch(MplPolygon(pts_m, closed=True, facecolor="#0284c7", alpha=0.22, edgecolor="#38bdf8", linewidth=1.8, zorder=4.9))
                all_pts_m.extend(pts_m)
        if all_pts_m and wb.get("name"):
            rx = sum(pt[0] for pt in all_pts_m) / len(all_pts_m)
            ry = sum(pt[1] for pt in all_pts_m) / len(all_pts_m)
            if min_x < rx < max_x and min_y < ry < max_y:
                extra = []
                if wb.get("acres"):
                    extra.append(f"{wb['acres']:.0f} ac" if float(wb["acres"]) >= 10 else f"{wb['acres']:.1f} ac")
                if wb.get("max_depth_ft"):
                    extra.append(f"Max {wb['max_depth_ft']:.0f}ft")
                lbl = wb["name"][:24] + (f"\n{' · '.join(extra)}" if extra else "")
                ax.text(rx, ry, lbl, color="#e0f2fe", fontsize=7.0, fontweight="bold", ha="center", va="center",
                        bbox=dict(boxstyle="round,pad=0.18", facecolor="#082f49", alpha=0.78, edgecolor="#38bdf8", lw=0.7), zorder=5.1)

    for bg in bundle.get("bathymetry_grids") or []:
        bfeats = ((bg.get("contours") or {}).get("features")) or []
        for bf in bfeats:
            g = bf.get("geometry") or {}
            pr = bf.get("properties") or {}
            if g.get("type") == "LineString" and len(g.get("coordinates") or []) >= 2:
                pts_m = [_TO_M.transform(c[0], c[1]) for c in g["coordinates"]]
                bcol = pr.get("color") or "#0ea5e9"
                ax.plot([pt[0] for pt in pts_m], [pt[1] for pt in pts_m], color=bcol, linewidth=1.6, alpha=0.88, zorder=5.2)
                if pr.get("depth_ft") is not None and len(pts_m) >= 3:
                    mid = pts_m[len(pts_m) // 2]
                    ax.text(mid[0], mid[1], f"-{pr['depth_ft']}ft", color=bcol, fontsize=5.8, fontweight="bold", ha="center",
                            bbox=dict(boxstyle="round,pad=0.08", facecolor="#0c1017", alpha=0.72, edgecolor="none"), zorder=5.4)

    for wf in bundle.get("water_flows") or []:
        fx, fy = _TO_M.transform(wf["lon"], wf["lat"])
        rad = math.radians(float(wf.get("flow_to_deg") or 0.0))
        spd_len = {"calm": 45.0, "slow": 65.0, "moderate": 95.0, "fast": 130.0}.get((wf.get("speed_class") or "moderate").lower(), 90.0)
        ax.annotate("", xy=(fx + spd_len * math.sin(rad), fy + spd_len * math.cos(rad)), xytext=(fx, fy),
                    arrowprops=dict(arrowstyle="->", color="#22d3ee", lw=2.4, mutation_scale=14), zorder=8.2)
        if wf.get("label"):
            ax.text(fx, fy - 18, wf["label"][:20], color="#67e8f9", fontsize=6.5, fontweight="bold", ha="center",
                    bbox=dict(boxstyle="round,pad=0.14", facecolor="#0c1017", alpha=0.78, edgecolor="#06b6d4", lw=0.6), zorder=8.6)

    for fs in bundle.get("fish_structures") or []:
        fx, fy = _TO_M.transform(fs["lon"], fs["lat"])
        ax.scatter([fx], [fy], s=92, c="#06b6d4", edgecolors="#ffffff", linewidths=1.5, marker="p", zorder=8.8)
        dtxt = f" ({fs['depth_ft']}ft)" if fs.get("depth_ft") is not None else ""
        ax.text(fx + 16, fy + 16, f"{fs['name']}{dtxt}", color="#cffafe", fontsize=7.0, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.16", facecolor="#083344", alpha=0.86, edgecolor="#06b6d4", lw=0.8), zorder=9.2)

    # 6. DEM-Blended Scent Cones + Stand/Blind/Cam/Bedding Markers
    for w in bundle.get("waypoints") or []:
        wx, wy = _TO_M.transform(w["lon"], w["lat"])
        if w["kind"] in ("stand", "blind"):
            pt_t = sample_point_terrain(pid, float(w["lat"]), float(w["lon"]))
            cone = scent_cone_polygon_m(w["lat"], w["lon"], wind_deg, wind_mph, thermal_mode, terrain_pt=pt_t)
            ax.add_patch(MplPolygon(list(cone.exterior.coords), closed=True, facecolor="#f97316", alpha=0.26, edgecolor="#f97316", linewidth=1.5, linestyle="--", zorder=6.5))
            ax.scatter([wx], [wy], s=110, c="#38bdf8", edgecolors="#0c1017", linewidths=1.8, marker="^", zorder=9)
        elif w["kind"] in ("trail_cam", "camera"):
            ax.scatter([wx], [wy], s=75, c="#a78bfa", edgecolors="#0c1017", linewidths=1.4, marker="s", zorder=9)
        elif w["kind"] == "bedding":
            ax.scatter([wx], [wy], s=90, c="#f97316", edgecolors="#0c1017", linewidths=1.4, marker="D", zorder=9)
        else:
            ax.scatter([wx], [wy], s=70, c="#f8fafc", edgecolors="#0c1017", linewidths=1.4, marker="o", zorder=9)
        label = w["name"] + (f" ({w['assigned_hunter']})" if w.get("assigned_hunter") else "")
        ax.text(wx + 18, wy + 18, label, color="#ffffff", fontsize=8.0, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#0c1017", alpha=0.86, edgecolor="#38bdf8", lw=0.9), zorder=10)

    # 7. Visible Removable Tactical Plan Annotations
    for ann in (bundle.get("annotations") or []):
        if not ann.get("visible", True):
            continue
        g = ann.get("geom") or {}
        gtype = g.get("type")
        col = ann.get("color") or "#a78bfa"
        lbl = (ann.get("label") or "")[:28]
        try:
            if gtype == "Point":
                c = g["coordinates"]
                ax_x, ax_y = _TO_M.transform(c[0], c[1])
                ax.scatter([ax_x], [ax_y], s=110, c=col, edgecolors="#ffffff", linewidths=1.6, marker="D", zorder=9.5)
                if lbl:
                    ax.text(ax_x + 16, ax_y - 22, lbl, color="#f8fafc", fontsize=7.5, fontweight="bold",
                            bbox=dict(boxstyle="round,pad=0.18", facecolor="#1e1b4b", alpha=0.88, edgecolor=col, lw=0.9), zorder=10.5)
            elif gtype == "LineString":
                pts_m = [_TO_M.transform(c[0], c[1]) for c in g["coordinates"]]
                if len(pts_m) >= 2:
                    ax.plot([pt[0] for pt in pts_m], [pt[1] for pt in pts_m], color=col, linewidth=2.6, linestyle="-.", zorder=7.5)
                    mid = pts_m[len(pts_m) // 2]
                    if lbl:
                        ax.text(mid[0], mid[1] + 18, lbl, color="#f8fafc", fontsize=7.2, fontweight="bold", ha="center",
                                bbox=dict(boxstyle="round,pad=0.16", facecolor="#0c1017", alpha=0.85, edgecolor=col, lw=0.8), zorder=10.5)
            elif gtype == "Polygon":
                ring = g["coordinates"][0]
                pts_m = [_TO_M.transform(c[0], c[1]) for c in ring]
                if len(pts_m) >= 3:
                    ax.add_patch(MplPolygon(pts_m, closed=True, facecolor=col, alpha=0.22, edgecolor=col, linewidth=2.0, linestyle="--", zorder=6))
                    rx = sum(pt[0] for pt in pts_m) / len(pts_m)
                    ry = sum(pt[1] for pt in pts_m) / len(pts_m)
                    if lbl:
                        ax.text(rx, ry, lbl, color="#f8fafc", fontsize=7.2, fontweight="bold", ha="center", va="center",
                                bbox=dict(boxstyle="round,pad=0.18", facecolor="#0c1017", alpha=0.85, edgecolor=col, lw=0.8), zorder=10.5)
        except Exception:
            continue

    # 8. Live Mobile GPS Hunters
    for h in bundle.get("hunters") or []:
        hx, hy = _TO_M.transform(h["lon"], h["lat"])
        ax.scatter([hx], [hy], s=110, c="#2563eb", edgecolors="#ffffff", linewidths=1.8, marker="o", zorder=10.5)
        if h.get("heading_deg") is not None:
            rad = math.radians(float(h["heading_deg"]))
            ax.annotate("", xy=(hx + 90 * math.sin(rad), hy + 90 * math.cos(rad)), xytext=(hx, hy),
                        arrowprops=dict(arrowstyle="->", color="#38bdf8", lw=2.2), zorder=10.5)
        ax.text(hx, hy - 32, f"GPS: {h['hunter_name']}", color="#93c5fd", fontsize=7.5, fontweight="bold", ha="center",
                bbox=dict(boxstyle="round,pad=0.16", facecolor="#0c1017", alpha=0.86, edgecolor="#38bdf8"), zorder=11)

    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])

    span_x = max_x - min_x
    span_y = max_y - min_y

    # Scale bar (200 yards = 182.88m)
    sb_len = 182.88
    sb_x0 = min_x + span_x * 0.04
    sb_y0 = min_y + span_y * 0.045
    ax.plot([sb_x0, sb_x0 + sb_len], [sb_y0, sb_y0], color="#ffffff", lw=3.0, zorder=12)
    ax.text(sb_x0 + sb_len / 2, sb_y0 + span_y * 0.018, "200 yds", color="#ffffff", fontsize=8, fontweight="bold", ha="center",
            bbox=dict(boxstyle="round,pad=0.15", facecolor="#0c1017", alpha=0.78, edgecolor="none"), zorder=12)

    # Elevation gradient legend bar (bottom-right) if DEM loaded
    if terrain and terrain.get("has_dem"):
        leg_w = span_x * 0.24
        leg_h = span_y * 0.016
        lx0 = max_x - span_x * 0.04 - leg_w
        ly0 = min_y + span_y * 0.045
        ax.add_patch(Rectangle((lx0 - span_x * 0.01, ly0 - span_y * 0.025), leg_w + span_x * 0.02, leg_h + span_y * 0.055,
                               facecolor="#0c1017", alpha=0.82, edgecolor="#334155", linewidth=1.0, zorder=11.5))
        n_seg = 24
        for i in range(n_seg):
            c = elevation_to_color(i / max(1, n_seg - 1))
            ax.add_patch(Rectangle((lx0 + (i / n_seg) * leg_w, ly0), leg_w / n_seg + 1, leg_h,
                                   facecolor=c, edgecolor="none", zorder=12))
        ax.text(lx0, ly0 - span_y * 0.016, f"{round(terrain.get('min_elevation_ft') or 0)}ft Low", color="#cbd5e1", fontsize=6.5, ha="left", zorder=12.5)
        ax.text(lx0 + leg_w, ly0 - span_y * 0.016, f"{round(terrain.get('max_elevation_ft') or 0)}ft Ridge", color="#cbd5e1", fontsize=6.5, ha="right", zorder=12.5)
        step_s = f"{terrain.get('contour_step_ft')}ft" if terrain.get("contour_step_ft") else "DEM"
        ax.text(lx0 + leg_w / 2, ly0 + leg_h + span_y * 0.008, f"LiDAR Elevation ({step_s} int.)", color="#f8fafc", fontsize=6.8, fontweight="bold", ha="center", zorder=12.5)

    # Compass rose + Wind arrow (top-right)
    cr_r = min(span_x, span_y) * 0.068
    cr_cx = max_x - cr_r * 1.45
    cr_cy = max_y - cr_r * 1.45
    ax.add_patch(Circle((cr_cx, cr_cy), cr_r, facecolor="#0c1017", alpha=0.78, edgecolor="#475569", linewidth=1.5, zorder=11))
    for ang, lbl, col in ((0, "N", "#f8fafc"), (90, "E", "#94a3b8"), (180, "S", "#94a3b8"), (270, "W", "#94a3b8")):
        rad = math.radians(ang)
        ax.plot([cr_cx, cr_cx + cr_r * 0.72 * math.sin(rad)], [cr_cy, cr_cy + cr_r * 0.72 * math.cos(rad)], color="#64748b", lw=1.1, zorder=12)
        ax.text(cr_cx + cr_r * 0.88 * math.sin(rad), cr_cy + cr_r * 0.88 * math.cos(rad), lbl, color=col, fontsize=8.5, fontweight="bold",
                ha="center", va="center", zorder=12)
    # Scent travel direction indicator on compass
    sc_rad = math.radians((wind_deg + 180.0) % 360.0)
    ax.annotate("", xy=(cr_cx + cr_r * 0.65 * math.sin(sc_rad), cr_cy + cr_r * 0.65 * math.cos(sc_rad)), xytext=(cr_cx, cr_cy),
                arrowprops=dict(arrowstyle="->", color="#f97316", lw=2.2), zorder=12.5)

    ax.text((min_x + max_x) / 2, min_y + span_y * 0.012, f"Basemap: {src_lbl} | Blue Lodge Hunting Buddy", color="#cbd5e1", fontsize=6.2, ha="center",
            bbox=dict(boxstyle="round,pad=0.12", facecolor="#0c1017", alpha=0.68, edgecolor="none"), zorder=12)

    w_comp = deg_to_compass(wind_deg)
    s_comp = deg_to_compass((wind_deg + 180.0) % 360.0)
    conflict_txt = f" | CONFLICTS: {len(plan['conflicts'])}" if plan.get("conflicts") else " | ZERO DOWNWIND CONFLICTS"
    ax.set_title(
        f"{prop['name']} — {scenario_name}\nWind: {w_comp} {wind_mph:.0f} mph (Scent → {s_comp}) | Thermal: {thermal_mode.replace('_', ' ')}{conflict_txt}",
        color="#f8fafc", fontsize=11, fontweight="bold", pad=10,
    )

    out_dir = os.path.join(DEFAULT_DATA_DIR, "renders")
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in prop["name"]).strip("_").lower() or "camp"
    png_path = os.path.join(out_dir, f"{safe}_tactical_{w_comp.lower()}.png")
    fig.savefig(png_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    try:
        shutil.copyfile(png_path, os.path.join(out_dir, "latest_map.png"))
    except Exception:
        pass

    conn.execute(
        "INSERT INTO map_renders (property_id, scenario_name, wind_deg, wind_mph, thermal_mode, basemap, png_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, scenario_name, wind_deg, wind_mph, thermal_mode, basemap, png_path, now_iso()),
    )
    conn.commit()
    return {
        "status": "ok",
        "png_path": png_path,
        "basemap_source": src_lbl,
        "has_dem_contours": bool(terrain and terrain.get("has_dem")),
        "conflicts": plan.get("conflicts", []),
        "stand_scores": plan.get("stand_scores", []),
    }


if __name__ == "__main__":
    from hunt_db import parse_json_stdin
    payload = parse_json_stdin()
    print(json.dumps(render_tactical_map(**payload), indent=2))
