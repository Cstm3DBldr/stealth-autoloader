#!/bin/bash
# This runs automatically after every Moonraker Update Manager pull
echo "[POST-UPDATE] Syncing autoloader folder to config..."
rm -rf ~/printer_data/config/autoloader 2>/dev/null || true
cp -r ~/autoloader/autoloader ~/printer_data/config/
echo "[POST-UPDATE] Sync complete."
