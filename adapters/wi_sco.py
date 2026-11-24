#!/usr/bin/env python3
"""Wisconsin Statewide Parcel Map Adapter (WI SCO / V12 2026 ArcGIS REST API).
Free, keyless public parcel geometry, owner name, Parcel ID, acreage, MFL flag,
and PLSS descriptions covering all 72 Wisconsin counties statewide.
"""

import json
import ssl
import urllib.parse
import urllib.request
from typing import List, Tuple

from .base import ParcelRecord, StateParcelProvider

# Official Wisconsin Statewide Parcel Layer (UW-Madison SCO / DOA ArcGIS Online)
WI_PARCEL_ENDPOINTS = [
    "https://services3.arcgis.com/n6uYoouQZW75n5WI/arcgis/rest/services/Wisconsin_Statewide_Parcels_DB/FeatureServer/0/query",
    "https://services3.arcgis.com/n6uYoouQZW75n5WI/arcgis/rest/services/Wisconsin_Statewide_Parcels/FeatureServer/0/query",
]


class WisconsinSCOProvider(StateParcelProvider):
    state_code = "WI"
    provider_id = "wi_sco"
    display_name = "Wisconsin Statewide Parcel Map V12 (SCO/DOA)"

    def supports_bbox(self, bbox_wgs84: Tuple[float, float, float, float]) -> bool:
        min_lon, min_lat, max_lon, max_lat = bbox_wgs84
        return (-93.0 <= min_lon <= -86.2) and (42.4 <= min_lat <= 47.2)

    @staticmethod
    def parse_geojson_features(geojson: dict) -> List[ParcelRecord]:
        records: List[ParcelRecord] = []
        for feat in geojson.get("features") or []:
            geom = feat.get("geometry")
            props = feat.get("properties") or feat.get("attributes") or {}
            if not geom:
                continue
            pin = str(
                props.get("PARCELID")
                or props.get("TAXPARCELID")
                or props.get("STATEID")
                or props.get("OBJECTID")
                or "unknown"
            ).strip()
            owner = str(
                props.get("OWNERNME1")
                or props.get("OWNER_NAME")
                or props.get("CONAME")
                or "Private Owner"
            ).strip()
            if props.get("OWNERNME2"):
                n2 = str(props["OWNERNME2"]).strip()
                if n2 and n2.upper() != owner.upper():
                    owner = f"{owner} / {n2}"
            raw_acres = props.get("GISACRES") or props.get("DEEDACRES") or props.get("ASSDACRES") or props.get("ACRES") or 0
            try:
                acres = round(float(raw_acres), 2)
            except Exception:
                acres = 0.0
            mfl_val = props.get("MFLVALUE") or 0
            try:
                has_mfl = float(mfl_val) > 0
            except Exception:
                has_mfl = bool(str(mfl_val).strip() and str(mfl_val).strip() not in ("0", "0.0", "None"))

            plss_parts = [
                str(props.get(k) or "").strip()
                for k in ("PLACENAME", "CONAME", "PLSSDESC")
                if props.get(k)
            ]
            plss = " · ".join(plss_parts)[:140]
            upper_owner = owner.upper()
            if any(
                kw in upper_owner
                for kw in (
                    "STATE OF WISCONSIN",
                    "COUNTY OF",
                    "COUNTY LAND",
                    "DNR",
                    "DEPT OF NATURAL",
                    "UNITED STATES",
                    "USA ",
                    "TOWN OF",
                    "PUBLIC",
                    "COUNTY FOREST",
                    "NATIONAL FOREST",
                    "WILDLIFE",
                )
            ):
                access = "public"
            elif has_mfl:
                access = "mfl_enrolled"
            else:
                access = "neighbor"
            records.append(
                ParcelRecord(
                    parcel_pin=pin,
                    owner_name=owner,
                    acres=acres,
                    plss_desc=plss,
                    geom_geojson=geom,
                    access_status=access,
                    provider_id="wi_sco",
                    extra={
                        "county": str(props.get("CONAME") or ""),
                        "place": str(props.get("PLACENAME") or ""),
                        "tax_parcel_id": str(props.get("TAXPARCELID") or ""),
                        "mfl_value": mfl_val,
                    },
                )
            )
        return records

    def fetch_parcels(
        self, bbox_wgs84: Tuple[float, float, float, float], max_records: int = 100
    ) -> List[ParcelRecord]:
        min_lon, min_lat, max_lon, max_lat = bbox_wgs84
        params = urllib.parse.urlencode(
            {
                "f": "geojson",
                "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "outSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "PARCELID,TAXPARCELID,OWNERNME1,OWNERNME2,GISACRES,DEEDACRES,MFLVALUE,CONAME,PLACENAME",
                "returnGeometry": "true",
                "resultRecordCount": str(max_records),
            }
        )
        ctx = ssl.create_default_context()
        last_err = None
        for ep in WI_PARCEL_ENDPOINTS:
            url = f"{ep}?{params}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "BlueLodge-HuntingBuddy/1.1 (Personal Cache-First GIS)"},
            )
            try:
                with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data.get("features") is not None:
                    return self.parse_geojson_features(data)
            except Exception as e:
                last_err = e
                continue
        if last_err:
            raise RuntimeError(f"Wisconsin SCO parcel query failed: {last_err}")
        return []
