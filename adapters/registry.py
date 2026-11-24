#!/usr/bin/env python3
"""Pluggable Sovereign Spatial Provider Engine, Offline Cache & Drift Watchdog.

Loads declarative geospatial providers from `adapters/providers.default.json` and overlays
persistent user overrides from `MODULE_DATA_DIR/providers.user.json` (which survives module
upgrades). Supports automatic 3-tier regional -> global failover, stale-while-revalidate
SQLite offline caching, schema-drift health probes, and one-click Offline Camp Packs.
"""

import datetime
import html as _html
import io
import json
import math
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD_ROOT = os.path.dirname(_HERE)
DEFAULT_PROVIDERS_FILE = os.path.join(_HERE, "providers.default.json")

import sys
if _MOD_ROOT not in sys.path:
    sys.path.insert(0, _MOD_ROOT)
from tools.hunt_db import DEFAULT_DATA_DIR, get_conn, now_iso

USER_AGENT = "BlueLodge-FieldAndWater/2.2 (Sovereign Cache-First GIS; contact@bluelodge.local)"
GRID_STEP = 0.015  # ~1.2-1.6 km per cache cell
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def user_providers_path() -> str:
    data_dir = os.environ.get("MODULE_DATA_DIR", DEFAULT_DATA_DIR)
    return os.path.join(data_dir, "providers.user.json")


def _bbox_intersects(a: Tuple[float, float, float, float], b: List[float]) -> bool:
    if not b or len(b) < 4:
        return True
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _extract_field(props: Dict[str, Any], aliases: List[str], default: Any = "") -> Any:
    if not props or not aliases:
        return default
    low_map = {str(k).lower(): v for k, v in props.items()}
    for cand in aliases:
        if cand in props and props[cand] not in (None, ""):
            return props[cand]
        lv = low_map.get(str(cand).lower())
        if lv not in (None, ""):
            return lv
    return default


