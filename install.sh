#!/bin/bash
KLIPPER_PATH="${HOME}/klipper"
INSTALL_PATH="${HOME}/stealth-autoloader"
CONFIG_DIR="${HOME}/printer_data/config"

set -e

# ====================== UNINSTALL ======================
if [ "${1:-}" = "--uninstall" ]; then
    echo "[UNINSTALL] Removing Stealth Autoloader..."
    rm -f "${KLIPPER_PATH}/klippy/extras/filament_feed.py"
    rm -rf "${CONFIG_DIR}/stealth-autoloader" 2>/dev/null || true
    rm -f "${HOME}/.moonraker/config/update_manager/stealth-autoloader.ini" 2>/dev/null || true
    echo "[UNINSTALL] Complete."
    sudo systemctl restart klipper
    exit 0
fi
# =======================================================

if [ "$EUID" -eq 0 ]; then echo "[ERROR] Do not run as root!"; exit 1; fi

# Suppress git pull warning forever
git config --global pull.rebase false

echo "[INSTALL] Pulling latest from GitHub..."
git -C "${INSTALL_PATH}" pull origin main

echo "[INSTALL] Removing old folder and copying fresh version..."
rm -rf "${CONFIG_DIR}/stealth-autoloader" 2>/dev/null || true
cp -r "${INSTALL_PATH}/stealth-autoloader" "${CONFIG_DIR}/"

echo "[INSTALL] Linking Python backend..."
ln -sfn "${INSTALL_PATH}/klipper/extras/filament_feed.py" "${KLIPPER_PATH}/klippy/extras/filament_feed.py"

# Create moonraker folder + config file (no heredoc issues)
mkdir -p "${HOME}/.moonraker/config"
cat > "${HOME}/.moonraker/config/update_manager/stealth-autoloader.ini" << EOF2
[update_manager stealth-autoloader]
type: git_repo
channel: dev
path: ${INSTALL_PATH}
origin: https://github.com/Cstm3DBldr/stealth-autoloader.git
managed_services: klipper
primary_branch: main
EOF2

echo "✅ Stealth Autoloader synced and installed!"
echo "   Next: Make sure [include stealth-autoloader/*.cfg] is in printer.cfg"
sudo systemctl restart klipper
