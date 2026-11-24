#!/usr/bin/env python3
"""Hunting Buddy Persistent SQLite Ledger (hunt.db).
Supports public-by-default properties with opt-in per-channel privacy isolation,
pluggable parcel polygons, stands/blinds/trail-cams, routes, sightings, and
live mobile GPS/compass heading telemetry + breadcrumb tracks.
"""

import datetime
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

DEFAULT_DATA_DIR = os.environ.get(
    "MODULE_DATA_DIR",
    os.path.join(
        os.environ.get("GEORGE_DIR", os.path.expanduser("~/blue-lodge/.george")),
        "module_data",
        "hunting-buddy",
    ),
)


def data_dir() -> str:
    d = os.environ.get("MODULE_DATA_DIR") or DEFAULT_DATA_DIR
    os.makedirs(d, exist_ok=True)
    return d


def db_path() -> str:
    return os.path.join(data_dir(), "hunt.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 15000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            state TEXT DEFAULT 'WI',
            county TEXT DEFAULT '',
            center_lat REAL NOT NULL,
            center_lon REAL NOT NULL,
            bbox_json TEXT DEFAULT '[]',
            channel_id TEXT DEFAULT '',
            visibility TEXT NOT NULL DEFAULT 'public', -- 'public' (default) | 'channel_private'
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS parcels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            parcel_pin TEXT NOT NULL,
            owner_name TEXT DEFAULT '',
            acres REAL DEFAULT 0.0,
            access_status TEXT DEFAULT 'neighbor', -- owned | permission | public | neighbor | no_hunt
            plss_desc TEXT DEFAULT '',
            geom_geojson TEXT NOT NULL,
            provider_id TEXT DEFAULT 'manual',
            UNIQUE(property_id, parcel_pin)
        );

        CREATE TABLE IF NOT EXISTS waypoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'stand', -- stand | blind | trail_cam | bedding | scrape | rub | funnel | parking | food_plot
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            ideal_winds TEXT DEFAULT '', -- comma-separated compass bearings e.g. 'NW,N,W'
            bad_winds TEXT DEFAULT '',
            assigned_hunter TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'walking_trail', -- walking_trail | atv_road | driving_road | entry_route | exit_route | deer_trail | stream | creek | current_seam | drift_line
            geom_geojson TEXT NOT NULL,
            notes TEXT DEFAULT '',
            direction_mode TEXT DEFAULT 'none', -- none | one_way | downstream | bidirectional
            flow_speed TEXT DEFAULT '', -- calm | slow | moderate | fast
            surface_type TEXT DEFAULT '', -- dirt | gravel | paved | grass | water
            length_yd REAL DEFAULT 0.0,
            source TEXT DEFAULT 'user', -- user | usgs_nhd | osm_overpass | wi_dnr
            created_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS trail_cam_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            waypoint_id INTEGER REFERENCES waypoints(id) ON DELETE SET NULL,
            captured_at TEXT NOT NULL,
            image_path TEXT DEFAULT '',
            species TEXT DEFAULT 'deer',
            antler_notes TEXT DEFAULT '',
            travel_dir TEXT DEFAULT '',
            wind_deg REAL,
            temp_f REAL,
            pressure_hpa REAL,
            vision_summary TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sightings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            waypoint_id INTEGER REFERENCES waypoints(id) ON DELETE SET NULL,
            hunter TEXT DEFAULT '',
            observed_at TEXT NOT NULL,
            summary TEXT NOT NULL,
            wind_deg REAL,
            pressure_trend TEXT DEFAULT '',
            harvested INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS map_renders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            scenario_name TEXT DEFAULT '',
            wind_deg REAL,
            wind_mph REAL,
            thermal_mode TEXT DEFAULT 'neutral',
            basemap TEXT DEFAULT 'usgs_aerial',
            png_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- Live Mobile GPS & Compass Heading Telemetry Bridge
        CREATE TABLE IF NOT EXISTS hunter_telemetry (
            hunter_id TEXT PRIMARY KEY,
            property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
            hunter_name TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            heading_deg REAL,
            speed_mps REAL DEFAULT 0.0,
            accuracy_m REAL DEFAULT 0.0,
            battery_pct REAL,
            status TEXT DEFAULT 'active', -- active | in_stand | stalking | returning | emergency
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hunter_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hunter_id TEXT NOT NULL,
            property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            heading_deg REAL,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hunter_tracks_hid ON hunter_tracks(hunter_id, recorded_at DESC);

        -- Persistent Hunting Plans (Agent & Operator Tactical Briefings)
        CREATE TABLE IF NOT EXISTS hunting_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            scenario_name TEXT DEFAULT '',
            wind_deg REAL,
            wind_mph REAL,
            thermal_mode TEXT DEFAULT 'neutral',
            shooting_light_json TEXT DEFAULT '{}',
            top_stands_json TEXT DEFAULT '[]',
            blown_stands_json TEXT DEFAULT '[]',
            briefing_markdown TEXT DEFAULT '',
            map_png_path TEXT DEFAULT '',
            source_task_id TEXT DEFAULT '',
            created_by TEXT DEFAULT 'george',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hunting_plans_prop ON hunting_plans(property_id, created_at DESC);

        -- Removable Tactical Map Annotations (Pins, Approach Routes, Travel Corridors, Bedding/Avoid Zones)
        CREATE TABLE IF NOT EXISTS map_annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            plan_id INTEGER REFERENCES hunting_plans(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'callout', -- stand_pick | approach_route | deer_corridor | bedding_zone | pinch_point | food_source | avoid_zone | callout
            geom_type TEXT NOT NULL DEFAULT 'Point', -- Point | LineString | Polygon
            geom_geojson TEXT NOT NULL,
            color TEXT DEFAULT '#a855f7',
            notes TEXT DEFAULT '',
            visible INTEGER NOT NULL DEFAULT 1,
            created_by TEXT DEFAULT 'george',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_map_annotations_prop ON map_annotations(property_id, plan_id);

        -- Topographic Elevation Grids (30m DEM / User-Uploaded LiDAR ASCII & CSV)
        CREATE TABLE IF NOT EXISTS terrain_grids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            source TEXT NOT NULL DEFAULT 'open_meteo_copernicus_30m_dem',
            summary_json TEXT NOT NULL DEFAULT '{}',
            grid_json TEXT NOT NULL DEFAULT '{}',
            contours_geojson TEXT NOT NULL DEFAULT '{"type":"FeatureCollection","features":[]}',
            thermal_vectors_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        );

        -- Topographic & Habitat Landmarks (Water Sources, Ridges, Saddles, Drainages, Benches, Uploaded Topo Features)
        CREATE TABLE IF NOT EXISTS topo_landmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'water_source', -- water_source | ridge | saddle | drainage | bench | food_plot | thick_cover | access_gate | contour
            geom_type TEXT NOT NULL DEFAULT 'Point', -- Point | LineString | Polygon
            geom_geojson TEXT NOT NULL,
            elevation_ft REAL,
            source TEXT NOT NULL DEFAULT 'user', -- user | auto_dem | upload:*
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_topo_landmarks_prop ON topo_landmarks(property_id, kind);

        -- Saved Plan Follow-Up Q&A Thread (Hunter Questions & Agent Answers tied to a Hunting Plan)
        CREATE TABLE IF NOT EXISTS plan_followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES hunting_plans(id) ON DELETE CASCADE,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            answer_markdown TEXT NOT NULL,
            created_by TEXT DEFAULT 'hiram',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_plan_followups_plan ON plan_followups(plan_id, id ASC);

        -- Pluggable Spatial Provider Health & Schema-Drift Watchdog
        CREATE TABLE IF NOT EXISTS provider_health (
            provider_id TEXT PRIMARY KEY,
            layer TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'untested', -- healthy | degraded | schema_drift | offline
            last_checked_at TEXT DEFAULT '',
            last_ok_at TEXT DEFAULT '',
            consecutive_failures INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            detected_fields_json TEXT DEFAULT '[]'
        );

        -- Fishing Structure Pins (Phase 2 — Deep Holes, Drop-Offs, Weed Edges, Rock Piles, Eddies, Boat Launches)
        CREATE TABLE IF NOT EXISTS fish_structures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'drop_off',
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            depth_ft REAL,
            bottom_type TEXT DEFAULT '',
            target_species TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fish_structures_prop ON fish_structures(property_id, kind);

        -- Directional Water Current Flow Vectors (Phase 2 — River Current, Inflow/Outflow, Wind Drift, Eddies)
        CREATE TABLE IF NOT EXISTS water_flows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            flow_to_deg REAL NOT NULL,
            speed_class TEXT DEFAULT 'moderate', -- calm | slow | moderate | fast
            flow_type TEXT DEFAULT 'river_current', -- river_current | wind_drift | inflow | outflow | eddy
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_water_flows_prop ON water_flows(property_id);

        -- Underwater Depth Soundings & Interpolated Bathymetry Contours (Phase 2)
        CREATE TABLE IF NOT EXISTS bathymetry_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            waterbody_name TEXT NOT NULL DEFAULT 'Camp Water',
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            depth_ft REAL NOT NULL,
            source TEXT DEFAULT 'user', -- user | sonar_csv | sonar_gpx | geojson
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bathymetry_points_prop ON bathymetry_points(property_id, waterbody_name);

        CREATE TABLE IF NOT EXISTS bathymetry_grids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            waterbody_name TEXT NOT NULL DEFAULT 'Camp Water',
            source TEXT DEFAULT 'interpolated_soundings',
            max_depth_ft REAL DEFAULT 0.0,
            avg_depth_ft REAL DEFAULT 0.0,
            contours_geojson TEXT NOT NULL DEFAULT '{"type":"FeatureCollection","features":[]}',
            grid_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        -- Persistent Cached & User-Editable Lakes / Reservoirs / Ponds & Official Dossiers (v2.2.0)
        CREATE TABLE IF NOT EXISTS waterbodies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            waterbody_type TEXT NOT NULL DEFAULT 'lake', -- lake | reservoir | flowage | pond | river_pool | slough
            wbic TEXT DEFAULT '',
            river_system TEXT DEFAULT '',
            county TEXT DEFAULT '',
            acres REAL,
            max_depth_ft REAL,
            mean_depth_ft REAL,
            bottom_substrate TEXT DEFAULT '',
            water_clarity TEXT DEFAULT '',
            species_json TEXT DEFAULT '[]',
            public_access TEXT DEFAULT '',
            contour_map_url TEXT DEFAULT '',
            contour_map_pdf_path TEXT DEFAULT '',
            contour_map_png_path TEXT DEFAULT '',
            dossier_url TEXT DEFAULT '',
            source TEXT DEFAULT 'user', -- user | wi_dnr_24k_lakes | usgs_nhd_waterbodies | osm_overpass_waterbodies
            user_edited INTEGER DEFAULT 0,
            geom_geojson TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_waterbodies_prop ON waterbodies(property_id, name);
        """
    )
    # Non-destructive column migrations for existing hunt.db installations
    _migrations = [
        ("routes", "direction_mode", "TEXT DEFAULT 'none'"),
        ("routes", "flow_speed", "TEXT DEFAULT ''"),
        ("routes", "surface_type", "TEXT DEFAULT ''"),
        ("routes", "length_yd", "REAL DEFAULT 0.0"),
        ("routes", "source", "TEXT DEFAULT 'user'"),
        ("routes", "created_at", "TEXT DEFAULT ''"),
        ("hunting_plans", "plan_mode", "TEXT DEFAULT 'hunt'"),
        ("waterbodies", "overlay_bounds_json", "TEXT DEFAULT ''"),
        ("waterbodies", "overlay_opacity", "REAL DEFAULT 0.68"),
        ("waterbodies", "overlay_visible", "INTEGER DEFAULT 0"),
        ("waterbodies", "overlay_transform_json", "TEXT DEFAULT ''"),
    ]
    for tbl, col, col_def in _migrations:
        try:
            existing_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_def}")
        except Exception:
            pass
    conn.commit()


def parse_json_stdin(raw: Optional[str] = None) -> Dict[str, Any]:
    """Resilient JSON stdin parser: tolerates concatenated JSON objects or trailing noise."""
    import sys as _sys
    text = (_sys.stdin.read() if raw is None else raw) or ""
    text = text.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        try:
            dec = json.JSONDecoder()
            idx = text.find("{")
            if idx >= 0:
                parsed, _ = dec.raw_decode(text, idx)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            pass
    return {}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def list_properties(conn: sqlite3.Connection, channel_id: str = "") -> List[Dict[str, Any]]:
    if channel_id:
        rows = conn.execute(
            "SELECT * FROM properties WHERE visibility = 'public' OR channel_id = ? ORDER BY id DESC",
            (channel_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM properties ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_property_bundle(conn: sqlite3.Connection, property_id: Optional[int] = None, channel_id: str = "") -> Optional[Dict[str, Any]]:
    if property_id is not None:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            return None
        if prop["visibility"] == "channel_private" and channel_id and prop["channel_id"] != channel_id:
            return None
    else:
        props = list_properties(conn, channel_id)
        if not props:
            return None
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (props[0]["id"],)).fetchone()
    pid = prop["id"]
    parcels = [dict(r) for r in conn.execute("SELECT * FROM parcels WHERE property_id = ?", (pid,)).fetchall()]
    for p in parcels:
        try:
            p["geom"] = json.loads(p["geom_geojson"])
        except Exception:
            p["geom"] = None
    waypoints = [dict(r) for r in conn.execute("SELECT * FROM waypoints WHERE property_id = ? ORDER BY id", (pid,)).fetchall()]
    routes = [dict(r) for r in conn.execute("SELECT * FROM routes WHERE property_id = ?", (pid,)).fetchall()]
    for r in routes:
        try:
            r["geom"] = json.loads(r["geom_geojson"])
        except Exception:
            r["geom"] = None
    sightings = [dict(r) for r in conn.execute("SELECT * FROM sightings WHERE property_id = ? ORDER BY observed_at DESC LIMIT 30", (pid,)).fetchall()]
    hunters = [dict(r) for r in conn.execute("SELECT * FROM hunter_telemetry WHERE property_id = ? OR property_id IS NULL ORDER BY updated_at DESC", (pid,)).fetchall()]
    plans = list_plans(conn, pid)
    annotations = list_annotations(conn, pid)
    topo_landmarks = [dict(r) for r in conn.execute("SELECT * FROM topo_landmarks WHERE property_id = ? ORDER BY id DESC", (pid,)).fetchall()]
    for lm in topo_landmarks:
        try:
            lm["geom"] = json.loads(lm["geom_geojson"])
        except Exception:
            lm["geom"] = None
    tg_row = conn.execute("SELECT source, summary_json, contours_geojson, thermal_vectors_json, updated_at FROM terrain_grids WHERE property_id = ? ORDER BY updated_at DESC LIMIT 1", (pid,)).fetchone()
    terrain = None
    if tg_row:
        try:
            tsum = json.loads(tg_row["summary_json"] or "{}")
            terrain = {
                "has_dem": True,
                "source": tg_row["source"],
                "updated_at": tg_row["updated_at"],
                **tsum,
                "contours": json.loads(tg_row["contours_geojson"] or '{"type":"FeatureCollection","features":[]}'),
                "thermal_vectors": json.loads(tg_row["thermal_vectors_json"] or "[]"),
            }
        except Exception:
            terrain = None
    fish_structures = [dict(r) for r in conn.execute("SELECT * FROM fish_structures WHERE property_id = ? ORDER BY id DESC", (pid,)).fetchall()]
    water_flows = [dict(r) for r in conn.execute("SELECT * FROM water_flows WHERE property_id = ? ORDER BY id DESC", (pid,)).fetchall()]
    bathymetry_points = [dict(r) for r in conn.execute("SELECT * FROM bathymetry_points WHERE property_id = ? ORDER BY id DESC", (pid,)).fetchall()]
    bg_rows = conn.execute("SELECT id, waterbody_name, source, max_depth_ft, avg_depth_ft, contours_geojson, updated_at FROM bathymetry_grids WHERE property_id = ? ORDER BY updated_at DESC", (pid,)).fetchall()
    bathymetry_grids = []
    for bgr in bg_rows:
        bd = dict(bgr)
        try:
            bd["contours"] = json.loads(bd.pop("contours_geojson") or '{"type":"FeatureCollection","features":[]}')
        except Exception:
            bd["contours"] = {"type": "FeatureCollection", "features": []}
        bathymetry_grids.append(bd)
    wb_rows = conn.execute("SELECT * FROM waterbodies WHERE property_id = ? ORDER BY user_edited DESC, COALESCE(acres, 0) DESC, name ASC", (pid,)).fetchall()
    waterbodies = []
    for wbr in wb_rows:
        wd = dict(wbr)
        try:
            wd["geom"] = json.loads(wd["geom_geojson"]) if wd.get("geom_geojson") else None
        except Exception:
            wd["geom"] = None
        try:
            wd["species"] = json.loads(wd.get("species_json") or "[]")
        except Exception:
            wd["species"] = []
        wd["user_edited"] = bool(wd.get("user_edited", 0))
        wd["has_cached_map"] = bool(wd.get("contour_map_png_path") and os.path.isfile(wd["contour_map_png_path"]))
        try:
            wd["overlay_bounds"] = json.loads(wd["overlay_bounds_json"]) if wd.get("overlay_bounds_json") else None
        except Exception:
            wd["overlay_bounds"] = None
        try:
            wd["overlay_transform"] = json.loads(wd["overlay_transform_json"]) if wd.get("overlay_transform_json") else None
        except Exception:
            wd["overlay_transform"] = None
        wd["overlay_visible"] = bool(wd.get("overlay_visible", 0))
        wd["overlay_opacity"] = float(wd.get("overlay_opacity") if wd.get("overlay_opacity") is not None else 0.68)
        waterbodies.append(wd)
    return {
        "property": dict(prop),
        "parcels": parcels,
        "waypoints": waypoints,
        "routes": routes,
        "sightings": sightings,
        "hunters": hunters,
        "plans": plans,
        "annotations": annotations,
        "topo_landmarks": topo_landmarks,
        "terrain": terrain,
        "fish_structures": fish_structures,
        "water_flows": water_flows,
        "bathymetry_points": bathymetry_points,
        "bathymetry_grids": bathymetry_grids,
        "waterbodies": waterbodies,
    }


ANNOTATION_KIND_COLORS: Dict[str, str] = {
    "stand_pick": "#22c55e",
    "approach_route": "#10b981",
    "deer_corridor": "#f59e0b",
    "bedding_zone": "#f97316",
    "pinch_point": "#a855f7",
    "food_source": "#84cc16",
    "avoid_zone": "#ef4444",
    "callout": "#38bdf8",
}


def _normalize_coord_pair(pt: Any) -> Optional[List[float]]:
    if not isinstance(pt, (list, tuple)) or len(pt) < 2:
        return None
    a, b = float(pt[0]), float(pt[1])
    # Smart auto-fix for Western Hemisphere LLMs that pass [lat, lon] instead of GeoJSON [lon, lat]
    # e.g., [45.920, -89.250] -> [-89.250, 45.920]
    if -90.0 <= a <= 90.0 and (b < -60.0 or b > 90.0) and a > 0:
        return [b, a]
    return [a, b]


def normalize_annotation_geometry(
    geom: Optional[Dict[str, Any]] = None,
    geom_type: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    coordinates: Optional[Any] = None,
) -> Dict[str, Any]:
    """Normalize Point / LineString / Polygon input into valid RFC 7946 GeoJSON geometry."""
    if isinstance(geom, dict) and geom.get("type") and geom.get("coordinates") is not None:
        gtype = str(geom["type"])
        coords = geom["coordinates"]
    elif lat is not None and lon is not None:
        return {"type": "Point", "coordinates": [float(lon), float(lat)]}
    elif coordinates is not None:
        gtype = geom_type or "Point"
        coords = coordinates
    else:
        raise ValueError("Provide either (lat, lon), coordinates, or a GeoJSON geometry object.")

    g_low = gtype.lower()
    if g_low == "point":
        pair = _normalize_coord_pair(coords)
        if not pair:
            raise ValueError("Invalid Point coordinates")
        return {"type": "Point", "coordinates": pair}
    if g_low in ("linestring", "line", "route"):
        pts = [p for p in (_normalize_coord_pair(c) for c in coords) if p]
        if len(pts) < 2:
            raise ValueError("LineString requires at least 2 coordinate pairs")
        return {"type": "LineString", "coordinates": pts}
    if g_low in ("polygon", "zone"):
        ring_src = coords[0] if (isinstance(coords, list) and coords and isinstance(coords[0], list) and coords[0] and isinstance(coords[0][0], (list, tuple))) else coords
        pts = [p for p in (_normalize_coord_pair(c) for c in ring_src) if p]
        if len(pts) < 3:
            raise ValueError("Polygon requires at least 3 vertices")
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return {"type": "Polygon", "coordinates": [pts]}
    raise ValueError(f"Unsupported geometry type: {gtype}")


def list_plans(conn: sqlite3.Connection, property_id: Optional[int] = None, limit: int = 30) -> List[Dict[str, Any]]:
    if property_id is not None:
        rows = conn.execute(
            "SELECT * FROM hunting_plans WHERE property_id = ? ORDER BY id DESC LIMIT ?",
            (int(property_id), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM hunting_plans ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for jkey, outkey, default in (
            ("shooting_light_json", "shooting_light", {}),
            ("top_stands_json", "top_stands", []),
            ("blown_stands_json", "blown_stands", []),
        ):
            try:
                d[outkey] = json.loads(d.get(jkey) or json.dumps(default))
            except Exception:
                d[outkey] = default
        cnt = conn.execute("SELECT COUNT(*) FROM map_annotations WHERE plan_id = ?", (d["id"],)).fetchone()[0]
        vis_cnt = conn.execute("SELECT COUNT(*) FROM map_annotations WHERE plan_id = ? AND visible = 1", (d["id"],)).fetchone()[0]
        d["annotation_count"] = int(cnt)
        d["visible_annotation_count"] = int(vis_cnt)
        d["followups"] = list_plan_followups(conn, int(d["id"]))
        out.append(d)
    return out


def list_plan_followups(conn: sqlite3.Connection, plan_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM plan_followups WHERE plan_id = ? ORDER BY id ASC",
        (int(plan_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def add_plan_followup(
    conn: sqlite3.Connection,
    plan_id: int,
    question: str,
    answer_markdown: str,
    property_id: Optional[int] = None,
    created_by: str = "hiram",
) -> Dict[str, Any]:
    row = conn.execute("SELECT property_id FROM hunting_plans WHERE id = ?", (int(plan_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Plan {plan_id} not found"}
    pid = int(property_id) if property_id is not None else int(row["property_id"])
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO plan_followups (plan_id, property_id, question, answer_markdown, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (int(plan_id), pid, str(question).strip(), str(answer_markdown).strip(), created_by or "hiram", ts),
    )
    conn.commit()
    return {
        "status": "ok",
        "followup_id": cur.lastrowid,
        "plan_id": int(plan_id),
        "property_id": pid,
        "created_at": ts,
    }


def get_plan(conn: sqlite3.Connection, plan_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM hunting_plans WHERE id = ?", (int(plan_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Plan {plan_id} not found"}
    d = dict(row)
    for jkey, outkey, default in (
        ("shooting_light_json", "shooting_light", {}),
        ("top_stands_json", "top_stands", []),
        ("blown_stands_json", "blown_stands", []),
    ):
        try:
            d[outkey] = json.loads(d.get(jkey) or json.dumps(default))
        except Exception:
            d[outkey] = default
    d["annotations"] = list_annotations(conn, property_id=int(d["property_id"]), plan_id=int(plan_id))
    d["all_camp_annotations"] = list_annotations(conn, property_id=int(d["property_id"]))
    d["followups"] = list_plan_followups(conn, int(plan_id))
    return {"status": "ok", "plan": d}


def list_annotations(
    conn: sqlite3.Connection,
    property_id: Optional[int] = None,
    plan_id: Optional[int] = None,
    visible_only: bool = False,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if property_id is not None:
        clauses.append("property_id = ?")
        params.append(int(property_id))
    if plan_id is not None:
        clauses.append("plan_id = ?")
        params.append(int(plan_id))
    if visible_only:
        clauses.append("visible = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM map_annotations {where} ORDER BY id DESC", params).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["geom"] = json.loads(d["geom_geojson"])
        except Exception:
            d["geom"] = None
        d["visible"] = bool(d.get("visible", 1))
        out.append(d)
    return out


def add_annotation(
    conn: sqlite3.Connection,
    property_id: int,
    label: str,
    kind: str = "callout",
    geom: Optional[Dict[str, Any]] = None,
    geom_type: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    coordinates: Optional[Any] = None,
    color: Optional[str] = None,
    notes: str = "",
    plan_id: Optional[int] = None,
    created_by: str = "george",
) -> Dict[str, Any]:
    norm = normalize_annotation_geometry(geom=geom, geom_type=geom_type, lat=lat, lon=lon, coordinates=coordinates)
    k = (kind or "callout").strip().lower()
    col = (color or ANNOTATION_KIND_COLORS.get(k) or "#a855f7").strip()
    cur = conn.execute(
        """
        INSERT INTO map_annotations
          (property_id, plan_id, label, kind, geom_type, geom_geojson, color, notes, visible, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            int(property_id),
            int(plan_id) if plan_id is not None else None,
            (label or k.replace("_", " ").title()).strip()[:120],
            k,
            norm["type"],
            json.dumps(norm),
            col,
            (notes or "").strip(),
            created_by or "george",
            now_iso(),
        ),
    )
    conn.commit()
    return {
        "status": "ok",
        "annotation_id": cur.lastrowid,
        "property_id": int(property_id),
        "plan_id": int(plan_id) if plan_id is not None else None,
        "label": (label or k).strip()[:120],
        "kind": k,
        "geom_type": norm["type"],
        "color": col,
        "geom": norm,
    }


