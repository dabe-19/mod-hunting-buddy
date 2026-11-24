import json
import os
import sys
import tempfile
import unittest

MOD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(MOD_DIR, "tools"))
sys.path.insert(0, MOD_DIR)


class TestHuntingBuddy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MODULE_DATA_DIR"] = self.tmp.name
        import hunt_db, hunt_mobile, hunt_export, hunt_render_map
        hunt_db.DEFAULT_DATA_DIR = self.tmp.name
        hunt_mobile.DEFAULT_DATA_DIR = self.tmp.name
        hunt_mobile.TOKENS_PATH = os.path.join(self.tmp.name, "beacon_tokens.json")
        hunt_export.DEFAULT_DATA_DIR = self.tmp.name
        hunt_render_map.DEFAULT_DATA_DIR = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_gpx_kml_roundtrip_and_privacy(self):
        import hunt_db, hunt_import, hunt_export
        gpx = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="45.9225" lon="-89.2555"><name>North Ridge Stand</name><type>stand</type></wpt>
  <wpt lat="45.9212" lon="-89.2542"><name>Creek Bottom Blind</name><type>blind</type></wpt>
</gpx>"""
        gpx_path = os.path.join(self.tmp.name, "camp.gpx")
        with open(gpx_path, "w", encoding="utf-8") as f:
            f.write(gpx)

        res = hunt_import.import_property(
            name="Northwoods Test 40",
            file_path=gpx_path,
            fetch_state_parcels=False,
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["waypoints_imported"], 2)
        pid = res["property_id"]

        # Default visibility is public
        conn = hunt_db.get_conn()
        pub_list = hunt_db.list_properties(conn, channel_id="chan_other")
        self.assertEqual(len(pub_list), 1)

        # Opt-in channel_private hides property from other channels
        hunt_db.set_property_privacy(conn, pid, "channel_private", channel_id="chan_camp_a")
        self.assertEqual(len(hunt_db.list_properties(conn, channel_id="chan_other")), 0)
        self.assertEqual(len(hunt_db.list_properties(conn, channel_id="chan_camp_a")), 1)

        # Export back to GPX and KML for onX Hunt
        exp_gpx = hunt_export.run_export(property_id=pid, fmt="gpx", channel_id="chan_camp_a")
        self.assertEqual(exp_gpx["status"], "ok")
        self.assertIn("North Ridge Stand", exp_gpx["content"])
        self.assertTrue(os.path.isfile(exp_gpx["file_path"]))

        exp_kml = hunt_export.run_export(property_id=pid, fmt="kml", channel_id="chan_camp_a")
        self.assertEqual(exp_kml["status"], "ok")
        self.assertIn("Creek Bottom Blind", exp_kml["content"])

    def test_scent_conflict_and_map_render(self):
        import hunt_db, hunt_import, hunt_plan, hunt_render_map
        res = hunt_import.import_property(
            name="Wind Test Camp",
            lat=45.9220,
            lon=-89.2550,
            fetch_state_parcels=False,
        )
        pid = res["property_id"]
        conn = hunt_db.get_conn()
        conn.execute(
            "INSERT INTO waypoints (property_id, name, kind, lat, lon, assigned_hunter, ideal_winds, updated_at) VALUES (?, ?, 'stand', ?, ?, 'Hunter A', 'NW', ?)",
            (pid, "NW Ridge", 45.9228, -89.2560, hunt_db.now_iso()),
        )
        conn.execute(
            "INSERT INTO waypoints (property_id, name, kind, lat, lon, assigned_hunter, ideal_winds, updated_at) VALUES (?, ?, 'stand', ?, ?, 'Hunter B', 'W', ?)",
            (pid, "SE Creek", 45.9220, -89.2548, hunt_db.now_iso()),
        )
        conn.commit()

        # Wind FROM 315° (NW) blows scent directly SE into SE Creek stand
        plan = hunt_plan.evaluate_hunt_plan(pid, wind_deg=315.0, wind_mph=10.0, thermal_mode="neutral")
        self.assertEqual(plan["status"], "ok")
        self.assertGreaterEqual(len(plan["conflicts"]), 1)
        self.assertEqual(plan["conflicts"][0]["upwind_stand"], "NW Ridge")
        self.assertEqual(plan["conflicts"][0]["downwind_stand"], "SE Creek")

        # Render offline tactical PNG map (basemap="none" avoids network dependency in unit tests)
        rendered = hunt_render_map.render_tactical_map(pid, wind_deg=315.0, wind_mph=10.0, basemap="none")
        self.assertEqual(rendered["status"], "ok")
        self.assertTrue(os.path.isfile(rendered["png_path"]))
        self.assertGreater(os.path.getsize(rendered["png_path"]), 5000)

    def test_mobile_beacon_auth_and_tracks(self):
        import hunt_mobile
        issued = hunt_mobile.issue_beacon_token("hunter-alpha", "Hunter Alpha")
        self.assertEqual(issued["status"], "ok")
        tok = issued["beacon_token"]

        # Reject invalid token
        bad = hunt_mobile.ingest_beacon({"hunter_id": "hunter-alpha", "lat": 45.92, "lon": -89.25}, lodge_authed=False, beacon_token="wrong")
        self.assertEqual(bad["status"], "unauthorized")

        # Accept valid scoped X-Beacon-Token
        ok1 = hunt_mobile.ingest_beacon(
            {"hunter_id": "hunter-alpha", "hunter_name": "Hunter Alpha", "lat": 45.9220, "lon": -89.2550, "heading_deg": 45.0, "battery_pct": 91},
            lodge_authed=False,
            beacon_token=tok,
        )
        self.assertEqual(ok1["status"], "ok")
        ok2 = hunt_mobile.ingest_beacon(
            {"hunter_id": "hunter-alpha", "hunter_name": "Hunter Alpha", "lat": 45.9225, "lon": -89.2545, "heading_deg": 50.0, "battery_pct": 90},
            lodge_authed=False,
            beacon_token=tok,
        )
        self.assertEqual(ok2["status"], "ok")

        listed = hunt_mobile.list_hunters()
        self.assertEqual(len(listed["hunters"]), 1)
        self.assertEqual(len(listed["hunters"][0]["track"]), 2)

    def test_wi_sco_parser(self):
        from adapters.wi_sco import WisconsinSCOProvider
        fake_geojson = {
            "features": [
                {
                    "properties": {"OWNERNME1": "STATE OF WISCONSIN", "GISACRES": 40.25, "CONAME": "VILAS", "PARCELID": "024-00123"},
                    "geometry": {"type": "Polygon", "coordinates": [[[-89.26, 45.92], [-89.25, 45.92], [-89.25, 45.93], [-89.26, 45.93], [-89.26, 45.92]]]},
                }
            ]
        }
        parsed = WisconsinSCOProvider.parse_geojson_features(fake_geojson)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].owner_name, "STATE OF WISCONSIN")
        self.assertAlmostEqual(parsed[0].acres, 40.25)

    def test_plans_and_removable_annotations(self):
        import hunt_db, hunt_render_map
        conn = hunt_db.get_conn()
        cur = conn.execute(
            "INSERT INTO properties (name, state, center_lat, center_lon, created_at) VALUES (?, 'WI', 45.923, -89.256, ?)",
            ("Plan Camp", hunt_db.now_iso()),
        )
        pid = cur.lastrowid
        conn.commit()

        saved = hunt_db.save_plan(
            conn,
            property_id=pid,
            title="NW Morning Sit",
            wind_deg=315.0,
            wind_mph=8.0,
            thermal_mode="neutral",
            briefing_markdown="## NW Morning Sit\n- Use **West Creek** approach.",
            annotations=[
                {"label": "Top Stand", "kind": "stand_pick", "geom_type": "Point", "lat": 45.9235, "lon": -89.2565},
                {"label": "Stealth Approach", "kind": "approach_route", "geom_type": "LineString", "coordinates": [[45.9220, -89.2580], [45.9235, -89.2565]]},
                {"label": "Avoid Bedding", "kind": "avoid_zone", "geom_type": "Polygon", "coordinates": [[45.9225, -89.2555], [45.9225, -89.2548], [45.9218, -89.2548], [45.9218, -89.2555]]},
            ],
        )
        self.assertEqual(saved["status"], "ok")
        self.assertEqual(saved["annotations_created"], 3)
        plan_id = saved["plan_id"]

        anns = hunt_db.list_annotations(conn, property_id=pid, plan_id=plan_id)
        self.assertEqual(len(anns), 3)
        # Render PNG with annotations active
        rendered = hunt_render_map.render_tactical_map(pid, wind_deg=315.0, wind_mph=8.0, basemap="none")
        self.assertEqual(rendered["status"], "ok")
        self.assertTrue(os.path.isfile(rendered["png_path"]))

        # Toggle & remove individual object, then cascade delete plan
        hunt_db.toggle_annotation(conn, anns[0]["id"], False)
        self.assertEqual(len(hunt_db.list_annotations(conn, property_id=pid, visible_only=True)), 2)
        hunt_db.delete_annotation(conn, anns[0]["id"])
        self.assertEqual(len(hunt_db.list_annotations(conn, property_id=pid)), 2)
        hunt_db.delete_plan(conn, plan_id)
        self.assertEqual(len(hunt_db.list_annotations(conn, property_id=pid)), 0)


if __name__ == "__main__":
    unittest.main()
