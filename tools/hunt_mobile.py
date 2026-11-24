#!/usr/bin/env python3
"""Mobile GPS & Compass Heading Telemetry Bridge for Android App / Mobile Browser PWA.
Supports SHA-256 hashed per-hunter X-Beacon-Token auth, live position updates,
breadcrumb history, and Blue Lodge Event Bus emission (`hunting/hunter_position_update`).
"""

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.hunt_db import DEFAULT_DATA_DIR, get_conn, now_iso

TOKENS_PATH = os.path.join(DEFAULT_DATA_DIR, "beacon_tokens.json")


def _hash_tok(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def _tokens_path() -> str:
    d = os.environ.get("MODULE_DATA_DIR") or DEFAULT_DATA_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "beacon_tokens.json")


def _load_tokens() -> dict:
    tp = _tokens_path()
    if os.path.isfile(tp):
        try:
            with open(tp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tokens": {}}


def _save_tokens(data: dict) -> None:
    tp = _tokens_path()
    tmp = tp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, tp)


def issue_beacon_token(hunter_id: str, hunter_name: str) -> Dict[str, Any]:
    raw = "hb_" + secrets.token_urlsafe(24)
    data = _load_tokens()
    data["tokens"][hunter_id] = {
        "hunter_id": hunter_id,
        "hunter_name": hunter_name,
        "sha256": _hash_tok(raw),
        "created_at": now_iso(),
    }
    _save_tokens(data)
    return {
        "status": "ok",
        "hunter_id": hunter_id,
        "hunter_name": hunter_name,
        "beacon_token": raw,
        "endpoint": "/api/modules/hunting-buddy/mobile/beacon",
        "header": "X-Beacon-Token",
        "note": "Store securely on Android device; only its SHA-256 hash is persisted on the server.",
    }


def verify_beacon_token(presented: str, hunter_id: Optional[str] = None) -> Optional[dict]:
    if not presented:
        return None
    ph = _hash_tok(presented)
    data = _load_tokens()
    for hid, meta in (data.get("tokens") or {}).items():
        if hunter_id and hid != hunter_id:
            continue
        if hmac.compare_digest(ph, meta.get("sha256", "")):
            return meta
    return None


def ingest_beacon(payload: Dict[str, Any], lodge_authed: bool = False, beacon_token: str = "") -> Dict[str, Any]:
    if not lodge_authed:
        tok_meta = verify_beacon_token(beacon_token, payload.get("hunter_id"))
        if not tok_meta:
            return {"status": "unauthorized", "message": "Invalid or missing X-Beacon-Token"}
        payload.setdefault("hunter_id", tok_meta["hunter_id"])
        payload.setdefault("hunter_name", tok_meta["hunter_name"])

    hunter_id = str(payload.get("hunter_id") or "").strip()
    hunter_name = str(payload.get("hunter_name") or hunter_id or "Hunter").strip()
    if not hunter_id:
        return {"status": "error", "message": "hunter_id is required"}

    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except Exception:
        return {"status": "error", "message": "valid numeric lat and lon are required"}
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return {"status": "error", "message": "lat/lon out of WGS84 bounds"}

    heading = payload.get("heading_deg")
    heading = float(heading) % 360.0 if heading is not None else None
    speed = float(payload.get("speed_mps") or 0.0)
    acc = float(payload.get("accuracy_m") or 0.0)
    batt = float(payload["battery_pct"]) if payload.get("battery_pct") is not None else None
    status = str(payload.get("status") or "active")[:32]
    prop_id = payload.get("property_id")
    ts = now_iso()

    conn = get_conn()
    conn.execute(
        """INSERT INTO hunter_telemetry (hunter_id, property_id, hunter_name, lat, lon, heading_deg, speed_mps, accuracy_m, battery_pct, status, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(hunter_id) DO UPDATE SET
             property_id=COALESCE(excluded.property_id, hunter_telemetry.property_id),
             hunter_name=excluded.hunter_name,
             lat=excluded.lat, lon=excluded.lon,
             heading_deg=excluded.heading_deg, speed_mps=excluded.speed_mps,
             accuracy_m=excluded.accuracy_m, battery_pct=excluded.battery_pct,
             status=excluded.status, updated_at=excluded.updated_at""",
        (hunter_id, prop_id, hunter_name, lat, lon, heading, speed, acc, batt, status, ts),
    )
    conn.execute(
        "INSERT INTO hunter_tracks (hunter_id, property_id, lat, lon, heading_deg, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (hunter_id, prop_id, lat, lon, heading, ts),
    )
    conn.commit()

    # Best-effort Event Bus emission (non-blocking)
    lodge_dir = os.environ.get("LODGE_DIR", os.path.expanduser("~/blue-lodge"))
    ev_fifo = os.path.join(os.environ.get("GEORGE_DIR", os.path.join(lodge_dir, ".george")), "ipc", "event_bus.fifo")
    if os.path.exists(ev_fifo):
        ev = json.dumps({"hunter_id": hunter_id, "hunter_name": hunter_name, "lat": lat, "lon": lon, "heading_deg": heading, "status": status})
        try:
            subprocess.Popen(
                ["bash", "-c", 'source "$1/lib/ipc/event_bus.sh" 2>/dev/null && event_bus_emit "hunting/hunter_position_update" "$2" >/dev/null 2>&1 || true', "_", lodge_dir, ev],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    return {
        "status": "ok",
        "hunter_id": hunter_id,
        "hunter_name": hunter_name,
        "lat": lat,
        "lon": lon,
        "heading_deg": heading,
        "updated_at": ts,
    }


def list_hunters(property_id: Optional[int] = None) -> Dict[str, Any]:
    conn = get_conn()
    if property_id is not None:
        rows = conn.execute("SELECT * FROM hunter_telemetry WHERE property_id = ? OR property_id IS NULL ORDER BY updated_at DESC", (property_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM hunter_telemetry ORDER BY updated_at DESC").fetchall()
    hunters = [dict(r) for r in rows]
    for h in hunters:
        tracks = conn.execute(
            "SELECT lat, lon, heading_deg, recorded_at FROM hunter_tracks WHERE hunter_id = ? ORDER BY id DESC LIMIT 40",
            (h["hunter_id"],),
        ).fetchall()
        h["track"] = [[t["lat"], t["lon"]] for t in reversed(tracks)]
    return {"status": "ok", "hunters": hunters}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "beacon"
    payload = json.loads(sys.stdin.read() or "{}")
    if mode == "issue_token":
        print(json.dumps(issue_beacon_token(payload["hunter_id"], payload.get("hunter_name", payload["hunter_id"])), indent=2))
    elif mode == "list":
        print(json.dumps(list_hunters(payload.get("property_id")), indent=2))
    else:
        lodge_authed = os.environ.get("BEACON_LODGE_AUTHED", "0") == "1"
        tok = os.environ.get("BEACON_TOKEN", "")
        res = ingest_beacon(payload, lodge_authed=lodge_authed, beacon_token=tok)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["status"] == "ok" else 1)
