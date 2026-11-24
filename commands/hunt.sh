#!/usr/bin/env bash
# commands/hunt.sh — /hunt slash command for Hunting Buddy
set -euo pipefail
MODULE_DIR="${MODULE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LODGE_DIR="${LODGE_DIR:-$(cd "$MODULE_DIR/../../.." && pwd)}"
MODULE_DATA_DIR="${MODULE_DATA_DIR:-$LODGE_DIR/.george/module_data/hunting-buddy}"
export MODULE_DIR LODGE_DIR MODULE_DATA_DIR

SUB="${1:-status}"
shift || true

case "$SUB" in
    status|list)
        bash "$MODULE_DIR/tools/dispatch.sh" hunt_log '{"action":"list_properties"}'
        ;;
    privacy)
        VIS="${1:-public}"
        PID="${2:-}"
        [ "$VIS" = "private" ] && VIS="channel_private"
        jq -n --arg v "$VIS" --arg p "$PID" '{action:"set_privacy",visibility:$v} + (if $p != "" then {property_id:($p|tonumber)} else {} end)' \
            | bash -c "args=\$(cat); bash '$MODULE_DIR/tools/dispatch.sh' hunt_log \"\$args\""
        ;;
    export)
        FMT="${1:-gpx}"
        bash "$MODULE_DIR/tools/dispatch.sh" hunt_export "{\"fmt\":\"$FMT\"}"
        ;;
    hunters)
        bash "$MODULE_DIR/tools/dispatch.sh" hunt_mobile '{"action":"list_hunters"}'
        ;;
    token)
        HID="${1:-hunter1}"
        HNAME="${2:-$HID}"
        bash "$MODULE_DIR/tools/dispatch.sh" hunt_mobile "{\"action\":\"issue_token\",\"hunter_id\":\"$HID\",\"hunter_name\":\"$HNAME\"}"
        ;;
    *)
        echo "Usage: /hunt {status|privacy <public|private> [property_id]|export [gpx|kml]|hunters|token <id> [name]}"
        ;;
esac
