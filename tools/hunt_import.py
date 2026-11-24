#!/usr/bin/env python3
"""Multi-format GIS, onX Hunt KML/KMZ/GPX, GeoJSON, PDF & Trail-Cam Importer."""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters import get_provider_for_bbox
from tools.hunt_db import get_conn, now_iso


def _bbox_around(lat: float, lon: float, radius_deg_lat: float = 0.012, radius_deg_lon: float = 0.016) -> Tuple[float, float, float, float]:
    return (lon - radius_deg_lon, lat - radius_deg_lat, lon + radius_deg_lon, lat + radius_deg_lat)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_gpx(xml_text: str) -> Tuple[List[dict], List[dict]]:
    root = ET.fromstring(xml_text)
    waypoints, routes = [], []
    for elem in root.iter():
        t = _strip_ns(elem.tag)
        if t == "wpt":
            lat = float(elem.attrib["lat"])
            lon = float(elem.attrib["lon"])
            name = desc = sym = ""
            for ch in elem:
                ct = _strip_ns(ch.tag)
                if ct == "name" and ch.text:
                    name = ch.text.strip()
                elif ct in ("desc", "cmt") and ch.text:
                    desc = ch.text.strip()
                elif ct == "sym" and ch.text:
                    sym = ch.text.strip().lower()
            kind = "stand"
            low = (name + " " + sym + " " + desc).lower()
            for k in ("blind", "trail_cam", "cam", "bedding", "scrape", "rub", "funnel", "parking", "food_plot"):
                if k.replace("_", " ") in low or k in low:
                    kind = "trail_cam" if k == "cam" else k
                    break
            waypoints.append({"name": name or "Waypoint", "kind": kind, "lat": lat, "lon": lon, "notes": desc})
        elif t in ("trk", "rte"):
            rname = "Track"
            coords = []
            for ch in elem.iter():
                ct = _strip_ns(ch.tag)
                if ct == "name" and ch.text and rname == "Track":
                    rname = ch.text.strip()
                elif ct in ("trkpt", "rtept"):
                    coords.append([float(ch.attrib["lon"]), float(ch.attrib["lat"])])
            if len(coords) >= 2:
                routes.append({"name": rname, "kind": "entry", "geom": {"type": "LineString", "coordinates": coords}})
    return waypoints, routes


def parse_kml(xml_text: str) -> Tuple[List[dict], List[dict], List[dict]]:
    root = ET.fromstring(xml_text)
    waypoints, routes, parcels = [], [], []
    for pm in root.iter():
        if _strip_ns(pm.tag) != "Placemark":
            continue
        name = desc = ""
        for ch in pm:
            ct = _strip_ns(ch.tag)
            if ct == "name" and ch.text:
                name = ch.text.strip()
            elif ct == "description" and ch.text:
                desc = re.sub(r"<[^>]+>", " ", ch.text).strip()
        for sub in pm.iter():
            st = _strip_ns(sub.tag)
            if st == "Point":
                for c in sub.iter():
                    if _strip_ns(c.tag) == "coordinates" and c.text:
                        parts = c.text.strip().split(",")
                        if len(parts) >= 2:
                            waypoints.append({
                                "name": name or "Pin",
                                "kind": "stand",
                                "lat": float(parts[1]),
                                "lon": float(parts[0]),
                                "notes": desc[:240],
                            })
            elif st == "LineString":
                for c in sub.iter():
                    if _strip_ns(c.tag) == "coordinates" and c.text:
                        coords = []
                        for tok in c.text.strip().split():
                            p = tok.split(",")
                            if len(p) >= 2:
                                coords.append([float(p[0]), float(p[1])])
                        if len(coords) >= 2:
                            routes.append({"name": name or "Path", "kind": "entry", "geom": {"type": "LineString", "coordinates": coords}})
            elif st == "Polygon":
                for c in sub.iter():
                    if _strip_ns(c.tag) == "coordinates" and c.text:
                        ring = []
                        for tok in c.text.strip().split():
                            p = tok.split(",")
                            if len(p) >= 2:
                                ring.append([float(p[0]), float(p[1])])
                        if len(ring) >= 3:
                            if ring[0] != ring[-1]:
                                ring.append(ring[0])
                            parcels.append({
                                "parcel_pin": name or f"kml_{len(parcels)+1}",
                                "owner_name": name or "Imported Boundary",
                                "acres": 0.0,
                                "access_status": "owned",
                                "plss_desc": desc[:120],
                                "geom": {"type": "Polygon", "coordinates": [ring]},
                                "provider_id": "kml_import",
                            })
                            break
    return waypoints, routes, parcels