def _is_raw_transcript(text: str) -> bool:
    """Detect raw CLI agent loop transcripts so they never pollute clean saved plans."""
    if not text:
        return False
    head = text[:1200]
    markers = (
        "── Agentic Loop:",
        "● [fifo_parallel]",
        "Loop Spec:",
        "FIFO Chan:",
        "✓ [gate] Verification passed",
        "✓ Agentic Loop completed:",
    )
    return any(m in head or m in text[-1200:] for m in markers)


def _load_clean_final_reply(source_task_id: str) -> str:
    """Read the clean synthesized final_reply.md for a completed workflow task if present."""
    tid = (source_task_id or "").strip()
    if not tid or "/" in tid or ".." in tid:
        return ""
    george_dir = os.environ.get("GEORGE_DIR") or os.path.dirname(os.path.dirname(data_dir()))
    runs_dir = os.path.join(george_dir, "runs")
    if not os.path.isdir(runs_dir):
        return ""
    try:
        candidates = []
        for entry in os.listdir(runs_dir):
            if entry == tid or entry.startswith(tid + "_"):
                fp = os.path.join(runs_dir, entry, "final_reply.md")
                if os.path.isfile(fp):
                    candidates.append((os.path.getmtime(fp), fp))
        if not candidates:
            return ""
        candidates.sort(reverse=True)
        with open(candidates[0][1], "r", encoding="utf-8", errors="replace") as f:
            txt = f.read().strip()
        return "" if _is_raw_transcript(txt) else txt
    except Exception:
        return ""


