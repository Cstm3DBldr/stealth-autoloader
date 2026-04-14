#!/bin/bash
# This runs automatically after every Moonraker Update Manager pull
echo "[POST-UPDATE] Syncing stealth-autoloader folder to config..."
rm -rf ~/printer_data/config/stealth-autoloader 2>/dev/null || true
cp -r ~/stealth-autoloader/stealth-autoloader ~/printer_data/config/
echo "[POST-UPDATE] Sync complete."