def load_providers(
    layer: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    include_disabled: bool = False,
) -> List[Dict[str, Any]]:
    """Load merged default + user providers ordered by priority (lowest number first)."""
    by_id: Dict[str, Dict[str, Any]] = {}
    if os.path.isfile(DEFAULT_PROVIDERS_FILE):
        with open(DEFAULT_PROVIDERS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for p in raw.get("providers") or []:
            pid = p.get("id")
            if pid:
                item = dict(p)
                item["origin"] = "default"
                by_id[pid] = item

    upath = user_providers_path()
    if os.path.isfile(upath):
        try:
            with open(upath, "r", encoding="utf-8") as f:
                uraw = json.load(f)
            u_list = uraw.get("providers") if isinstance(uraw, dict) else (uraw if isinstance(uraw, list) else [])
            for p in u_list or []:
                pid = p.get("id")
                if not pid:
                    continue
                if pid in by_id:
                    merged = dict(by_id[pid])
                    merged.update(p)
                    merged["origin"] = "user_override"
                    by_id[pid] = merged
                else:
                    item = dict(p)
                    item["origin"] = "user_custom"
                    by_id[pid] = item
        except Exception:
            pass

    out: List[Dict[str, Any]] = []
    for p in by_id.values():
        if not include_disabled and not p.get("enabled", True):
            continue
        if layer and p.get("layer") != layer:
            continue
        if bbox and not _bbox_intersects(bbox, p.get("bbox") or []):
            continue
        out.append(p)
    out.sort(key=lambda x: (int(x.get("priority", 50)), str(x.get("id", ""))))
    return out


def save_user_provider(prov: Dict[str, Any]) -> Dict[str, Any]:
    """Add or override a provider in persistent MODULE_DATA_DIR/providers.user.json."""
    pid = str(prov.get("id") or "").strip()
    if not pid:
        return {"status": "error", "message": "Provider 'id' is required."}
    if not prov.get("layer") or not prov.get("driver") or not prov.get("url"):
        return {"status": "error", "message": "Provider requires 'id', 'layer', 'driver', and 'url'."}
    upath = user_providers_path()
    os.makedirs(os.path.dirname(upath), exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if os.path.isfile(upath):
        try:
            with open(upath, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("providers") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        except Exception:
            existing = []
    clean = [p for p in existing if p.get("id") != pid]
    clean.append(prov)
    with open(upath, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "providers": clean}, f, indent=2)
    return {"status": "ok", "id": pid, "providers_file": upath}


def delete_user_provider(provider_id: str) -> Dict[str, Any]:
    upath = user_providers_path()
    if not os.path.isfile(upath):
        return {"status": "ok", "deleted": provider_id}
    with open(upath, "r", encoding="utf-8") as f:
        data = json.load(f)
    existing = data.get("providers") if isinstance(data, dict) else []
    clean = [p for p in existing if p.get("id") != provider_id]
    with open(upath, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "providers": clean}, f, indent=2)
    return {"status": "ok", "deleted": provider_id}


def record_provider_health(
    provider_id: str,
    layer: str,
    status: str,
    latency_ms: int = 0,
    error_msg: str = "",
    detected_fields: Optional[List[str]] = None,
) -> None:
    try:
        conn = get_conn()
        ts = now_iso()
        row = conn.execute("SELECT consecutive_failures, last_ok_at FROM provider_health WHERE provider_id = ?", (provider_id,)).fetchone()
        prev_fails = int(row["consecutive_failures"]) if row else 0
        last_ok = ts if status == "healthy" else (row["last_ok_at"] if row else "")
        fails = 0 if status == "healthy" else (prev_fails + 1)
        conn.execute(
            """INSERT OR REPLACE INTO provider_health
               (provider_id, layer, status, last_checked_at, last_ok_at, consecutive_failures, latency_ms, last_error, detected_fields_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider_id,
                layer,
                status,
                ts,
                last_ok,
                fails,
                int(latency_ms),
                str(error_msg or "")[:300],
                json.dumps(detected_fields or []),
            ),
        )
        conn.commit()
    except Exception:
        pass


def get_provider_health_map() -> Dict[str, Dict[str, Any]]:
    try:
        conn = get_conn()
        rows = conn.execute("SELECT * FROM provider_health").fetchall()
        return {r["provider_id"]: dict(r) for r in rows}
    except Exception:
        return {}


# ── Generic Protocol Drivers ───────────────────────────────────────────────

def _http_get_bytes(
    url: str,
    timeout: int = 16,
    data: Optional[bytes] = None,
    retries: int = 2,
    extra_headers: Optional[Dict[str, str]] = None,
) -> bytes:
    """Resilient HTTP fetch with brief exponential backoff on transient 429/502/503/504 & timeouts."""
    ctx = ssl.create_default_context()
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    last_exc: Optional[Exception] = None
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in _RETRYABLE_HTTP and attempt + 1 < attempts:
                time.sleep(0.55 * (2 ** attempt))
                continue
            raise
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(0.55 * (2 ** attempt))
                continue
            raise
    raise RuntimeError(str(last_exc or "HTTP request failed"))


def query_arcgis_feature(
    prov: Dict[str, Any],
    bbox: Tuple[float, float, float, float],
    max_records: int = 120,
) -> List[Dict[str, Any]]:
    """Generic ArcGIS MapServer/FeatureServer GeoJSON bbox query with fallback URLs & multi-layer support."""
    min_lon, min_lat, max_lon, max_lat = bbox
    base_url = str(prov.get("url") or "")
    extra_ids = prov.get("extra_layer_ids") or []
    urls_to_try: List[str] = []

    if extra_ids and "/MapServer/" in base_url:
        prefix = base_url.split("/MapServer/")[0] + "/MapServer"
        for lid in extra_ids:
            urls_to_try.append(f"{prefix}/{lid}/query")
    else:
        urls_to_try.append(base_url)
        for fb in (prov.get("fallback_urls") or []):
            urls_to_try.append(str(fb))

    base_params = {
        "f": "geojson",
        "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": prov.get("out_fields") or "*",
        "returnGeometry": "true",
    }

    all_feats: List[Dict[str, Any]] = []
    field_map = prov.get("field_map") or {}
    last_err = None
    multi_layer_mode = bool(extra_ids and "/MapServer/" in base_url)

    for u in urls_to_try:
        try:
            q_dict = dict(base_params)
            if "/FeatureServer/" in u or prov.get("supports_pagination"):
                q_dict["resultRecordCount"] = str(max_records)
            params = urllib.parse.urlencode(q_dict)
            raw = _http_get_bytes(f"{u}?{params}", timeout=12)
            data = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("error"):
                last_err = str(data["error"])
                continue
            feats = data.get("features") or []
            for feat in feats:
                geom = feat.get("geometry")
                props = feat.get("properties") or feat.get("attributes") or {}
                if not geom:
                    continue
                norm: Dict[str, Any] = {
                    "geom": geom,
                    "provider_id": prov["id"],
                    "raw_props": props,
                }
                for out_k, aliases in field_map.items():
                    norm[out_k] = _extract_field(props, aliases, "")
                if prov.get("default_kind"):
                    norm["kind"] = prov["default_kind"]
                if prov.get("direction_mode"):
                    norm["direction_mode"] = prov["direction_mode"]
                if prov.get("fishery_tag"):
                    norm["fishery_tag"] = prov["fishery_tag"]
                all_feats.append(norm)
            if not multi_layer_mode:
                return all_feats
        except Exception as exc:
            last_err = str(exc)
            if not multi_layer_mode:
                continue
    if not all_feats and last_err:
        raise RuntimeError(last_err)
    return all_feats


def query_arcgis_image_dem(
    prov: Dict[str, Any],
    bbox: Tuple[float, float, float, float],
    grid_n: int = 64,
) -> Tuple[List[float], List[float], np.ndarray]:
    """Generic ArcGIS F32 GeoTIFF elevation ImageServer driver (USGS 3DEP or custom state LiDAR)."""
    from PIL import Image

    min_lon, min_lat, max_lon, max_lat = bbox
    n = max(16, min(96, int(grid_n)))
    q = urllib.parse.urlencode({
        "bbox": f"{min_lon:.6f},{min_lat:.6f},{max_lon:.6f},{max_lat:.6f}",
        "bboxSR": "4326",
        "size": f"{n},{n}",
        "imageSR": "4326",
        "format": "tiff",
        "pixelType": "F32",
        "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "+RSP_BilinearInterpolation",
        "f": "image",
    })
    raw = _http_get_bytes(f"{prov['url']}?{q}", timeout=20)
    img = Image.open(io.BytesIO(raw))
    arr = np.array(img, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 4 or arr.shape[1] < 4:
        raise RuntimeError("Invalid DEM raster shape")
    valid = np.isfinite(arr) & (arr > -500.0) & (arr < 9000.0)
    if int(np.sum(valid)) < (arr.size * 0.7):
        raise RuntimeError("DEM raster contains >30% NoData pixels")
    med = float(np.median(arr[valid]))
    arr = np.where(valid, arr, med)
    arr = arr[::-1, :]  # Flip north->south image rows to south->north latitude order
    lats_1d = list(np.linspace(min_lat, max_lat, arr.shape[0]))
    lons_1d = list(np.linspace(min_lon, max_lon, arr.shape[1]))
    return lats_1d, lons_1d, arr


def query_open_meteo_dem(
    prov: Dict[str, Any],
    bbox: Tuple[float, float, float, float],
    grid_n: int = 12,
) -> Tuple[List[float], List[float], np.ndarray]:
    """Global keyless Open-Meteo Copernicus 30m DEM driver."""
    min_lon, min_lat, max_lon, max_lat = bbox
    n = max(6, min(14, int(grid_n)))
    lats_1d = list(np.linspace(min_lat, max_lat, n))
    lons_1d = list(np.linspace(min_lon, max_lon, n))
    flat_lats, flat_lons = [], []
    for la in lats_1d:
        for lo in lons_1d:
            flat_lats.append(float(la))
            flat_lons.append(float(lo))
    out: List[float] = []
    chunk = 95
    for start in range(0, len(flat_lats), chunk):
        la_s = ",".join(f"{x:.5f}" for x in flat_lats[start : start + chunk])
        lo_s = ",".join(f"{x:.5f}" for x in flat_lons[start : start + chunk])
        raw = _http_get_bytes(f"{prov['url']}?latitude={la_s}&longitude={lo_s}", timeout=16)
        data = json.loads(raw.decode("utf-8"))
        out.extend(float(v) if v is not None else 0.0 for v in (data.get("elevation") or []))
    if len(out) != len(flat_lats):
        raise RuntimeError(f"Open-Meteo DEM returned {len(out)}/{len(flat_lats)} samples")
    return lats_1d, lons_1d, np.array(out, dtype=np.float64).reshape((n, n))


def query_overpass_osm(
    prov: Dict[str, Any],
    bbox: Tuple[float, float, float, float],
) -> List[Dict[str, Any]]:
    """Global OpenStreetMap Overpass driver for waterways & forest trails/roads with mirror failover."""
    min_lon, min_lat, max_lon, max_lat = bbox
    qkind = prov.get("osm_query_kind") or "waterways"
    bb = f"{min_lat:.5f},{min_lon:.5f},{max_lat:.5f},{max_lon:.5f}"
    if qkind == "waterways":
        ql = f'[out:json][timeout:14];(way["waterway"~"^(stream|river|brook|canal|drain)$"]({bb}););out geom;'
    elif qkind == "waterbodies":
        ql = f'[out:json][timeout:14];(way["natural"="water"]({bb});way["water"~"^(lake|reservoir|pond|basin)$"]({bb}););out geom;'
    else:
        ql = f'[out:json][timeout:14];(way["highway"~"^(track|path|footway|bridleway|unclassified|service|tertiary|residential)$"]({bb}););out geom;'

    urls = [prov["url"]] + list(prov.get("fallback_urls") or [])
    body = urllib.parse.urlencode({"data": ql}).encode("utf-8")
    last_err = None
    data = None
    for u in urls:
        try:
            raw = _http_get_bytes(u, timeout=14, data=body, retries=1)
            data = json.loads(raw.decode("utf-8", errors="replace"))
            break
        except Exception as exc:
            last_err = str(exc)
    if data is None:
        raise RuntimeError(last_err or "Overpass mirrors failed")

    feats: List[Dict[str, Any]] = []
    for elem in (data.get("elements") or [])[:140]:
        pts = elem.get("geometry") or []
        if len(pts) < 2:
            continue
        coords = [[round(float(p["lon"]), 6), round(float(p["lat"]), 6)] for p in pts]
        tags = elem.get("tags") or {}
        if qkind == "waterbodies":
            if len(coords) < 4:
                continue
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            nm = tags.get("name") or f"Waterbody #{elem.get('id', '')}"
            feats.append({
                "name": nm,
                "waterbody_type": tags.get("water") or "lake",
                "geom": {"type": "Polygon", "coordinates": [coords]},
                "provider_id": prov["id"],
                "raw_props": tags,
            })
        elif qkind == "waterways":
            ww = tags.get("waterway", "stream")
            nm = tags.get("name") or f"{ww.title()} #{elem.get('id', '')}"
            feats.append({
                "name": nm,
                "kind": "stream",
                "direction_mode": "downstream",
                "surface": "water",
                "geom": {"type": "LineString", "coordinates": coords},
                "provider_id": prov["id"],
                "raw_props": tags,
            })
        else:
            hw = tags.get("highway", "track")
            kind = "walking_trail" if hw in ("path", "footway", "bridleway") else ("atv_road" if hw == "track" else "driving_road")
            nm = tags.get("name") or f"{hw.replace('_', ' ').title()} #{elem.get('id', '')}"
            feats.append({
                "name": nm,
                "kind": kind,
                "direction_mode": "bidirectional",
                "surface": tags.get("surface") or ("dirt" if hw in ("track", "path") else "gravel"),
                "geom": {"type": "LineString", "coordinates": coords},
                "provider_id": prov["id"],
                "raw_props": tags,
            })
    return feats


def fetch_elevation_cascade(
    bbox: Tuple[float, float, float, float],
    grid_n: int = 64,
) -> Tuple[str, List[float], List[float], np.ndarray]:
    """Cascade across elevation providers (User Custom -> USGS 3DEP 1m -> Open-Meteo 30m Global)."""
    provs = load_providers(layer="elevation", bbox=bbox)
    errors: List[str] = []
    for p in provs:
        t0 = time.monotonic()
        try:
            drv = p.get("driver")
            if drv == "arcgis_image_dem":
                lats, lons, arr = query_arcgis_image_dem(p, bbox, grid_n=grid_n)
            elif drv == "open_meteo_dem":
                lats, lons, arr = query_open_meteo_dem(p, bbox, grid_n=min(12, grid_n))
            else:
                continue
            record_provider_health(p["id"], "elevation", "healthy", int((time.monotonic() - t0) * 1000))
            return p["id"], lats, lons, arr
        except Exception as exc:
            record_provider_health(p["id"], "elevation", "degraded", int((time.monotonic() - t0) * 1000), str(exc))
            errors.append(f"{p['id']}: {exc}")
    raise RuntimeError("All elevation providers failed: " + "; ".join(errors))


def _is_cache_fresh(fetched_at: str, ttl_days: float = 30.0) -> bool:
    try:
        dt = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() < ttl_days * 86400.0
    except Exception:
        return False


def fetch_vector_layer_cascade(
    layer: str,
    bbox: Tuple[float, float, float, float],
    force_refresh: bool = False,
    merge_all_matching: bool = False,
    max_records: int = 120,
) -> Dict[str, Any]:
    """Stale-while-revalidate spatial grid cache with automatic multi-tier provider failover."""
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gis_cache_tiles (
            cache_key TEXT PRIMARY KEY,
            layer_type TEXT NOT NULL,
            bbox_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )"""
    )
    cx = int(math.floor(((bbox[0] + bbox[2]) * 0.5) / GRID_STEP))
    cy = int(math.floor(((bbox[1] + bbox[3]) * 0.5) / GRID_STEP))
    ckey = f"{layer}:{cx}:{cy}"

    row = conn.execute("SELECT fetched_at, payload_json FROM gis_cache_tiles WHERE cache_key = ?", (ckey,)).fetchone()
    provs = load_providers(layer=layer, bbox=bbox)
    ttl_days = float(provs[0].get("ttl_days", 30)) if provs else 30.0

    if row and not force_refresh and _is_cache_fresh(row["fetched_at"], ttl_days=ttl_days):
        cached = json.loads(row["payload_json"])
        return {
            "status": "ok",
            "layer": layer,
            "features": cached.get("features") or [],
            "providers_used": cached.get("providers_used") or [],
            "from_cache": True,
            "offline_fallback": False,
            "fetched_at": row["fetched_at"],
        }

    collected: List[Dict[str, Any]] = []
    used_ids: List[str] = []
    errors: List[str] = []

    for p in provs:
        t0 = time.monotonic()
        try:
            drv = p.get("driver")
            if drv == "arcgis_feature":
                feats = query_arcgis_feature(p, bbox, max_records=max_records)
            elif drv == "overpass_osm":
                feats = query_overpass_osm(p, bbox)
            else:
                continue
            record_provider_health(p["id"], layer, "healthy", int((time.monotonic() - t0) * 1000))
            if feats:
                collected.extend(feats)
                used_ids.append(p["id"])
                if not merge_all_matching:
                    break
        except Exception as exc:
            record_provider_health(p["id"], layer, "degraded", int((time.monotonic() - t0) * 1000), str(exc))
            errors.append(f"{p['id']}: {exc}")

    if collected or not errors:
        ts = now_iso()
        conn.execute(
            "INSERT OR REPLACE INTO gis_cache_tiles (cache_key, layer_type, bbox_json, fetched_at, payload_json) VALUES (?, ?, ?, ?, ?)",
            (ckey, layer, json.dumps(list(bbox)), ts, json.dumps({"features": collected, "providers_used": used_ids})),
        )
        conn.commit()
        return {
            "status": "ok",
            "layer": layer,
            "features": collected,
            "providers_used": used_ids,
            "from_cache": False,
            "offline_fallback": False,
            "fetched_at": ts,
            "warnings": errors,
        }

    # Stale-while-revalidate offline fallback: NEVER discard good cached data when upstream is unreachable!
    if row:
        cached = json.loads(row["payload_json"])
        return {
            "status": "ok",
            "layer": layer,
            "features": cached.get("features") or [],
            "providers_used": cached.get("providers_used") or [],
            "from_cache": True,
            "offline_fallback": True,
            "fetched_at": row["fetched_at"],
            "warnings": errors,
        }

    return {"status": "error", "layer": layer, "features": [], "message": "; ".join(errors)}


def check_providers_health(layer: Optional[str] = None) -> Dict[str, Any]:
    """Probe every configured provider for reachability & ArcGIS field-map schema drift."""
    provs = load_providers(layer=layer, include_disabled=True)
    results: List[Dict[str, Any]] = []
    for p in provs:
        if not p.get("enabled", True):
            results.append({"id": p["id"], "name": p.get("name"), "layer": p.get("layer"), "region": p.get("region"), "status": "disabled", "origin": p.get("origin")})
            continue
        hurl = p.get("health_url") or p.get("url")
        probe_urls: List[str] = [hurl]
        for fb in (p.get("fallback_urls") or []):
            fb_s = str(fb)
            if p.get("driver") == "overpass_osm" and fb_s.endswith("/interpreter"):
                probe_urls.append(fb_s[:-len("/interpreter")] + "/status")
            else:
                probe_urls.append(fb_s)
        t0 = time.monotonic()
        status = "healthy"
        err = ""
        drift_fields: List[str] = []
        detected_fields: List[str] = []
        raw: Optional[bytes] = None
        last_exc: Optional[Exception] = None
        for pu in probe_urls:
            try:
                raw = _http_get_bytes(pu, timeout=8, retries=1)
                break
            except Exception as exc:
                last_exc = exc
        try:
            if raw is None:
                raise last_exc or RuntimeError("Unreachable")
            ms = int((time.monotonic() - t0) * 1000)
            txt = raw.decode("utf-8", errors="replace")
            if p.get("driver") == "arcgis_feature" and hurl.endswith("f=pjson"):
                meta = json.loads(txt)
                if meta.get("error"):
                    status = "degraded"
                    err = str(meta["error"])
                elif isinstance(meta.get("fields"), list):
                    detected_fields = [str(f.get("name") or "") for f in meta["fields"]]
                    low_set = {f.lower() for f in detected_fields}
                    for fk, aliases in (p.get("field_map") or {}).items():
                        if aliases and not any(a.lower() in low_set for a in aliases):
                            drift_fields.append(f"{fk} (expected one of {aliases})")
                    if drift_fields:
                        status = "schema_drift"
                        err = "Missing mapped fields: " + ", ".join(drift_fields)
            record_provider_health(p["id"], p.get("layer", ""), status, ms, err, detected_fields[:25])
            results.append({
                "id": p["id"],
                "name": p.get("name"),
                "layer": p.get("layer"),
                "driver": p.get("driver"),
                "region": p.get("region"),
                "origin": p.get("origin"),
                "status": status,
                "latency_ms": ms,
                "error": err,
                "drift_fields": drift_fields,
            })
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            record_provider_health(p["id"], p.get("layer", ""), "offline", ms, str(exc))
            results.append({
                "id": p["id"],
                "name": p.get("name"),
                "layer": p.get("layer"),
                "driver": p.get("driver"),
                "region": p.get("region"),
                "origin": p.get("origin"),
                "status": "offline",
                "latency_ms": ms,
                "error": str(exc)[:180],
            })
    ok_cnt = sum(1 for r in results if r["status"] == "healthy")
    return {
        "status": "ok",
        "checked_at": now_iso(),
        "total": len(results),
        "healthy": ok_cnt,
        "providers": results,
    }


def list_providers_with_health() -> Dict[str, Any]:
    provs = load_providers(include_disabled=True)
    hmap = get_provider_health_map()
    out = []
    for p in provs:
        h = hmap.get(p["id"]) or {}
        out.append({
            **p,
            "health_status": h.get("status") or ("disabled" if not p.get("enabled", True) else "untested"),
            "last_checked_at": h.get("last_checked_at") or "",
            "last_ok_at": h.get("last_ok_at") or "",
            "latency_ms": h.get("latency_ms"),
            "last_error": h.get("last_error") or "",
        })
    # Compute local offline cache footprint
    data_dir = os.environ.get("MODULE_DATA_DIR", DEFAULT_DATA_DIR)
    total_bytes = 0
    tile_count = 0
    for root_d, _, files in os.walk(data_dir):
        for fn in files:
            fp = os.path.join(root_d, fn)
            try:
                total_bytes += os.path.getsize(fp)
                if fn.endswith(".tile"):
                    tile_count += 1
            except Exception:
                pass
    conn = get_conn()
    try:
        cached_cells = conn.execute("SELECT COUNT(*) FROM gis_cache_tiles").fetchone()[0]
    except Exception:
        cached_cells = 0
    return {
        "status": "ok",
        "providers": out,
        "user_providers_file": user_providers_path(),
        "offline_cache": {
            "disk_mb": round(total_bytes / (1024 * 1024), 2),
            "cached_tiles": tile_count,
            "cached_gis_cells": int(cached_cells),
        },
    }


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_mi = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2.0 * r_mi * math.asin(min(1.0, math.sqrt(a))), 1)


