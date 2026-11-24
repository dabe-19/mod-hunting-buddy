#!/usr/bin/env bash
set -euo pipefail
LODGE_DIR="${LODGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
MODULE_DIR="${MODULE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODULE_DATA_DIR="${MODULE_DATA_DIR:-$LODGE_DIR/.george/module_data/hunting-buddy}"
PY="$LODGE_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
"$PY" -c '
import json, os, sys
sys.path.insert(0, "'"$MODULE_DIR"'/tools")
import matplotlib, shapely, pyproj, pypdf
import hunt_db
conn = hunt_db.get_conn()
props = hunt_db.list_properties(conn)
print(json.dumps({"status": "ok", "module": "hunting-buddy", "properties": len(props), "db": hunt_db.db_path()}))
'
