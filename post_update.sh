#!/bin/bash
# post_update.sh — runs automatically after every Moonraker Update Manager pull.
#
# This is the canonical "deploy non-symlinked files" step. It must be safe to
# run repeatedly (idempotent). Files NOT covered by the Klipper-extras and
# Moonraker symlinks live here:
#
#   ~/printer_data/config/autoloader/          .cfg + .html (user-editable)
#   ~/KlipperScreen/panels/sa_*.py             KlipperScreen panel modules
#   ~/KlipperScreen/sa_*.py                    KlipperScreen helpers
#   ~/printer_data/config/sa_klipperscreen.conf KlipperScreen menu registration
#
# Every component must be listed here. If you add a new on-printer destination,
# add it to this script AND to CLAUDE.md "Project Surface".

set -e
REPO="${HOME}/autoloader"
CONFIG="${HOME}/printer_data/config"
KS="${HOME}/KlipperScreen"

echo "[POST-UPDATE] Syncing user-editable cfg + html to ${CONFIG}/autoloader/..."
mkdir -p "${CONFIG}/autoloader"
cp -f "${REPO}"/autoloader/*.cfg  "${CONFIG}/autoloader/"
cp -f "${REPO}"/autoloader/*.html "${CONFIG}/autoloader/" 2>/dev/null || true

echo "[POST-UPDATE] Syncing KlipperScreen panels..."
if [ -d "${KS}/panels" ]; then
    cp -f "${REPO}"/KlipperScreen/panels/sa_*.py "${KS}/panels/" 2>/dev/null || true
    cp -f "${REPO}"/KlipperScreen/sa_*.py        "${KS}/"        2>/dev/null || true
    cp -f "${REPO}"/KlipperScreen/sa_klipperscreen.conf "${CONFIG}/" 2>/dev/null || true
else
    echo "[POST-UPDATE]   (KlipperScreen not installed — skipping panel sync)"
fi

echo "[POST-UPDATE] Sync complete."
