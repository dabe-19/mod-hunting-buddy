#!/usr/bin/env python3
"""Bidirectional GPX 1.1 & KML Exporter for onX Hunt / Phone GPS."""

import json
import os
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.hunt_db import DEFAULT_DATA_DIR, get_conn, get_property_bundle


def export_gpx(bundle: Dict[str, Any]) -> str:
    gpx = ET.Element("gpx", version="1.1", creator="BlueLodge-HuntingBuddy", xmlns="http://www.topografix.com/GPX/1/1")
    meta = ET.SubElement(gpx, "metadata")
    ET.SubElement(meta, "name").text = bundle["property"]["name"]

    for w in bundle.get("waypoints", []):
        wpt = ET.SubElement(gpx, "wpt", lat=f"{w['lat']:.6f}", lon=f"{w['lon']:.6f}")
        ET.SubElement(wpt, "name").text = w["name"]
        desc_parts = [f"Kind: {w['kind']}"]
        if w.get("ideal_winds"):
            desc_parts.append(f"Ideal Wind: {w['ideal_winds']}")
        if w.get("assigned_hunter"):
            desc_parts.append(f"Hunter: {w['assigned_hunter']}")
        if w.get("notes"):
            desc_parts.append(w["notes"])
        ET.SubElement(wpt, "desc").text = " | ".join(desc_parts)
        ET.SubElement(wpt, "sym").text = w["kind"]

    for r in bundle.get("routes", []):
        geom = r.get("geom") or {}
        if geom.get("type") == "LineString":
            trk = ET.SubElement(gpx, "trk")
            ET.SubElement(trk, "name").text = r["name"]
            seg = ET.SubElement(trk, "trkseg")
            for c in geom.get("coordinates", []):
                ET.SubElement(seg, "trkpt", lat=f"{c[1]:.6f}", lon=f"{c[0]:.6f}")

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(gpx, encoding="unicode")


def export_kml(bundle: Dict[str, Any]) -> str:
    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = ET.SubElement(kml, "Document")
    ET.SubElement(doc, "name").text = bundle["property"]["name"]

    for w in bundle.get("waypoints", []):
        pm = ET.SubElement(doc, "Placemark")
        ET.SubElement(pm, "name").text = w["name"]
        ET.SubElement(pm, "description").text = f"{w['kind']} | Ideal Wind: {w.get('ideal_winds','')} | {w.get('notes','')}"
        pt = ET.SubElement(pm, "Point")
        ET.SubElement(pt, "coordinates").text = f"{w['lon']:.6f},{w['lat']:.6f},0"

    for p in bundle.get("parcels", []):
        if p.get("access_status") not in ("owned", "permission"):
            continue
        geom = p.get("geom") or {}
        rings = []
        if geom.get("type") == "Polygon":
            rings = geom.get("coordinates", [])[:1]
        elif geom.get("type") == "MultiPolygon" and geom.get("coordinates"):
            rings = geom["coordinates"][0][:1]
        if rings:
            pm = ET.SubElement(doc, "Placemark")
            ET.SubElement(pm, "name").text = f"{p['owner_name']} ({p['acres']} ac)"
            poly = ET.SubElement(pm, "Polygon")
            ob = ET.SubElement(poly, "outerBoundaryIs")
            lr = ET.SubElement(ob, "LinearRing")
            ET.SubElement(lr, "coordinates").text = " ".join(f"{c[0]:.6f},{c[1]:.6f},0" for c in rings[0])

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(kml, encoding="unicode")


def run_export(property_id: Optional[int] = None, fmt: str = "gpx", channel_id: str = "") -> Dict[str, Any]:
    conn = get_conn()
    bundle = get_property_bundle(conn, property_id=property_id, channel_id=channel_id)
    if not bundle:
        return {"status": "error", "message": "Property not found"}
    fmt = fmt.lower()
    content = export_kml(bundle) if fmt == "kml" else export_gpx(bundle)
    ext = "kml" if fmt == "kml" else "gpx"
    safe_name = "".join(c if c.isalnum() else "_" for c in bundle["property"]["name"]).strip("_").lower() or "camp"
    out_dir = os.path.join(DEFAULT_DATA_DIR, "exports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{safe_name}_onx.{ext}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {
        "status": "ok",
        "format": ext,
        "file_path": out_path,
        "waypoints_count": len(bundle.get("waypoints", [])),
        "content": content,
    }


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(run_export(**payload), indent=2))
