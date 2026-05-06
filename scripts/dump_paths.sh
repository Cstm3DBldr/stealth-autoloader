#!/bin/bash
# Dumps the autoloader's per-path state and color-hex arrays from
# Moonraker. Used to diagnose LED animator and color-rendering issues
# without fighting nested-quote escaping over SSH.

curl -s 'http://localhost:7125/printer/objects/query?autoloader' \
    | python3 -c '
import sys, json
sa = json.load(sys.stdin)["result"]["status"]["autoloader"]
print("num_paths:        ", sa.get("num_paths"))
print("path_states:      ", sa.get("path_states"))
print("path_color_hexes: ", sa.get("path_color_hexes"))
print("path_materials:   ", sa.get("path_materials"))
print("cal_state:        ", repr(sa.get("cal_state")))
print("current_path:     ", sa.get("current_path"))
'
