#!/bin/bash
KLIPPER_PATH="${HOME}/klipper"
INSTALL_PATH="${HOME}/stealth-autoloader"
CONFIG_DIR="${HOME}/printer_data/config"

set -e

if [ "${1:-}" = "--uninstall" ]; then
    echo "[UNINSTALL] Removing Stealth Autoloader..."
    rm -f "${KLIPPER_PATH}/klippy/extras/filament_feed.py"
    rm -rf "${CONFIG_DIR}/stealth-autoloader" 2>/dev/null || true
    rm -f "${HOME}/.moonraker/config/update_manager/stealth-autoloader.ini" 2>/dev/null || true
    echo "[UNINSTALL] Complete."
    sudo systemctl restart klipper
    exit 0
fi

if [ "$EUID" -eq 0 ]; then echo "[ERROR] Do not run as root!"; exit 1; fi

git config --global pull.rebase false
echo "[INSTALL] Pulling latest from GitHub..."
git -C "${INSTALL_PATH}" pull origin main

echo "[INSTALL] Copying stealth-autoloader folder to config..."
rm -rf "${CONFIG_DIR}/stealth-autoloader" 2>/dev/null || true
cp -r "${INSTALL_PATH}/stealth-autoloader" "${CONFIG_DIR}/"

echo "[INSTALL] Linking Python backend..."
ln -sfn "${INSTALL_PATH}/klipper/extras/filament_feed.py" "${KLIPPER_PATH}/klippy/extras/filament_feed.py"

mkdir -p "${HOME}/.moonraker/config/update_manager"
cat > "${HOME}/.moonraker/config/update_manager/stealth-autoloader.ini" << 'ENDOFINI'
[update_manager stealth-autoloader]
type: git_repo
channel: dev
path: ${INSTALL_PATH}
origin: https://github.com/Cstm3DBldr/stealth-autoloader.git
managed_services: klipper
primary_branch: main
post_update_script: ${INSTALL_PATH}/post_update.sh
ENDOFINI

echo "✅ Installed (Happy Hare style)!"
echo "   Add [include stealth-autoloader/stealth-autoloader.cfg] to printer.cfg"
sudo systemctl restart klipper
