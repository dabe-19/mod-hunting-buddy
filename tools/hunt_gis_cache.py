#!/usr/bin/env python3
"""Good-Netizen Persistent Spatial Grid Cache for Hunting Buddy GIS.

Caches Wisconsin SCO parcels & WI DNR / USGS PAD-US public hunting lands in
hunt.db (`gis_cache_tiles`) keyed by rounded ~1.5 km grid cells with a 30-day
TTL and polite rate-limiting so Hunting Buddy never hammers public GIS APIs.
"""

import datetime
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters import get_provider_for_bbox
from adapters.public_lands import fetch_public_lands
from tools.hunt_db import get_conn, now_iso

TTL_DAYS = 30
GRID_STEP = 0.015  # ~1.2–1.6 km per cache cell
MAX_CELLS_PER_REQUEST = 9  # Prevent giant zoomed-out scrapes


def _ensure_cache_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gis_cache_tiles (
            cache_key TEXT PRIMARY KEY,
            layer_type TEXT NOT NULL,
            bbox_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """
    )
    conn.commit()


def _grid_cells_for_bbox(bbox: Tuple[float, float, float, float]) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    x0 = math.floor(min_lon / GRID_STEP)
    x1 = math.floor(max_lon / GRID_STEP)
    y0 = math.floor(min_lat / GRID_STEP)
    y1 = math.floor(max_lat / GRID_STEP)
    cells = []
    for ix in range(x0, x1 + 1):
        for iy in range(y0, y1 + 1):
            cbbox = (
                round(ix * GRID_STEP, 5),
                round(iy * GRID_STEP, 5),
                round((ix + 1) * GRID_STEP, 5),
                round((iy + 1) * GRID_STEP, 5),
            )
            cells.append((f"{ix}:{iy}", cbbox))
    return cells


def _is_fresh(fetched_at: str) -> bool:
    try:
        dt = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        age = datetime.datetime.now(datetime.timezone.utc) - dt
        return age.total_seconds() < TTL_DAYS * 86400
    except Exception:
        return False


def fetch_viewport_gis(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    property_id: Optional[int] = None,
    include_parcels: bool = True,
    include_public_lands: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    bbox = (float(min_lon), float(min_lat), float(max_lon), float(max_lat))
    cells = _grid_cells_for_bbox(bbox)
    if len(cells) > MAX_CELLS_PER_REQUEST:
        return {
            "status": "zoom_in",
            "message": f"Zoom in closer before loading parcels (viewport covers {len(cells)} grid cells; max {MAX_CELLS_PER_REQUEST} to stay polite to public GIS servers).",
            "parcels": [],
            "public_lands": [],
        }

    conn = get_conn()
    _ensure_cache_table(conn)

    parcels_by_pin: Dict[str, Dict[str, Any]] = {}
    public_by_id: Dict[str, Dict[str, Any]] = {}
    cache_hits = 0
    upstream_fetches = 0
    warnings: List[str] = []

    for cell_id, cbbox in cells:
        if include_parcels:
            ckey = f"parcels:{cell_id}"
            row = conn.execute("SELECT fetched_at, payload_json FROM gis_cache_tiles WHERE cache_key = ?", (ckey,)).fetchone()
            items = None
            if row and not force_refresh and _is_fresh(row["fetched_at"]):
                cache_hits += 1
                items = json.loads(row["payload_json"])
            else:
                prov = get_provider_for_bbox(cbbox)
                if prov:
                    if upstream_fetches > 0:
                        time.sleep(0.5)  # Polite pacing between grid cells
                    try:
                        recs = prov.fetch_parcels(cbbox, max_records=100)
                        upstream_fetches += 1
                        items = [
                            {
                                "parcel_pin": r.parcel_pin,
                                "owner_name": r.owner_name,
                                "acres": r.acres,
                                "access_status": r.access_status,
                                "plss_desc": r.plss_desc,
                                "county": (r.extra or {}).get("county", ""),
                                "mfl_value": (r.extra or {}).get("mfl_value", 0),
                                "provider_id": r.provider_id,
                                "geom": r.geom_geojson,
                            }
                            for r in recs
                        ]
                        conn.execute(
                            "INSERT OR REPLACE INTO gis_cache_tiles (cache_key, layer_type, bbox_json, fetched_at, payload_json) VALUES (?, 'parcels', ?, ?, ?)",
                            (ckey, json.dumps(list(cbbox)), now_iso(), json.dumps(items)),
                        )
                        conn.commit()
                    except Exception as exc:
                        warnings.append(str(exc))
                        items = json.loads(row["payload_json"]) if row else []
                else:
                    # Fallback to any custom declarative parcel provider registered in providers.user.json
                    try:
                        from adapters.registry import fetch_vector_layer_cascade
                        reg_p = fetch_vector_layer_cascade("parcels", cbbox, force_refresh=force_refresh, max_records=100)
                        items = [
                            {
                                "parcel_pin": str(f.get("parcel_pin") or f"p_{i}"),
                                "owner_name": str(f.get("owner_name") or "Owner"),
                                "acres": float(f.get("acres") or 0),
                                "access_status": "neighbor",
                                "plss_desc": str(f.get("plss_desc") or ""),
                                "county": str(f.get("county") or ""),
                                "provider_id": f.get("provider_id", "custom_parcel"),
                                "geom": f["geom"],
                            }
                            for i, f in enumerate(reg_p.get("features") or [])
                            if f.get("geom")
                        ]
                    except Exception:
                        items = json.loads(row["payload_json"]) if row else []
            for p in items or []:
                parcels_by_pin[p["parcel_pin"]] = p

        if include_public_lands:
            pkey = f"public_lands:{cell_id}"
            row = conn.execute("SELECT fetched_at, payload_json FROM gis_cache_tiles WHERE cache_key = ?", (pkey,)).fetchone()
            pub_items = None
            if row and not force_refresh and _is_fresh(row["fetched_at"]):
                cache_hits += 1
                pub_items = json.loads(row["payload_json"])
            else:
                if upstream_fetches > 0:
                    time.sleep(0.5)
                try:
                    pub_items = fetch_public_lands(cbbox, max_records=40)
                    upstream_fetches += 1
                    conn.execute(
                        "INSERT OR REPLACE INTO gis_cache_tiles (cache_key, layer_type, bbox_json, fetched_at, payload_json) VALUES (?, 'public_lands', ?, ?, ?)",
                        (pkey, json.dumps(list(cbbox)), now_iso(), json.dumps(pub_items)),
                    )
                    conn.commit()
                except Exception as exc:
                    warnings.append(str(exc))
                    pub_items = json.loads(row["payload_json"]) if row else []
            for pl in pub_items or []:
                public_by_id[pl["id"]] = pl

    # Optionally persist fetched parcels onto active property so Discord maps & GPX exports see them too
    if property_id is not None and parcels_by_pin:
        for p in list(parcels_by_pin.values())[:150]:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO parcels
                    (property_id, parcel_pin, owner_name, acres, access_status, plss_desc, geom_geojson, provider_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(property_id),
                        p["parcel_pin"],
                        p["owner_name"],
                        float(p.get("acres") or 0),
                        p.get("access_status", "neighbor"),
                        p.get("plss_desc", ""),
                        json.dumps(p["geom"]),
                        p.get("provider_id", "wi_sco"),
                    ),
                )
            except Exception:
                pass
        conn.commit()

    return {
        "status": "ok",
        "grid_cells": len(cells),
        "cache_hits": cache_hits,
        "upstream_fetches": upstream_fetches,
        "ttl_days": TTL_DAYS,
        "parcels": list(parcels_by_pin.values()),
        "public_lands": list(public_by_id.values()),
        "warnings": warnings,
    }


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(fetch_viewport_gis(**payload), indent=2))