def query_usgs_nwis_gauges(prov: Dict[str, Any], lat: float, lon: float, radius_deg: float = 0.45) -> List[Dict[str, Any]]:
    """Query live USGS NWIS instantaneous stream gauges (CFS, gauge height ft, water temp F)."""
    bb = f"{lon - radius_deg:.4f},{lat - radius_deg * 0.8:.4f},{lon + radius_deg:.4f},{lat + radius_deg * 0.8:.4f}"
    q = urllib.parse.urlencode({
        "format": "json",
        "bBox": bb,
        "parameterCd": "00060,00065,00010",
        "siteStatus": "active",
    })
    raw = _http_get_bytes(f"{prov['url']}?{q}", timeout=14)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    ts_list = ((data.get("value") or {}).get("timeSeries")) or []

    by_site: Dict[str, Dict[str, Any]] = {}
    for ts in ts_list:
        sinfo = ts.get("sourceInfo") or {}
        scode = ((sinfo.get("siteCode") or [{}])[0]).get("value") or ""
        if not scode:
            continue
        loc = ((sinfo.get("geoLocation") or {}).get("geogLocation")) or {}
        sla = float(loc.get("latitude") or lat)
        slo = float(loc.get("longitude") or lon)
        site = by_site.setdefault(scode, {
            "site_id": scode,
            "site_name": sinfo.get("siteName") or f"USGS {scode}",
            "lat": round(sla, 5),
            "lon": round(slo, 5),
            "distance_mi": _haversine_miles(lat, lon, sla, slo),
            "cfs": None,
            "gage_height_ft": None,
            "water_temp_f": None,
            "observed_at": "",
            "provider_id": prov["id"],
        })
        vcode = (((ts.get("variable") or {}).get("variableCode") or [{}])[0]).get("value") or ""
        vals = ((ts.get("values") or [{}])[0]).get("value") or []
        if not vals:
            continue
        latest = vals[-1]
        try:
            num = float(latest.get("value"))
        except Exception:
            continue
        if num <= -9999:
            continue
        if latest.get("dateTime") and not site["observed_at"]:
            site["observed_at"] = latest["dateTime"]
        if vcode == "00060":
            site["cfs"] = round(num, 1)
        elif vcode == "00065":
            site["gage_height_ft"] = round(num, 2)
        elif vcode == "00010":
            site["water_temp_f"] = round(num * 9.0 / 5.0 + 32.0, 1)

    sites = [s for s in by_site.values() if s["cfs"] is not None or s["gage_height_ft"] is not None]
    sites.sort(key=lambda x: x["distance_mi"])
    return sites[:8]


