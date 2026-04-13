#!/bin/bash
KLIPPER_PATH="${HOME}/klipper"
INSTALL_PATH="${HOME}/stealth-autoloader"
CONFIG_DIR="${HOME}/printer_data/config"

set -eu
if [ "$1" = "--uninstall" ]; then
    echo "[UNINSTALL] Removing Stealth Autoloader..."
    rm -f "${KLIPPER_PATH}/klippy/extras/filament_feed.py"
    rm -rf "${CONFIG_DIR}/stealth-autoloader" 2>/dev/null || true
    rm -f "${HOME}/.moonraker/config/update_manager/stealth-autoloader.ini" 2>/dev/null || true
    echo "[UNINSTALL] Complete."
    sudo systemctl restart klipper
    exit 0
fi
if [ "$EUID" -eq 0 ]; then echo "[ERROR] Do not run as root!"; exit 1; fi

echo "[INSTALL] Copying stealth-autoloader folder to config..."
cp -r "${INSTALL_PATH}" "${CONFIG_DIR}/"

echo "[INSTALL] Linking Python backend..."
ln -sfn "${INSTALL_PATH}/klipper/extras/filament_feed.py" "${KLIPPER_PATH}/klippy/extras/filament_feed.py"

mkdir -p "${HOME}/.moonraker/config"
cat > "${HOME}/.moonraker/config/update_manager/stealth-autoloader.ini" <<EOF
[update_manager stealth-autoloader]
type: git_repo
channel: dev
path: ${INSTALL_PATH}
origin: https://github.com/Cstm3DBldr/stealth-autoloader.git
managed_services: klipper
primary_branch: main