def _extract_heading_title(md: str) -> str:
    for line in (md or "").splitlines()[:8]:
        s = line.strip()
        if s.startswith("#"):
            t = s.lstrip("#").strip()
            if len(t) >= 4:
                return t[:160]
    return ""


def _infer_plan_mode(plan_mode: Optional[str], title: str = "", scenario_name: str = "", briefing_markdown: str = "") -> str:
    pm = (plan_mode or "").strip().lower()
    if pm in ("hunt", "fish"):
        return pm
    blob = f"{title} {scenario_name} {briefing_markdown[:300]}".lower()
    if any(k in blob for k in ("fishing", "bathymetr", "contour map", "walleye", "musky", "bass", "panfish", "river gauge", "bite index", "reservoir", "lake structure", "fish structure")):
        return "fish"
    return "hunt"


def save_plan(
    conn: sqlite3.Connection,
    property_id: int,
    title: str,
    briefing_markdown: str = "",
    scenario_name: str = "",
    wind_deg: Optional[float] = None,
    wind_mph: Optional[float] = None,
    thermal_mode: str = "neutral",
    shooting_light: Optional[Any] = None,
    top_stands: Optional[List[Any]] = None,
    blown_stands: Optional[List[Any]] = None,
    map_png_path: str = "",
    source_task_id: str = "",
    created_by: str = "george",
    annotations: Optional[List[Dict[str, Any]]] = None,
    plan_mode: Optional[str] = None,
) -> Dict[str, Any]:
    clean_md = (briefing_markdown or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "  ")
    src_tid = (source_task_id or "").strip()
    if _is_raw_transcript(clean_md):
        clean_md = _load_clean_final_reply(src_tid)
    resolved_mode = _infer_plan_mode(plan_mode, title, scenario_name, clean_md)
    default_title = "Fishing & Structure Plan" if resolved_mode == "fish" else "Tactical Hunting Plan"
    raw_title = (title or "").strip()
    if not raw_title or raw_title in ("Tactical Hunting Plan", "Fishing & Structure Plan"):
        raw_title = _extract_heading_title(clean_md) or raw_title or default_title

    # Deduplicate by source_task_id if a plan for this exact task already exists; keep the richer CLEAN markdown!
    if src_tid:
        existing = conn.execute(
            "SELECT id, title, briefing_markdown, top_stands_json, blown_stands_json, plan_mode FROM hunting_plans WHERE property_id = ? AND source_task_id = ? ORDER BY id DESC LIMIT 1",
            (int(property_id), src_tid),
        ).fetchone()
        if existing:
            return update_plan(
                conn,
                plan_id=int(existing["id"]),
                title=raw_title if raw_title and len(raw_title) > 4 else None,
                briefing_markdown=clean_md if (clean_md and not _is_raw_transcript(clean_md)) else None,
                top_stands=top_stands if top_stands else None,
                blown_stands=blown_stands if blown_stands else None,
                map_png_path=map_png_path or None,
                plan_mode=resolved_mode,
                source_task_id=src_tid,
                only_if_richer=True,
            )

    cur = conn.execute(
        """
        INSERT INTO hunting_plans
          (property_id, title, scenario_name, wind_deg, wind_mph, thermal_mode,
           shooting_light_json, top_stands_json, blown_stands_json,
           briefing_markdown, map_png_path, source_task_id, created_by, created_at, plan_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(property_id),
            raw_title[:160],
            (scenario_name or "").strip()[:120],
            float(wind_deg) if wind_deg is not None else None,
            float(wind_mph) if wind_mph is not None else None,
            (thermal_mode or "neutral").strip(),
            json.dumps(shooting_light or {}),
            json.dumps(top_stands or []),
            json.dumps(blown_stands or []),
            clean_md,
            map_png_path or "",
            src_tid,
            created_by or "george",
            now_iso(),
            resolved_mode,
        ),
    )
    plan_id = cur.lastrowid
    conn.commit()

    ann_ids: List[int] = []
    for a in (annotations or []):
        try:
            res = add_annotation(
                conn,
                property_id=int(property_id),
                plan_id=plan_id,
                label=a.get("label") or a.get("name") or "Plan Object",
                kind=a.get("kind") or "callout",
                geom=a.get("geom") or a.get("geometry"),
                geom_type=a.get("geom_type") or a.get("type"),
                lat=a.get("lat"),
                lon=a.get("lon"),
                coordinates=a.get("coordinates"),
                color=a.get("color"),
                notes=a.get("notes") or a.get("rationale") or "",
                created_by=created_by or "george",
            )
            ann_ids.append(int(res["annotation_id"]))
        except Exception:
            continue

    return {
        "status": "ok",
        "plan_id": plan_id,
        "property_id": int(property_id),
        "title": raw_title[:160],
        "plan_mode": resolved_mode,
        "annotation_ids": ann_ids,
        "annotations_created": len(ann_ids),
    }


def update_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    title: Optional[str] = None,
    briefing_markdown: Optional[str] = None,
    top_stands: Optional[List[Any]] = None,
    blown_stands: Optional[List[Any]] = None,
    map_png_path: Optional[str] = None,
    plan_mode: Optional[str] = None,
    source_task_id: Optional[str] = None,
    only_if_richer: bool = False,
) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM hunting_plans WHERE id = ?", (int(plan_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Plan {plan_id} not found"}
    sets = []
    vals: List[Any] = []

    src_tid = str(source_task_id or row["source_task_id"] or "").strip()
    existing_md = row["briefing_markdown"] or ""
    existing_is_raw = _is_raw_transcript(existing_md)

    # Pick the richest CLEAN markdown among caller input and final_reply.md on disk (NEVER raw CLI transcript!)
    cand_md = ""
    if briefing_markdown is not None:
        raw_c = str(briefing_markdown).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "  ").strip()
        if raw_c and not _is_raw_transcript(raw_c):
            cand_md = raw_c
    disk_reply = _load_clean_final_reply(src_tid) if src_tid else ""
    if disk_reply and len(disk_reply) > len(cand_md):
        cand_md = disk_reply

    if cand_md:
        if existing_is_raw or not only_if_richer or len(cand_md) > len(existing_md) + 40:
            sets.append("briefing_markdown = ?")
            vals.append(cand_md)

    # Upgrade generic title if caller supplied one or if we can derive a heading from the clean report
    next_title = str(title).strip() if (title is not None and str(title).strip()) else ""
    cur_title = str(row["title"] or "").strip()
    if not next_title and cur_title in ("", "Tactical Hunting Plan", "Fishing & Structure Plan"):
        next_title = _extract_heading_title(cand_md or existing_md)
    if next_title and next_title != cur_title:
        sets.append("title = ?")
        vals.append(next_title[:160])

    if top_stands is not None and isinstance(top_stands, list) and len(top_stands) > 0:
        sets.append("top_stands_json = ?")
        vals.append(json.dumps(top_stands))
    if blown_stands is not None and isinstance(blown_stands, list) and len(blown_stands) > 0:
        sets.append("blown_stands_json = ?")
        vals.append(json.dumps(blown_stands))
    if map_png_path is not None and str(map_png_path).strip():
        sets.append("map_png_path = ?")
        vals.append(str(map_png_path).strip())
    if plan_mode is not None and str(plan_mode).strip().lower() in ("hunt", "fish"):
        sets.append("plan_mode = ?")
        vals.append(str(plan_mode).strip().lower())
    if source_task_id is not None and str(source_task_id).strip():
        sets.append("source_task_id = ?")
        vals.append(str(source_task_id).strip())
    if sets:
        vals.append(int(plan_id))
        conn.execute(f"UPDATE hunting_plans SET {', '.join(sets)} WHERE id = ?", tuple(vals))
        conn.commit()
    return {
        "status": "ok",
        "plan_id": int(plan_id),
        "property_id": int(row["property_id"]),
        "updated": bool(sets),
    }


def delete_plan(conn: sqlite3.Connection, plan_id: int) -> Dict[str, Any]:
    conn.execute("DELETE FROM hunting_plans WHERE id = ?", (int(plan_id),))
    conn.commit()
    return {"status": "ok", "deleted_plan_id": int(plan_id)}


def toggle_annotation(conn: sqlite3.Connection, ann_id: int, visible: Optional[bool] = None) -> Dict[str, Any]:
    row = conn.execute("SELECT visible FROM map_annotations WHERE id = ?", (int(ann_id),)).fetchone()
    if not row:
        return {"status": "error", "message": f"Annotation {ann_id} not found"}
    new_v = (0 if row["visible"] else 1) if visible is None else (1 if visible else 0)
    conn.execute("UPDATE map_annotations SET visible = ? WHERE id = ?", (new_v, int(ann_id)))
    conn.commit()
    return {"status": "ok", "annotation_id": int(ann_id), "visible": bool(new_v)}


def toggle_plan_annotations(conn: sqlite3.Connection, plan_id: int, visible: bool) -> Dict[str, Any]:
    conn.execute("UPDATE map_annotations SET visible = ? WHERE plan_id = ?", (1 if visible else 0, int(plan_id)))
    conn.commit()
    return {"status": "ok", "plan_id": int(plan_id), "visible": bool(visible)}


def delete_annotation(conn: sqlite3.Connection, ann_id: int) -> Dict[str, Any]:
    conn.execute("DELETE FROM map_annotations WHERE id = ?", (int(ann_id),))
    conn.commit()
    return {"status": "ok", "deleted_annotation_id": int(ann_id)}


def clear_annotations(conn: sqlite3.Connection, property_id: int, plan_id: Optional[int] = None) -> Dict[str, Any]:
    if plan_id is not None:
        cur = conn.execute("DELETE FROM map_annotations WHERE property_id = ? AND plan_id = ?", (int(property_id), int(plan_id)))
    else:
        cur = conn.execute("DELETE FROM map_annotations WHERE property_id = ?", (int(property_id),))
    conn.commit()
    return {"status": "ok", "deleted_count": cur.rowcount}


def set_property_privacy(conn: sqlite3.Connection, property_id: int, visibility: str, channel_id: str = "") -> Dict[str, Any]:
    if visibility not in ("public", "channel_private"):
        raise ValueError("visibility must be 'public' or 'channel_private'")
    if visibility == "channel_private" and channel_id:
        conn.execute("UPDATE properties SET visibility = ?, channel_id = ? WHERE id = ?", (visibility, channel_id, property_id))
    else:
        conn.execute("UPDATE properties SET visibility = ? WHERE id = ?", (visibility, property_id))
    conn.commit()
    return {"status": "ok", "property_id": property_id, "visibility": visibility}
