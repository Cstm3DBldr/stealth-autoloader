#!/bin/bash
# verify.sh — post-deploy sanity check.
#
# Runs against the printer (over SSH) or locally if already on the printer.
# Reports: symlink state, service health, log errors, and any forbidden
# string patterns found in deployed files.
#
# Usage:
#   ./scripts/verify.sh                       # default: scan for stealth_autoloader leftovers
#   ./scripts/verify.sh PATTERN [PATTERN...]  # custom patterns (e.g. after another rename)
#   PRINTER=pi@1.2.3.4 ./scripts/verify.sh    # override default printer host
#   LOCAL_ONLY=1 ./scripts/verify.sh          # don't ssh, run locally
#
# Exits 0 on clean, 1 if any forbidden pattern hits.

set -u

PRINTER="${PRINTER:-pi@192.168.1.214}"

if [ $# -eq 0 ]; then
    PATTERNS=(stealth_autoloader stealth-autoloader StealthAutoloader 'Stealth Autoloader')
else
    PATTERNS=("$@")
fi
# Build alternation pattern (escape any regex metachars in user input later if needed)
PATTERN_RE=$(IFS='|'; echo "${PATTERNS[*]}")

if [ -z "${LOCAL_ONLY:-}" ] && [ "$(hostname 2>/dev/null)" != "sc350" ]; then
    REMOTE="ssh $PRINTER"
else
    REMOTE=""
fi

echo "Verify against: ${REMOTE:-(local)}"
echo "Forbidden pattern(s): ${PATTERNS[*]}"
echo

# ── parameters.cfg drift check ──────────────────────────────────────────────
# Rule: printer's parameters.cfg is authoritative for default values. Any
# divergence between printer and repo means either (a) the printer has been
# tuned and the repo needs to catch up, or (b) the repo has new params the
# printer hasn't seen yet. Reports the diff and exits non-zero if drift is
# present in EXISTING params (case a). New repo-only params are informational.
echo "── parameters.cfg drift (printer vs repo) ──"
PARAM_DRIFT=0
if [ -n "$REMOTE" ]; then
    REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
    REPO_PARAMS="$REPO_ROOT/autoloader/parameters.cfg"
    if [ -f "$REPO_PARAMS" ]; then
        PRINTER_PARAMS=$(mktemp)
        if scp -q "${PRINTER}:/home/pi/printer_data/config/autoloader/parameters.cfg" "$PRINTER_PARAMS" 2>/dev/null; then
            # Extract "key : value" lines (ignore comments + blanks), normalize whitespace
            awk -F: '!/^[[:space:]]*#/ && NF>=2 {gsub(/^[[:space:]]+|[[:space:]]+$/,"",$1); split($2,a,"#"); gsub(/^[[:space:]]+|[[:space:]]+$/,"",a[1]); print $1"="a[1]}' "$PRINTER_PARAMS" | sort > "${PRINTER_PARAMS}.kv"
            awk -F: '!/^[[:space:]]*#/ && NF>=2 {gsub(/^[[:space:]]+|[[:space:]]+$/,"",$1); split($2,a,"#"); gsub(/^[[:space:]]+|[[:space:]]+$/,"",a[1]); print $1"="a[1]}' "$REPO_PARAMS"    | sort > "${PRINTER_PARAMS}.repo.kv"
            DIFFS=$(diff "${PRINTER_PARAMS}.repo.kv" "${PRINTER_PARAMS}.kv" || true)
            if [ -z "$DIFFS" ]; then
                echo "  ✓ in sync"
            else
                # Lines starting < are repo-only or repo-different; > are printer-only or printer-different.
                # Build a map of param -> (repo_val, printer_val)
                python3 - <<PYEOF
import subprocess
def parse(p):
    d = {}
    with open(p) as f:
        for line in f:
            line=line.strip()
            if not line or '=' not in line: continue
            k,v = line.split('=',1)
            d[k.strip()] = v.strip()
    return d
repo    = parse("${PRINTER_PARAMS}.repo.kv")
printer = parse("${PRINTER_PARAMS}.kv")
val_diff = []   # param exists in both, values differ -> printer wins, repo must catch up
repo_only = [] # in repo, not on printer -> info
printer_only = [] # on printer, not in repo -> info (probably new param recently added on printer)
for k in sorted(set(repo) | set(printer)):
    rv, pv = repo.get(k), printer.get(k)
    if rv is None:
        printer_only.append((k,pv))
    elif pv is None:
        repo_only.append((k,rv))
    elif rv != pv:
        val_diff.append((k,rv,pv))
if val_diff:
    print("  ✗ printer has tuned values that differ from repo defaults:")
    for k,rv,pv in val_diff:
        print(f"      {k}: repo={rv}  printer={pv}")
    print("    -> per CLAUDE.md rule, pull printer values into the repo:")
    print(f"       scp ${PRINTER}:/home/pi/printer_data/config/autoloader/parameters.cfg autoloader/parameters.cfg")
    import sys; sys.exit(2)
if repo_only:
    print("  ℹ repo has params not yet on printer (new params, expected after deploy):")
    for k,v in repo_only: print(f"      {k}: repo={v}")
if printer_only:
    print("  ℹ printer has params not in repo (likely added on printer; consider porting):")
    for k,v in printer_only: print(f"      {k}: printer={v}")
PYEOF
                rc=$?
                if [ $rc -eq 2 ]; then PARAM_DRIFT=1; fi
            fi
            rm -f "$PRINTER_PARAMS" "${PRINTER_PARAMS}.kv" "${PRINTER_PARAMS}.repo.kv"
        else
            echo "  ⚠ could not fetch printer parameters.cfg (skipping)"
        fi
    else
        echo "  ⚠ repo parameters.cfg not found at $REPO_PARAMS (skipping)"
    fi
else
    echo "  (skipped — local mode)"
fi
echo

# ── Symlinks ─────────────────────────────────────────────────────────────────
echo "── Klipper extras symlinks ──"
$REMOTE 'for f in autoloader.py sa_motion.py sa_sequences.py sa_calibration.py sa_encoder.py; do
    p="$HOME/klipper/klippy/extras/$f"
    if [ -L "$p" ] && [ -e "$p" ]; then
        printf "  ✓ %-22s -> %s\n" "$f" "$(readlink "$p")"
    elif [ -L "$p" ]; then
        printf "  ✗ %-22s BROKEN (symlink target missing)\n" "$f"
    else
        printf "  ✗ %-22s MISSING\n" "$f"
    fi
done'

echo
echo "── Moonraker component symlink ──"
$REMOTE 'p="$HOME/moonraker/moonraker/components/sa_moonraker.py"
if [ -L "$p" ] && [ -e "$p" ]; then
    printf "  ✓ sa_moonraker.py        -> %s\n" "$(readlink "$p")"
elif [ -L "$p" ]; then
    printf "  ✗ sa_moonraker.py        BROKEN\n"
else
    printf "  ✗ sa_moonraker.py        MISSING\n"
fi'

# ── Service health ───────────────────────────────────────────────────────────
echo
echo "── Service health ──"
$REMOTE 'for s in klipper moonraker KlipperScreen; do
    state=$(systemctl is-active "$s" 2>/dev/null || echo "unknown")
    if [ "$state" = "active" ]; then
        printf "  ✓ %-15s %s\n" "$s" "$state"
    else
        printf "  ✗ %-15s %s\n" "$s" "$state"
    fi
done'

# ── Forbidden pattern scan ───────────────────────────────────────────────────
echo
echo "── Forbidden patterns in deployed files ──"
HITS=$($REMOTE "
hits=''
for d in \"\$HOME/KlipperScreen/panels\" \"\$HOME/KlipperScreen\" \"\$HOME/printer_data/config/autoloader\"; do
    [ -d \"\$d\" ] || continue
    f=\$(grep -rlE \"$PATTERN_RE\" \"\$d\" 2>/dev/null)
    [ -n \"\$f\" ] && hits=\"\$hits\$f\\n\"
done
for f in \"\$HOME/printer_data/config/printer.cfg\" \"\$HOME/printer_data/config/moonraker.conf\" \"\$HOME/printer_data/config/sa_klipperscreen.conf\"; do
    [ -f \"\$f\" ] || continue
    if grep -qE \"$PATTERN_RE\" \"\$f\" 2>/dev/null; then
        # ignore stealthchop (legitimate TMC config) when scanning for stealth_*
        if grep -E \"$PATTERN_RE\" \"\$f\" | grep -qv stealthchop; then
            hits=\"\$hits\$f\\n\"
        fi
    fi
done
if [ -d \"\$HOME/mainsail/assets\" ]; then
    f=\$(grep -lE \"$PATTERN_RE\" \"\$HOME/mainsail/index.html\" \"\$HOME\"/mainsail/assets/*.js 2>/dev/null | head -10)
    [ -n \"\$f\" ] && hits=\"\$hits\$f\\n\"
fi
printf \"%b\" \"\$hits\"
")

if [ -z "$HITS" ]; then
    echo "  ✓ clean"
    HITS_EXIT=0
else
    echo "$HITS" | grep -v '^$' | sed 's/^/  ✗ /'
    HITS_EXIT=1
fi

# ── Recent log errors ────────────────────────────────────────────────────────
echo
echo "── Recent klippy.log errors (last 200 lines) ──"
# Exclude: Stats lines (rx_error/tx_error counters), TMC stealthchop refs,
# and CAN error counters that show up in normal idle output.
$REMOTE 'tail -200 "$HOME/printer_data/logs/klippy.log" \
    | grep -v "^Stats " \
    | grep -iE "error|traceback|exception" \
    | grep -vi "stealthchop\|rx_error=0\|tx_error=0\|bytes_invalid=0" \
    | tail -5 | sed "s/^/  /" || echo "  (none)"'

echo
echo "── Recent moonraker.log errors (current startup only) ──"
$REMOTE 'awk "/Starting Moonraker on/{out=\"\"} {out=out\$0\"\\n\"} END{print out}" "$HOME/printer_data/logs/moonraker.log" | grep -iE "ERROR|Exception|warning|Failed" | tail -5 | sed "s/^/  /" || echo "  (none)"'

echo
# Combined exit: any forbidden pattern OR any parameters drift fails the check.
[ $HITS_EXIT -ne 0 ] || [ $PARAM_DRIFT -ne 0 ] && exit 1 || exit 0
