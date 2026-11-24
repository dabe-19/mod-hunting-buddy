# Hunting Buddy (`mod-hunting-buddy`)

Sovereign, zero-API-key, offline-capable hunting GIS, wind & thermal scent-cone profiler, bidirectional onX Hunt GPX/KML interchange, and live mobile GPS/compass telemetry module for **Blue Lodge**.

## Features
- **Pluggable State GIS Parcel Adapters**: Ships with Wisconsin SCO/WROC Statewide Parcel ArcGIS adapter (`adapters/wi_sco.py`); add MI, MN, IA, OH, or PA by implementing `StateParcelProvider`.
- **Multi-Format Ingest & onX Hunt Export**: Import `.gpx`, `.kml`, `.kmz`, `.geojson`, county parcel PDFs (`pypdf`), and trail-cam JPEG EXIF timestamps; export stand recommendations and routes back to `.gpx`/`.kml` for **onX Hunt**.
- **Hyper-Local Wind, Thermals & DNR Legal Shooting Light**: Open-Meteo hourly forecast + NOAA astronomical sunrise/sunset calculations (sunrise -30m / sunset +20m).
- **Gaussian/Katabatic Scent Cones & Conflict Detector**: Uses `shapely` + `pyproj` to score every stand and warn whenever one hunter's scent plume blows into another hunter's stand.
- **Offline-First Interactive Leaflet.js Map**: Vendored Leaflet 1.9.4 + USGS NAIP Aerial / 3DEP Topo / Hybrid tiles cached locally on disk under `.george/module_data/hunting-buddy/tile_cache/`.
- **Camp Privacy Model**: Defaults to `public` for smooth multi-cousin setup; opt into per-Discord-channel isolation via `/hunt privacy private`.
- **High-DPI Discord Tactical Map Cards**: Renders annotated aerial/topo PNGs with parcel lines, stands, scent cones, live hunter GPS arrows, compass rose, and scale bar.

---

## Mobile / Android GPS & Compass Heading Bridge

Hunting Buddy exposes a dedicated authenticated REST bridge on Blue Lodge (`george-web`) so either the built-in browser **Mobile Field Beacon** (over Headscale/LAN) or a native **Android companion app** can stream real-time GPS coordinates and compass heading directly onto the camp map.

### 1. Issue a Scoped Hunter Beacon Token
From the Web UI (**4. Mobile GPS & Compass Bridge → Issue Android Token**) or CLI:
```bash
/hunt token hunter-alpha "Hunter Alpha"
```
This stores only the SHA-256 hash of the token in `hunt.db` and returns a bearer token for `X-Beacon-Token`.

### 2. Stream GPS & Heading from Android / Mobile Client
- **Endpoint**: `POST https://<your-lodge-host>/api/modules/hunting-buddy/mobile/beacon`
- **Authentication**: Either `X-Beacon-Token: <hunter_token>` OR `X-Lodge-Token: <lodge_token>`
- **Content-Type**: `application/json`
- **Request Payload**:
```json
{
  "hunter_id": "hunter-alpha",
  "hunter_name": "Hunter Alpha",
  "lat": 45.92015,
  "lon": -89.25082,
  "heading_deg": 315.0,
  "speed_mps": 1.1,
  "accuracy_m": 4.5,
  "battery_pct": 84,
  "status": "walking_in",
  "property_id": 1
}
```
- **Response (`200 OK`)**:
```json
{
  "status": "ok",
  "hunter_id": "hunter-alpha",
  "hunter_name": "Hunter Alpha",
  "lat": 45.92015,
  "lon": -89.25082,
  "heading_deg": 315.0,
  "updated_at": "2026-11-22T16:15:00Z"
}
```
- **Side Effects**: Updates live position + breadcrumb track in `hunt.db`, renders live heading arrows on the Leaflet map & high-DPI Discord tactical cards, and emits `hunting/hunter_position_update` on the Blue Lodge Event Bus.
