#!/bin/bash
# Run a Klipper macro by name without fighting nested-quote escaping.
# Usage: bash ~/autoloader/scripts/run_macro.sh MACRO_NAME [ARG1=v1 ARG2=v2 ...]
#
# Examples:
#   bash ~/autoloader/scripts/run_macro.sh _SA_LED_TEST_CASELIGHT_MAX
#   bash ~/autoloader/scripts/run_macro.sh _SA_LED_TEST_CASELIGHT_BROWNS
#   bash ~/autoloader/scripts/run_macro.sh _SA_LED_TEST_CASELIGHT_GRAYSCALE
#   bash ~/autoloader/scripts/run_macro.sh _SA_LED_PARKED TOOL=2

if [ -z "$1" ]; then
    echo "Usage: $0 MACRO_NAME [ARG=val ...]"
    exit 1
fi

# Build the gcode line from all args (macro name + any params).
SCRIPT="$*"

# JSON-encode the script string via Python — handles spaces, equals
# signs, and any other special characters cleanly.
PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'script': sys.argv[1]}))" "$SCRIPT")

curl -s -X POST 'http://localhost:7125/printer/gcode/script' \
    -H 'Content-Type: application/json' \
    --data "$PAYLOAD"
echo