def query_open_meteo_flood(prov: Dict[str, Any], lat: float, lon: float) -> List[Dict[str, Any]]:
    """Global keyless Open-Meteo GloFAS modeled river discharge fallback."""
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "river_discharge",
        "forecast_days": "4",
    })
    raw = _http_get_bytes(f"{prov['url']}?{q}", timeout=12)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    vals = daily.get("river_discharge") or []
    if not vals or vals[0] is None:
        return []
    m3s_now = float(vals[0])
    m3s_next = float(vals[1]) if len(vals) > 1 and vals[1] is not None else m3s_now
    trend = "rising" if m3s_next > m3s_now * 1.08 else ("falling" if m3s_next < m3s_now * 0.92 else "steady")
    return [{
        "site_id": "glofas_local",
        "site_name": f"Local Basin Discharge (GloFAS {trend})",
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "distance_mi": 0.0,
        "cfs": round(m3s_now * 35.3147, 1),
        "discharge_m3s": round(m3s_now, 2),
        "flow_trend": trend,
        "gage_height_ft": None,
        "water_temp_f": None,
        "observed_at": times[0] if times else "",
        "provider_id": prov["id"],
    }]


def fetch_stream_gauges_cascade(lat: float, lon: float, force_refresh: bool = False) -> Dict[str, Any]:
    """Cascade across live river gauge providers (USGS NWIS -> Global Open-Meteo Flood) with 30m cache."""
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gis_cache_tiles (
            cache_key TEXT PRIMARY KEY,
            layer_type TEXT NOT NULL,
            bbox_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )"""
    )
    ckey = f"stream_gauges:{round(lat, 2)}:{round(lon, 2)}"
    row = conn.execute("SELECT fetched_at, payload_json FROM gis_cache_tiles WHERE cache_key = ?", (ckey,)).fetchone()
    if row and not force_refresh and _is_cache_fresh(row["fetched_at"], ttl_days=(30.0 / 1440.0)):
        cached = json.loads(row["payload_json"])
        return {**cached, "from_cache": True, "offline_fallback": False, "fetched_at": row["fetched_at"]}

    bbox = (lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1)
    provs = load_providers(layer="stream_gauges", bbox=bbox)
    gauges: List[Dict[str, Any]] = []
    used: List[str] = []
    errors: List[str] = []

    for p in provs:
        t0 = time.monotonic()
        try:
            drv = p.get("driver")
            if drv == "usgs_nwis":
                res = query_usgs_nwis_gauges(p, lat, lon)
            elif drv == "open_meteo_flood":
                res = query_open_meteo_flood(p, lat, lon)
            else:
                continue
            record_provider_health(p["id"], "stream_gauges", "healthy", int((time.monotonic() - t0) * 1000))
            if res:
                gauges.extend(res)
                used.append(p["id"])
                break
        except Exception as exc:
            record_provider_health(p["id"], "stream_gauges", "degraded", int((time.monotonic() - t0) * 1000), str(exc))
            errors.append(f"{p['id']}: {exc}")

    if gauges:
        ts = now_iso()
        out = {"status": "ok", "gauges": gauges, "providers_used": used, "warnings": errors}
        conn.execute(
            "INSERT OR REPLACE INTO gis_cache_tiles (cache_key, layer_type, bbox_json, fetched_at, payload_json) VALUES (?, 'stream_gauges', ?, ?, ?)",
            (ckey, json.dumps(list(bbox)), ts, json.dumps(out)),
        )
        conn.commit()
        return {**out, "from_cache": False, "offline_fallback": False, "fetched_at": ts}

    if row:
        cached = json.loads(row["payload_json"])
        return {**cached, "from_cache": True, "offline_fallback": True, "fetched_at": row["fetched_at"], "warnings": errors}

    return {"status": "ok", "gauges": [], "providers_used": [], "warnings": errors}


# ── Weather Cascade (Open-Meteo Global -> NOAA/NWS US Failover -> SQLite Cache) ─

_COMPASS_TO_DEG: Dict[str, float] = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}


def query_open_meteo_weather(prov: Dict[str, Any], lat: float, lon: float, days: int = 3) -> Dict[str, Any]:
    """Fetch Open-Meteo hourly forecast via resilient HTTP retry."""
    params = urllib.parse.urlencode({
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "hourly": ",".join([
            "temperature_2m", "apparent_temperature", "relative_humidity_2m", "dew_point_2m",
            "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "pressure_msl",
            "precipitation_probability", "precipitation", "snowfall", "cloud_cover",
            "shortwave_radiation", "visibility", "weather_code",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
        "forecast_days": str(max(1, min(7, int(days)))),
    })
    raw = _http_get_bytes(f"{prov['url']}?{params}", timeout=14, retries=2)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, dict) or not (data.get("hourly") or {}).get("time"):
        raise RuntimeError("Open-Meteo returned empty hourly forecast")
    return data


def _nws_text_to_wmo_and_cloud(short_fc: str) -> Tuple[int, float]:
    s = (short_fc or "").lower()
    if "thunder" in s or "storm" in s:
        return 95, 95.0
    if "snow" in s or "flurr" in s:
        return 71, 90.0
    if "rain" in s or "shower" in s:
        return 61, 88.0
    if "drizzle" in s:
        return 51, 85.0
    if "fog" in s or "mist" in s:
        return 45, 92.0
    if "overcast" in s or "mostly cloudy" in s or "cloudy" in s:
        return 3, 82.0
    if "partly" in s:
        return 2, 48.0
    if "mostly sunny" in s or "mostly clear" in s:
        return 1, 20.0
    return 0, 5.0


def query_noaa_nws_weather(prov: Dict[str, Any], lat: float, lon: float, days: int = 3) -> Dict[str, Any]:
    """US National Weather Service (api.weather.gov) hourly failover normalized to Open-Meteo hourly schema."""
    pts_url = f"{prov['url'].rstrip('/')}/{lat:.4f},{lon:.4f}"
    raw_pt = _http_get_bytes(pts_url, timeout=12, retries=1, extra_headers={"Accept": "application/geo+json"})
    pt_data = json.loads(raw_pt.decode("utf-8", errors="replace"))
    props = pt_data.get("properties") or {}
    hourly_url = props.get("forecastHourly")
    if not hourly_url:
        raise RuntimeError("NOAA/NWS did not return forecastHourly URL for location")
    tz_name = props.get("timeZone") or "America/Chicago"

    raw_h = _http_get_bytes(hourly_url, timeout=14, retries=1, extra_headers={"Accept": "application/geo+json"})
    h_data = json.loads(raw_h.decode("utf-8", errors="replace"))
    periods = ((h_data.get("properties") or {}).get("periods")) or []
    if not periods:
        raise RuntimeError("NOAA/NWS returned 0 hourly periods")

    max_hrs = max(24, min(168, int(days) * 24))
    utc_off_sec = -21600
    times, temps, feels, rhum, dewp, wspd, wdir, wgst, pres, pprob, pmm, snow, cloud, srad, vis, wcode = (
        [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], []
    )
    for per in periods[:max_hrs]:
        st = str(per.get("startTime") or "")
        if len(st) >= 16:
            times.append(st[:16])
            m_off = re.search(r"([+-])(\d{2}):(\d{2})$", st)
            if m_off:
                sign = 1 if m_off.group(1) == "+" else -1
                utc_off_sec = sign * (int(m_off.group(2)) * 3600 + int(m_off.group(3)) * 60)
        else:
            continue
        t_val = float(per.get("temperature") or 45.0)
        if str(per.get("temperatureUnit") or "F").upper() == "C":
            t_val = t_val * 9.0 / 5.0 + 32.0
        temps.append(t_val)
        feels.append(t_val)
        rh_v = ((per.get("relativeHumidity") or {}).get("value"))
        rhum.append(float(rh_v) if rh_v is not None else 65.0)
        dp_c = ((per.get("dewpoint") or {}).get("value"))
        dewp.append((float(dp_c) * 9.0 / 5.0 + 32.0) if dp_c is not None else (t_val - 8.0))

        ws_str = str(per.get("windSpeed") or "5 mph")
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", ws_str)]
        ws_v = (sum(nums) / len(nums)) if nums else 5.0
        wspd.append(ws_v)
        wgst.append(max(nums) if nums else ws_v)
        wd_str = str(per.get("windDirection") or "NW").strip().upper()
        wdir.append(_COMPASS_TO_DEG.get(wd_str, 315.0))
        pres.append(1013.2)
        pp_v = ((per.get("probabilityOfPrecipitation") or {}).get("value"))
        pp_i = int(pp_v) if pp_v is not None else 0
        pprob.append(pp_i)
        wc, cc = _nws_text_to_wmo_and_cloud(per.get("shortForecast") or "")
        pmm.append(0.8 if wc in (51, 61, 95) and pp_i >= 40 else 0.0)
        snow.append(0.5 if wc == 71 and pp_i >= 40 else 0.0)
        cloud.append(cc)
        srad.append(320.0 if per.get("isDaytime") and cc < 50 else (110.0 if per.get("isDaytime") else 0.0))
        vis.append(16093.4)
        wcode.append(wc)

    return {
        "timezone": tz_name,
        "utc_offset_seconds": utc_off_sec,
        "hourly": {
            "time": times, "temperature_2m": temps, "apparent_temperature": feels,
            "relative_humidity_2m": rhum, "dew_point_2m": dewp, "wind_speed_10m": wspd,
            "wind_direction_10m": wdir, "wind_gusts_10m": wgst, "pressure_msl": pres,
            "precipitation_probability": pprob, "precipitation": pmm, "snowfall": snow,
            "cloud_cover": cloud, "shortwave_radiation": srad, "visibility": vis, "weather_code": wcode,
        },
    }


def fetch_weather_cascade(lat: float, lon: float, days: int = 3, force_refresh: bool = False) -> Dict[str, Any]:
    """Cascade across weather providers (Open-Meteo -> NOAA/NWS US Failover) with 15-min cache + stale fallback."""
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gis_cache_tiles (
            cache_key TEXT PRIMARY KEY,
            layer_type TEXT NOT NULL,
            bbox_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )"""
    )
    ckey = f"weather:{round(lat, 2)}:{round(lon, 2)}:{int(days)}"
    row = conn.execute("SELECT fetched_at, payload_json FROM gis_cache_tiles WHERE cache_key = ?", (ckey,)).fetchone()
    if row and not force_refresh and _is_cache_fresh(row["fetched_at"], ttl_days=(15.0 / 1440.0)):
        cached = json.loads(row["payload_json"])
        return {**cached, "from_cache": True, "offline_fallback": False, "fetched_at": row["fetched_at"]}

    bbox = (lon - 0.05, lat - 0.05, lon + 0.05, lat + 0.05)
    provs = load_providers(layer="weather", bbox=bbox)
    errors: List[str] = []
    for p in provs:
        t0 = time.monotonic()
        try:
            drv = p.get("driver")
            if drv == "open_meteo_weather":
                raw_data = query_open_meteo_weather(p, lat, lon, days=days)
            elif drv == "noaa_nws_weather":
                raw_data = query_noaa_nws_weather(p, lat, lon, days=days)
            else:
                continue
            record_provider_health(p["id"], "weather", "healthy", int((time.monotonic() - t0) * 1000))
            ts = now_iso()
            out = {"status": "ok", "provider_id": p["id"], "provider_name": p.get("name") or p["id"], "raw": raw_data, "warnings": errors}
            conn.execute(
                "INSERT OR REPLACE INTO gis_cache_tiles (cache_key, layer_type, bbox_json, fetched_at, payload_json) VALUES (?, 'weather', ?, ?, ?)",
                (ckey, json.dumps(list(bbox)), ts, json.dumps(out)),
            )
            conn.commit()
            return {**out, "from_cache": False, "offline_fallback": False, "fetched_at": ts}
        except Exception as exc:
            record_provider_health(p["id"], "weather", "degraded", int((time.monotonic() - t0) * 1000), str(exc))
            errors.append(f"{p['id']}: {exc}")

    if row:
        cached = json.loads(row["payload_json"])
        return {**cached, "from_cache": True, "offline_fallback": True, "fetched_at": row["fetched_at"], "warnings": errors}

    raise RuntimeError("All weather providers failed: " + "; ".join(errors))


