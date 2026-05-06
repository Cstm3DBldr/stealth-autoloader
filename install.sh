#!/bin/bash
# install.sh — first-time setup. After install, all subsequent updates run
# through Moonraker Update Manager → post_update.sh (auto-syncs non-symlinked
# files). Use scripts/verify.sh to confirm everything is in place.

set -e
KLIPPER_PATH="${HOME}/klipper"
MOONRAKER_PATH="${HOME}/moonraker"
INSTALL_PATH="${HOME}/autoloader"
CONFIG_DIR="${HOME}/printer_data/config"
KS_PATH="${HOME}/KlipperScreen"

# ── Uninstall ────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
    echo "[UNINSTALL] Removing Autoloader..."
    # Klipper extras symlinks
    for f in autoloader.py sa_motion.py sa_sequences.py sa_calibration.py sa_encoder.py sa_led_animator.py; do
        rm -f "${KLIPPER_PATH}/klippy/extras/${f}"
    done
    # Moonraker component symlink
    rm -f "${MOONRAKER_PATH}/moonraker/components/sa_moonraker.py"
    # KlipperScreen direct copies
    rm -f "${KS_PATH}/panels/"sa_*.py 2>/dev/null || true
    rm -f "${KS_PATH}/"sa_*.py 2>/dev/null || true
    rm -f "${CONFIG_DIR}/sa_klipperscreen.conf"
    # Live config dir
    rm -rf "${CONFIG_DIR}/autoloader" 2>/dev/null || true
    # Update manager registration
    rm -f "${HOME}/.moonraker/config/update_manager/autoloader.ini" 2>/dev/null || true
    echo "[UNINSTALL] Complete. Repo at ${INSTALL_PATH} kept (rm -rf manually if desired)."
    echo "[UNINSTALL] Remove [include autoloader/autoloader.cfg] from printer.cfg, then restart klipper."
    exit 0
fi

if [ "$EUID" -eq 0 ]; then echo "[ERROR] Do not run as root."; exit 1; fi

# ── Pull latest ──────────────────────────────────────────────────────────────
echo "[INSTALL] Pulling latest from GitHub..."
git -C "${INSTALL_PATH}" pull origin main

# ── Symlink Klipper extras ───────────────────────────────────────────────────
echo "[INSTALL] Symlinking Klipper extras..."
for f in autoloader.py sa_motion.py sa_sequences.py sa_calibration.py sa_encoder.py; do
    ln -sfn "${INSTALL_PATH}/klipper/extras/${f}" "${KLIPPER_PATH}/klippy/extras/${f}"
    echo "  ${KLIPPER_PATH}/klippy/extras/${f} -> ${INSTALL_PATH}/klipper/extras/${f}"
done

# ── Symlink Moonraker component ──────────────────────────────────────────────
echo "[INSTALL] Symlinking Moonraker component..."
ln -sfn "${INSTALL_PATH}/moonraker/sa_moonraker.py" "${MOONRAKER_PATH}/moonraker/components/sa_moonraker.py"

# ── Initial sync of non-symlinked files ──────────────────────────────────────
echo "[INSTALL] Running post_update.sh for initial file sync..."
"${INSTALL_PATH}/post_update.sh"

# ── Register with Moonraker Update Manager ───────────────────────────────────
echo "[INSTALL] Registering with Moonraker Update Manager..."
mkdir -p "${HOME}/.moonraker/config/update_manager"
cat > "${HOME}/.moonraker/config/update_manager/autoloader.ini" <<EOF
[update_manager autoloader]
type: git_repo
channel: dev
path: ${INSTALL_PATH}
origin: https://github.com/Cstm3DBldr/autoloader.git
managed_services: klipper
primary_branch: main
post_update_script: ${INSTALL_PATH}/post_update.sh
EOF

# ── Restart services ─────────────────────────────────────────────────────────
echo "[INSTALL] Restarting klipper, moonraker, KlipperScreen..."
sudo systemctl restart klipper
sudo systemctl restart moonraker
sudo systemctl restart KlipperScreen 2>/dev/null || true

echo
echo "✓ Install complete."
echo "  • Add [include autoloader/autoloader.cfg] to printer.cfg (if not already there)"
echo "  • Run ${INSTALL_PATH}/scripts/verify.sh to confirm everything is in sync"
