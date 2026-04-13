#!/bin/bash

KLIPPER_PATH="${HOME}/klipper"
INSTALL_PATH="${HOME}/stealth-autoloader"

set -eu
export LC_ALL=C

# ====================== UNINSTALL ======================
if [ "$1" = "--uninstall" ]; then
    echo "[UNINSTALL] Removing Stealth Autoloader..."
    rm -f "${KLIPPER_PATH}/klippy/extras/filament_feed.py"
    rm -f "${HOME}/.moonraker/config/update_manager/stealth-autoloader.ini" 2>/dev/null || true
    echo "[UNINSTALL] Files removed. Restart Klipper to finish."
    sudo systemctl restart klipper
    exit 0
fi
# =======================================================

function preflight_checks {
    if [ "$EUID" -eq 0 ]; then
        echo "[PRE-CHECK] This script must not be run as root!"
        exit -1
    fi
    if [ "$(sudo systemctl list-units --full -all -t service --no-legend | grep -F 'klipper.service')" ]; then
        printf "[PRE-CHECK] Klipper service found! Continuing...\n\n"
    else
        echo "[ERROR] Klipper service not found, please install Klipper first!"
        exit -1
    fi
}

function check_download {
    if [ ! -d "${INSTALL_PATH}" ]; then
        echo "[DOWNLOAD] Cloning Stealth Autoloader repo..."
        git clone https://github.com/YOUR_GITHUB_USERNAME/stealth-autoloader.git "${INSTALL_PATH}"
        chmod +x "${INSTALL_PATH}/install.sh"
        printf "[DOWNLOAD] Complete!\n\n"
    else
        printf "[DOWNLOAD] Repo already exists locally.\n\n"
    fi
}

function link_extension {
    echo "[INSTALL] Linking filament_feed.py to Klipper..."
    ln -sfn "${INSTALL_PATH}/klipper/extras/filament_feed.py" "${KLIPPER_PATH}/klippy/extras/filament_feed.py"
}

function restart_klipper {
    echo "[POST-INSTALL] Restarting Klipper..."
    sudo systemctl restart klipper
}

printf "\n======================================\n"
echo "- Stealth Autoloader install script -"
printf "======================================\n\n"

preflight_checks
check_download
link_extension
restart_klipper

echo "   Install complete!"
echo "   Next: Add the Moonraker update section and [include] macros.cfg (see below)"
