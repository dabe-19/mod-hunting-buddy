#!/usr/bin/env python3
"""Public Hunting Lands Adapter (WI DNR Public Access Lands + Nationwide USGS PAD-US).

Keyless, cache-first public hunting territory polygons:
  1. Nationwide USGS PAD-US (all 50 states: State Forests, County Forests,
     National Forests, Wildlife Management Areas, BLM, USFWS).
  2. Wisconsin DNR Public Access Lands (County Forests, BCPL, USFS, USFWS,
     Conservation Reserve) when viewport lies inside Wisconsin.
"""

import json
import ssl
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

PADUS_ENDPOINT = (
    "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
    "PADUS_Protection_Status_by_GAP_Status_Code/FeatureServer/0/query"
)

WI_DNR_LAYERS = [
    (0, "County Forest"),
    (1, "Board of Commissioners of Public Lands"),
    (2, "US Fish & Wildlife Service"),
    (4, "US Forest Service"),
    (6, "Conservation Reserve Land"),
]
WI_DNR_BASE = "https://dnrmaps.wi.gov/arcgis/rest/services/LF_DML/LF_DNR_PUBLIC_LAND_WTM_Ext/MapServer"


def _in_wisconsin(bbox: Tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return (-93.0 <= min_lon <= -86.2) and (42.4 <= min_lat <= 47.2)


def fetch_public_lands(
    bbox_wgs84: Tuple[float, float, float, float], max_records: int = 40
) -> List[Dict[str, Any]]:
    """Query public hunting lands intersecting bbox_wgs84 (min_lon, min_lat, max_lon, max_lat)."""
    min_lon, min_lat, max_lon, max_lat = bbox_wgs84
    ctx = ssl.create_default_context()
    results: List[Dict[str, Any]] = []
    seen = set()

    # 1. Nationwide USGS PAD-US (Open & Restricted public lands with clear Pub_Access tags)
    params = urllib.parse.urlencode(
        {
            "f": "geojson",
            "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "Unit_Nm,Pub_Access,Category,FeatClass,GAP_Sts,GIS_Acres",
            "returnGeometry": "true",
            "resultRecordCount": str(max_records),
        }
    )
    try:
        req = urllib.request.Request(
            f"{PADUS_ENDPOINT}?{params}",
            headers={"User-Agent": "BlueLodge-HuntingBuddy/1.1 (Personal Cache-First GIS)"},
        )
        with urllib.request.urlopen(req, timeout=18, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for feat in data.get("features") or []:
            geom = feat.get("geometry")
            props = feat.get("properties") or {}
            if not geom:
                continue
            unit = str(props.get("Unit_Nm") or props.get("Category") or "Public Land").strip()
            access = str(props.get("Pub_Access") or "OA").strip()
            if access.upper() == "XA":  # Closed to public access
                continue
            key = f"padus:{unit}:{props.get('GIS_Acres', '')}"
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "id": key,
                    "unit_name": unit,
                    "agency": str(props.get("FeatClass") or props.get("Category") or "Public Land"),
                    "pub_access": "Open Public Access" if access.upper() == "OA" else f"Access: {access}",
                    "acres": round(float(props.get("GIS_Acres") or 0), 1),
                    "source": "USGS PAD-US",
                    "geom": geom,
                }
            )
    except Exception:
        pass

    # 2. Supplement with WI DNR Public Access Lands when in Wisconsin
    if _in_wisconsin(bbox_wgs84):
        for layer_id, label in WI_DNR_LAYERS:
            q = urllib.parse.urlencode(
                {
                    "f": "geojson",
                    "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": "4326",
                    "outSR": "4326",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "resultRecordCount": "20",
                }
            )
            try:
                req = urllib.request.Request(
                    f"{WI_DNR_BASE}/{layer_id}/query?{q}",
                    headers={"User-Agent": "BlueLodge-HuntingBuddy/1.1 (Personal Cache-First GIS)"},
                )
                with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                for feat in data.get("features") or []:
                    geom = feat.get("geometry")
                    props = feat.get("properties") or {}
                    if not geom:
                        continue
                    unit = str(
                        props.get("PROP_NAME")
                        or props.get("COUNTY_NAM")
                        or props.get("UNIT_NAME")
                        or props.get("NAME")
                        or label
                    ).strip()
                    key = f"widnr:{layer_id}:{unit}:{props.get('OBJECTID', '')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(
                        {
                            "id": key,
                            "unit_name": unit,
                            "agency": label,
                            "pub_access": "Wisconsin Public Access Land",
                            "acres": round(float(props.get("GIS_ACRES") or props.get("ACRES") or 0), 1),
                            "source": "WI DNR PAL",
                            "geom": geom,
                        }
                    )
            except Exception:
                continue

    return results