# ── WI DNR Lake Dossier & Official Contour (Bathymetric) Map PDF Caching ──────

def _render_pdf_first_page_png(pdf_path: str, png_path: str) -> bool:
    """Render page 1 of a downloaded lake contour PDF to PNG for Vision analysis & offline preview."""
    try:
        try:
            import pymupdf as _pdf_mod
        except ImportError:
            import fitz as _pdf_mod
        doc = _pdf_mod.open(pdf_path)
        if len(doc) > 0:
            pix = doc[0].get_pixmap(dpi=150)
            pix.save(png_path)
            doc.close()
            return os.path.isfile(png_path) and os.path.getsize(png_path) > 500
        doc.close()
    except Exception:
        pass
    try:
        prefix = os.path.splitext(png_path)[0]
        subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile", "-f", "1", "-l", "1", pdf_path, prefix], check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.isfile(png_path) and os.path.getsize(png_path) > 500
    except Exception:
        return False


def fetch_wi_dnr_lake_dossier(wbic: str, download_pdf: bool = True, force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch, parse, and cache official WI DNR Lake Detail facts & SWIMS Contour Map PDF by WBIC."""
    clean_wbic = re.sub(r"[^0-9]", "", str(wbic or ""))
    if not clean_wbic or clean_wbic == "0":
        return {"status": "error", "message": "Invalid WBIC"}

    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gis_cache_tiles (
            cache_key TEXT PRIMARY KEY,
            layer_type TEXT NOT NULL,
            bbox_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )"""
    )
    ckey = f"lake_dossier:wi:{clean_wbic}"
    row = conn.execute("SELECT fetched_at, payload_json FROM gis_cache_tiles WHERE cache_key = ?", (ckey,)).fetchone()
    if row and not force_refresh and _is_cache_fresh(row["fetched_at"], ttl_days=30.0):
        cached = json.loads(row["payload_json"])
        if not download_pdf or not cached.get("contour_map_url") or (cached.get("contour_map_pdf_path") and os.path.isfile(cached["contour_map_pdf_path"])):
            return {**cached, "from_cache": True, "fetched_at": row["fetched_at"]}

    url = f"https://apps.dnr.wi.gov/lakes/lakepages/LakeDetail.aspx?wbic={clean_wbic}"
    t0 = time.monotonic()
    try:
        raw = _http_get_bytes(url, timeout=14, retries=2)
        record_provider_health("wi_dnr_lake_dossier", "lake_dossier", "healthy", int((time.monotonic() - t0) * 1000))
    except Exception as exc:
        record_provider_health("wi_dnr_lake_dossier", "lake_dossier", "degraded", int((time.monotonic() - t0) * 1000), str(exc))
        if row:
            cached = json.loads(row["payload_json"])
            return {**cached, "from_cache": True, "offline_fallback": True, "fetched_at": row["fetched_at"]}
        return {"status": "error", "message": str(exc)}

    page_html = raw.decode("utf-8", errors="replace")
    # Strip scripts/styles for clean text extraction
    clean_txt = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", page_html, flags=re.S | re.I)
    clean_txt = _html.unescape(re.sub(r"<[^>]+>", " ", clean_txt))
    clean_txt = re.sub(r"\s+", " ", clean_txt).strip()

    # Extract Overview paragraph
    ov_m = re.search(r"id=\"[^\"]*divOverview[^\"]*\"[^>]*>(.*?)</div>", page_html, re.I | re.S)
    overview = _html.unescape(re.sub(r"<[^>]+>", " ", ov_m.group(1))) if ov_m else ""
    overview = re.sub(r"\s+", " ", overview).strip()
    search_blob = overview or clean_txt

    acres = None
    m_ac = re.search(r"([\d,]+(?:\.\d+)?)\s+acre", search_blob, re.I)
    if m_ac:
        try:
            acres = float(m_ac.group(1).replace(",", ""))
        except Exception:
            pass

    county = ""
    m_co = re.search(r"located in ([A-Za-z\s]+?)\s+County", search_blob, re.I)
    if m_co:
        county = m_co.group(1).strip()

    max_depth = None
    m_md = re.search(r"maximum depth of\s+([\d.]+)\s*feet|Max(?:imum)?\s*Depth[:\s]+([\d.]+)\s*(?:ft|feet)", clean_txt, re.I)
    if m_md:
        try:
            max_depth = float(m_md.group(1) or m_md.group(2))
        except Exception:
            pass

    mean_depth = None
    m_mean = re.search(r"Mean\s*Depth[:\s]+([\d.]+)\s*(?:ft|feet)", clean_txt, re.I)
    if m_mean:
        try:
            mean_depth = float(m_mean.group(1))
        except Exception:
            pass

    bottom = ""
    m_bot = re.search(r"Bottom[:\s]+([0-9%a-zA-Z,\s]+?)(?:Hydrologic|Waterbody|Trophic|Water Clarity|Invasive|Public|$)", clean_txt, re.I)
    if m_bot:
        cand = m_bot.group(1).strip(" ,.;:")
        if "%" in cand or any(k in cand.lower() for k in ("sand", "muck", "gravel", "rock", "silt")):
            bottom = cand[:180]

    clarity = ""
    m_cl = re.search(r"Water Clarity[:\s]+([A-Za-z0-9\s\.\-\(\)/]{3,60}?)(?:Trophic|Bottom|Fish|Public|Invasive|$)", clean_txt, re.I)
    if m_cl:
        clarity = m_cl.group(1).strip(" ,.;:")[:80]

    species: List[str] = []
    m_sp = re.search(r"Fish include\s+([^\.]+)\.", search_blob, re.I)
    if m_sp:
        raw_sp = re.sub(r"\band\b", ",", m_sp.group(1), flags=re.I)
        species = [s.strip() for s in raw_sp.split(",") if s.strip()]

    public_access = ""
    m_pa = re.search(r"(Visitors have access[^\.]+?\.)", search_blob, re.I)
    if m_pa:
        public_access = m_pa.group(1).strip()

    contour_url = ""
    m_pdf = re.search(r"href=[\x27\"]([^\x27\"]*DownloadDocument\?id=\d+[^\x27\"]*)[\x27\"][^>]*>[^<]*(?:Contour|Bathymetric|Lake Map)", page_html, re.I)
    if not m_pdf:
        m_pdf = re.search(r"href=[\x27\"](https?://[^\x27\"]*DownloadDocument\?id=\d+[^\x27\"]*)[\x27\"]", page_html, re.I)
    if m_pdf:
        contour_url = _html.unescape(m_pdf.group(1))
        if contour_url.startswith("/"):
            contour_url = "https://apps.dnr.wi.gov" + contour_url

    pdf_path = ""
    png_path = ""
    if download_pdf and contour_url:
        try:
            maps_dir = os.path.join(os.environ.get("MODULE_DATA_DIR", DEFAULT_DATA_DIR), "lake_maps")
            os.makedirs(maps_dir, exist_ok=True)
            target_pdf = os.path.join(maps_dir, f"wbic_{clean_wbic}.pdf")
            target_png = os.path.join(maps_dir, f"wbic_{clean_wbic}.png")
            if force_refresh or not (os.path.isfile(target_pdf) and os.path.getsize(target_pdf) > 1000):
                pdf_bytes = _http_get_bytes(contour_url, timeout=20, retries=1)
                if pdf_bytes.startswith(b"%PDF"):
                    with open(target_pdf, "wb") as f:
                        f.write(pdf_bytes)
            if os.path.isfile(target_pdf) and os.path.getsize(target_pdf) > 1000:
                pdf_path = target_pdf
                if force_refresh or not (os.path.isfile(target_png) and os.path.getsize(target_png) > 500):
                    _render_pdf_first_page_png(target_pdf, target_png)
                if os.path.isfile(target_png):
                    png_path = target_png
        except Exception:
            pass

    dossier = {
        "status": "ok",
        "wbic": clean_wbic,
        "county": county,
        "acres": acres,
        "max_depth_ft": max_depth,
        "mean_depth_ft": mean_depth,
        "bottom_substrate": bottom,
        "water_clarity": clarity,
        "species": species,
        "public_access": public_access,
        "overview": overview[:500],
        "dossier_url": url,
        "contour_map_url": contour_url,
        "contour_map_pdf_path": pdf_path,
        "contour_map_png_path": png_path,
    }
    ts = now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO gis_cache_tiles (cache_key, layer_type, bbox_json, fetched_at, payload_json) VALUES (?, 'lake_dossier', '[]', ?, ?)",
        (ckey, ts, json.dumps(dossier)),
    )
    conn.commit()
    return {**dossier, "from_cache": False, "fetched_at": ts}
