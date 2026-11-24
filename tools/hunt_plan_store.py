#!/usr/bin/env python3
"""Hunting Buddy Plan Archive & Removable Map Annotations CLI.

Powers both native `+hunting` agent tools (`hunt_plan_save`, `hunt_annotate`)
and Web UI actions (`list_plans`, `save_plan`, `delete_plan`, `list_annotations`,
`add_annotation`, `toggle_annotation`, `toggle_plan_annotations`,
`delete_annotation`, `clear_annotations`).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

import hunt_db


def _resolve_property_id(conn, pid: Optional[Any]) -> Optional[int]:
    if pid is not None and str(pid).strip() != "":
        try:
            return int(pid)
        except Exception:
            pass
    b = hunt_db.get_property_bundle(conn)
    return int(b["property"]["id"]) if b else None


def handle_plan_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    conn = hunt_db.get_conn()
    action = str(payload.get("action") or "save").strip().lower()

    if action == "list":
        pid = _resolve_property_id(conn, payload.get("property_id"))
        return {"status": "ok", "property_id": pid, "plans": hunt_db.list_plans(conn, pid)}

    if action == "get":
        plan_id = payload.get("plan_id") or payload.get("id")
        if not plan_id:
            pid = _resolve_property_id(conn, payload.get("property_id"))
            plans = hunt_db.list_plans(conn, pid, limit=1)
            if not plans:
                return {"status": "error", "message": "No saved plans found for this camp"}
            plan_id = plans[0]["id"]
        return hunt_db.get_plan(conn, int(plan_id))

    if action == "followup":
        plan_id = payload.get("plan_id") or payload.get("id")
        if not plan_id:
            return {"status": "error", "message": "plan_id is required for followup"}
        q_txt = str(payload.get("question") or "").strip()
        a_txt = str(payload.get("answer_markdown") or payload.get("answer") or payload.get("briefing_markdown") or "").strip()
        if not q_txt or not a_txt:
            return {"status": "error", "message": "question and answer_markdown are required for followup"}
        return hunt_db.add_plan_followup(
            conn,
            plan_id=int(plan_id),
            question=q_txt,
            answer_markdown=a_txt,
            property_id=int(payload["property_id"]) if payload.get("property_id") not in (None, "") else None,
            created_by=str(payload.get("created_by") or "hiram"),
        )

    if action == "delete":
        plan_id = payload.get("plan_id") or payload.get("id")
        if not plan_id:
            return {"status": "error", "message": "plan_id is required"}
        return hunt_db.delete_plan(conn, int(plan_id))

    if action == "update":
        plan_id = payload.get("plan_id") or payload.get("id")
        if not plan_id:
            return {"status": "error", "message": "plan_id is required for update"}
        return hunt_db.update_plan(
            conn,
            plan_id=int(plan_id),
            title=payload.get("title"),
            briefing_markdown=payload.get("briefing_markdown") or payload.get("markdown"),
            top_stands=payload.get("top_stands") if isinstance(payload.get("top_stands"), list) else None,
            blown_stands=payload.get("blown_stands") if isinstance(payload.get("blown_stands"), list) else None,
            map_png_path=payload.get("map_png_path") or payload.get("map_path"),
            plan_mode=payload.get("plan_mode"),
            source_task_id=payload.get("source_task_id") or payload.get("task_id"),
            only_if_richer=bool(payload.get("only_if_richer", False)),
        )

    if action == "save":
        pid = _resolve_property_id(conn, payload.get("property_id"))
        if pid is None:
            return {"status": "error", "message": "No hunting property found. Import or create a camp first."}
        return hunt_db.save_plan(
            conn,
            property_id=pid,
            title=str(payload.get("title") or payload.get("scenario_name") or "Tactical Hunting Plan"),
            briefing_markdown=str(payload.get("briefing_markdown") or payload.get("markdown") or payload.get("summary") or ""),
            scenario_name=str(payload.get("scenario_name") or ""),
            wind_deg=payload.get("wind_deg"),
            wind_mph=payload.get("wind_mph"),
            thermal_mode=str(payload.get("thermal_mode") or "neutral"),
            shooting_light=payload.get("shooting_light"),
            top_stands=payload.get("top_stands") if isinstance(payload.get("top_stands"), list) else [],
            blown_stands=payload.get("blown_stands") if isinstance(payload.get("blown_stands"), list) else [],
            map_png_path=str(payload.get("map_png_path") or payload.get("map_path") or ""),
            source_task_id=str(payload.get("source_task_id") or payload.get("task_id") or ""),
            created_by=str(payload.get("created_by") or "george"),
            annotations=payload.get("annotations") if isinstance(payload.get("annotations"), list) else [],
            plan_mode=payload.get("plan_mode"),
        )

    return {"status": "error", "message": f"Unknown plan action: {action}"}


def handle_annotate_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    conn = hunt_db.get_conn()
    action = str(payload.get("action") or "add").strip().lower()

    if action == "list":
        pid = _resolve_property_id(conn, payload.get("property_id"))
        plan_id = int(payload["plan_id"]) if payload.get("plan_id") not in (None, "") else None
        return {
            "status": "ok",
            "property_id": pid,
            "annotations": hunt_db.list_annotations(conn, property_id=pid, plan_id=plan_id),
        }

    if action in ("remove", "delete"):
        ann_id = payload.get("annotation_id") or payload.get("id")
        if not ann_id:
            return {"status": "error", "message": "annotation_id is required"}
        return hunt_db.delete_annotation(conn, int(ann_id))

    if action == "toggle":
        ann_id = payload.get("annotation_id") or payload.get("id")
        if not ann_id:
            return {"status": "error", "message": "annotation_id is required"}
        vis = payload.get("visible")
        return hunt_db.toggle_annotation(conn, int(ann_id), None if vis is None else bool(vis))

    if action == "toggle_plan":
        plan_id = payload.get("plan_id")
        if not plan_id:
            return {"status": "error", "message": "plan_id is required"}
        return hunt_db.toggle_plan_annotations(conn, int(plan_id), bool(payload.get("visible", True)))

    if action in ("clear", "clear_plan"):
        pid = _resolve_property_id(conn, payload.get("property_id"))
        if pid is None:
            return {"status": "error", "message": "No property loaded"}
        plan_id = int(payload["plan_id"]) if payload.get("plan_id") not in (None, "") else None
        return hunt_db.clear_annotations(conn, pid, plan_id=plan_id)

    if action == "add":
        pid = _resolve_property_id(conn, payload.get("property_id"))
        if pid is None:
            return {"status": "error", "message": "No hunting property found. Import or create a camp first."}
        plan_id = int(payload["plan_id"]) if payload.get("plan_id") not in (None, "") else None

        # Support batch array via payload["annotations"] or single annotation
        items = payload.get("annotations") if isinstance(payload.get("annotations"), list) else [payload]
        created = []
        errors = []
        for item in items:
            try:
                res = hunt_db.add_annotation(
                    conn,
                    property_id=pid,
                    plan_id=int(item["plan_id"]) if item.get("plan_id") not in (None, "") else plan_id,
                    label=str(item.get("label") or item.get("name") or "Tactical Annotation"),
                    kind=str(item.get("kind") or "callout"),
                    geom=item.get("geom") or item.get("geometry"),
                    geom_type=item.get("geom_type") or item.get("type"),
                    lat=item.get("lat"),
                    lon=item.get("lon"),
                    coordinates=item.get("coordinates"),
                    color=item.get("color"),
                    notes=str(item.get("notes") or item.get("rationale") or ""),
                    created_by=str(item.get("created_by") or payload.get("created_by") or "george"),
                )
                created.append(res)
            except Exception as exc:
                errors.append(str(exc))
        if not created and errors:
            return {"status": "error", "message": "; ".join(errors)}
        if len(created) == 1 and not errors:
            return created[0]
        return {"status": "ok", "created": created, "count": len(created), "errors": errors}

    return {"status": "error", "message": f"Unknown annotate action: {action}"}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    payload = hunt_db.parse_json_stdin()
    if mode == "annotate":
        out = handle_annotate_tool(payload)
    else:
        out = handle_plan_tool(payload)
    print(json.dumps(out, indent=2, default=str))
    if out.get("status") == "error":
        sys.exit(1)
