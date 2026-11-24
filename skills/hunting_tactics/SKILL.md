---
name: hunting_tactics
description: Sovereign whitetail & big-game hunting GIS, wind/thermal scent profiling, onX GPX/KML interchange, and live mobile GPS hunter coordination.
---
# Hunting Buddy Tactical Workflow

1. **Ingest Camp GIS**: Use `hunt_import` with coordinates (`lat`/`lon` + `state="WI"` for automatic Wisconsin SCO parcel polygon lookup) or an uploaded onX `.gpx`/`.kml`/`.kmz`, GeoJSON, or county parcel PDF.
2. **Fetch Weather & Legal Shooting Hours**: Call `hunt_weather` to retrieve Open-Meteo hourly wind speed/direction, barometric trend, thermal regime (`upslope_warming` vs `katabatic_drainage`), and NOAA astronomical DNR legal shooting hours (sunrise -30m / sunset +20m).
3. **Evaluate Stands & Zero Downwind Conflicts**: Call `hunt_plan` with the forecast wind direction/mph and thermal mode. Check `conflicts` — never place one hunter inside another hunter's downwind scent cone.
4. **Render & Publish Tactical Card**: Call `hunt_render_map` with `publish_discord: true` to deliver an annotated aerial/topo PNG map (with parcel lines, stands, scent cones, compass rose, scale bar, and any live mobile GPS hunters).
5. **Export Back to onX Hunt**: Call `hunt_export` (`fmt: "gpx"`) so hunters can load recommended stand pins directly into onX Hunt on their phones before stepping into the woods.
6. **Live Field Telemetry**: Hunters can stream live GPS + compass heading from Android or mobile web to `POST /api/modules/hunting-buddy/mobile/beacon` using a scoped `X-Beacon-Token` issued via `hunt_mobile(action="issue_token")`.