def extract_exif_gps(image_path: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"captured_at": now_iso(), "lat": None, "lon": None}
    try:
        from PIL import Image, ExifTags
        img = Image.open(image_path)
        exif = img.getexif()
        if not exif:
            return out
        tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        dt = tag_map.get("DateTimeOriginal") or tag_map.get("DateTime")
        if dt:
            out["captured_at"] = str(dt).replace(":", "-", 2)
    except Exception:
        pass
    return out


def extract_pdf_parcel_pins(pdf_path: str) -> Dict[str, Any]:
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:8])
    pins = sorted(set(re.findall(r"\b\d{2,3}-\d{4,6}(?:\.\d+)?\b", text)))
    acres = re.findall(r"(\d+(?:\.\d+)?)\s*(?:acres?|ac\b)", text, re.I)
    return {"pins": pins[:25], "acres_mentions": acres[:15], "excerpt": text[:600]}


def import_property(
    name: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    file_path: Optional[str] = None,
    file_content: Optional[str] = None,
    file_ext: Optional[str] = None,
    fetch_state_parcels: bool = True,
    channel_id: str = "",
    visibility: str = "public",
    property_id: Optional[int] = None,
) -> Dict[str, Any]:
    conn = get_conn()
    waypoints: List[dict] = []
    routes: List[dict] = []
    parcels: List[dict] = []
    pdf_info = None
    cam_info = None

    ext = (file_ext or (os.path.splitext(file_path)[1] if file_path else "")).lower().lstrip(".")
    if file_path and os.path.isfile(file_path):
        if ext == "kmz":
            with zipfile.ZipFile(file_path, "r") as zf:
                kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
                if kml_names:
                    file_content = zf.read(kml_names[0]).decode("utf-8", errors="replace")
                    ext = "kml"
        elif ext in ("gpx", "kml", "geojson", "json"):
            file_content = open(file_path, "r", encoding="utf-8", errors="replace").read()
        elif ext == "pdf":
            pdf_info = extract_pdf_parcel_pins(file_path)
        elif ext in ("jpg", "jpeg", "png"):
            cam_info = extract_exif_gps(file_path)

    if file_content:
        if ext == "gpx":
            w, r = parse_gpx(file_content)
            waypoints.extend(w)
            routes.extend(r)
        elif ext == "kml":
            w, r, p = parse_kml(file_content)
            waypoints.extend(w)
            routes.extend(r)
            parcels.extend(p)
        elif ext in ("geojson", "json"):
            gj = json.loads(file_content)
            feats = gj.get("features") if isinstance(gj, dict) and gj.get("type") == "FeatureCollection" else [gj]
            for f in feats or []:
                g = f.get("geometry") or {}
                pr = f.get("properties") or {}
                gt = g.get("type")
                if gt == "Point":
                    c = g.get("coordinates", [])
                    if len(c) >= 2:
                        waypoints.append({"name": pr.get("name", "Pin"), "kind": pr.get("kind", "stand"), "lat": c[1], "lon": c[0], "notes": pr.get("notes", "")})
                elif gt == "LineString":
                    routes.append({"name": pr.get("name", "Route"), "kind": pr.get("kind", "entry"), "geom": g})
                elif gt in ("Polygon", "MultiPolygon"):
                    parcels.append({
                        "parcel_pin": str(pr.get("parcel_pin") or pr.get("PARCELID") or f"gj_{len(parcels)+1}"),
                        "owner_name": str(pr.get("owner_name") or pr.get("OWNERNME1") or "Imported Parcel"),
                        "acres": float(pr.get("acres") or pr.get("GISACRES") or 0.0),
                        "access_status": str(pr.get("access_status") or "owned"),
                        "plss_desc": str(pr.get("plss_desc") or ""),
                        "geom": g,
                        "provider_id": "geojson_import",
                    })

    # Determine property center
    if lat is None or lon is None:
        if waypoints:
            lat = sum(w["lat"] for w in waypoints) / len(waypoints)
            lon = sum(w["lon"] for w in waypoints) / len(waypoints)
        elif parcels:
            c0 = parcels[0]["geom"]["coordinates"][0][0]
            if isinstance(c0[0], list):
                c0 = c0[0]
            lon, lat = float(c0[0]), float(c0[1])
        elif property_id is not None:
            row = conn.execute("SELECT center_lat, center_lon FROM properties WHERE id = ?", (property_id,)).fetchone()
            if row:
                lat, lon = row["center_lat"], row["center_lon"]

    if lat is None or lon is None:
        return {"status": "error", "message": "Could not determine property lat/lon (provide lat/lon or GPX/KML/GeoJSON)."}

    bbox = _bbox_around(float(lat), float(lon))
    provider_used = None
    provider_err = None
    if fetch_state_parcels:
        prov = get_provider_for_bbox(bbox)
        if prov:
            provider_used = prov.provider_id
            try:
                fetched = prov.fetch_parcels(bbox)
                for rec in fetched:
                    parcels.append({
                        "parcel_pin": rec.parcel_pin,
                        "owner_name": rec.owner_name,
                        "acres": rec.acres,
                        "access_status": rec.access_status,
                        "plss_desc": rec.plss_desc,
                        "geom": rec.geom_geojson,
                        "provider_id": rec.provider_id,
                    })
            except Exception as e:
                provider_err = str(e)

    ts = now_iso()
    if property_id is None:
        cur = conn.execute(
            "INSERT INTO properties (name, state, center_lat, center_lon, bbox_json, channel_id, visibility, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name or f"Camp {lat:.4f},{lon:.4f}", "WI", float(lat), float(lon), json.dumps(list(bbox)), channel_id, visibility if visibility in ("public", "channel_private") else "public", ts),
        )
        property_id = cur.lastrowid

    p_added = 0
    for p in parcels:
        conn.execute(
            """INSERT OR REPLACE INTO parcels (property_id, parcel_pin, owner_name, acres, access_status, plss_desc, geom_geojson, provider_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (property_id, p["parcel_pin"], p["owner_name"], p["acres"], p["access_status"], p["plss_desc"], json.dumps(p["geom"]), p["provider_id"]),
        )
        p_added += 1

    w_added = 0
    for w in waypoints:
        conn.execute(
            """INSERT INTO waypoints (property_id, name, kind, lat, lon, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (property_id, w["name"], w["kind"], w["lat"], w["lon"], w.get("notes", ""), ts),
        )
        w_added += 1

    r_added = 0
    for r in routes:
        conn.execute(
            "INSERT INTO routes (property_id, name, kind, geom_geojson, notes) VALUES (?, ?, ?, ?, ?)",
            (property_id, r["name"], r["kind"], json.dumps(r["geom"]), r.get("notes", "")),
        )
        r_added += 1

    if cam_info and file_path:
        conn.execute(
            "INSERT INTO trail_cam_events (property_id, captured_at, image_path) VALUES (?, ?, ?)",
            (property_id, cam_info["captured_at"], file_path),
        )

    conn.commit()
    return {
        "status": "ok",
        "property_id": property_id,
        "center": [float(lat), float(lon)],
        "parcels_imported": p_added,
        "waypoints_imported": w_added,
        "routes_imported": r_added,
        "state_provider": provider_used,
        "provider_warning": provider_err,
        "pdf_extracted": pdf_info,
        "trail_cam_exif": cam_info,
    }


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read() or "{}")
    if "property_name" in payload and "name" not in payload:
        payload["name"] = payload.pop("property_name")
    payload.pop("state", None)
    payload.pop("radius_m", None)
    print(json.dumps(import_property(**payload), indent=2))
