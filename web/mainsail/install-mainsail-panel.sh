#!/usr/bin/env bash
# install-mainsail-panel.sh
# Builds a patched Mainsail with the StealthAutoloader dashboard panel.
#
# Run on a machine that has Node.js 18+ and npm installed:
#   bash install-mainsail-panel.sh [printer_ip]
#
# What it does:
#   1. Clones Mainsail source at the version currently installed on your printer
#   2. Copies StealthAutoloaderPanel.vue + stealthAutoloaderMixin.ts into src/
#   3. Patches 3 existing Mainsail files (5 lines total)
#   4. Builds the bundle (npm run build)
#   5. SCPs the built files to your printer's ~/mainsail/ directory
#
set -e

PRINTER_IP="${1:-192.168.1.214}"
PRINTER_USER="pi"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="/tmp/mainsail-build"

echo "=== Stealth Autoloader — Mainsail Panel Installer ==="
echo "Printer: ${PRINTER_USER}@${PRINTER_IP}"
echo ""

# ── Detect installed Mainsail version ────────────────────────────────────
echo "Detecting Mainsail version on printer..."
MAINSAIL_VERSION=$(ssh "${PRINTER_USER}@${PRINTER_IP}" \
    "cat ~/mainsail/.version 2>/dev/null || cat ~/mainsail/release_info.json 2>/dev/null | python3 -c \"import sys,json;d=json.load(sys.stdin);print(d.get('version',''))\" 2>/dev/null || echo 'v2.14.0'")
echo "Installed version: ${MAINSAIL_VERSION}"
echo ""

# ── Clone Mainsail source ─────────────────────────────────────────────────
if [ -d "${BUILD_DIR}" ]; then
    echo "Removing old build directory..."
    rm -rf "${BUILD_DIR}"
fi

echo "Cloning Mainsail ${MAINSAIL_VERSION} source..."
git clone --depth 1 --branch "${MAINSAIL_VERSION}" \
    https://github.com/mainsail-crew/mainsail.git "${BUILD_DIR}" 2>&1 || \
git clone --depth 1 \
    https://github.com/mainsail-crew/mainsail.git "${BUILD_DIR}"

echo ""

# ── Copy our panel files ──────────────────────────────────────────────────
echo "Installing StealthAutoloader panel files..."

cp "${SCRIPT_DIR}/src/StealthAutoloaderPanel.vue" \
   "${BUILD_DIR}/src/components/panels/StealthAutoloaderPanel.vue"

cp "${SCRIPT_DIR}/src/stealthAutoloaderMixin.ts" \
   "${BUILD_DIR}/src/components/mixins/stealthAutoloader.ts"

echo "  ✓ StealthAutoloaderPanel.vue"
echo "  ✓ stealthAutoloaderMixin.ts"

# ── Patch 1: variables.ts — add 'stealth-autoloader' to allDashboardPanels ──
VARS_FILE="${BUILD_DIR}/src/store/variables.ts"
echo ""
echo "Patching variables.ts..."

# Insert 'stealth-autoloader' before 'mmu' in the allDashboardPanels list
if grep -q "stealth-autoloader" "${VARS_FILE}"; then
    echo "  ✓ Already patched"
else
    # Find 'mmu' in the panels list and insert stealth-autoloader before it
    sed -i "s/'mmu'/'stealth-autoloader', 'mmu'/" "${VARS_FILE}"
    echo "  ✓ Added 'stealth-autoloader' to allDashboardPanels"
fi

# ── Patch 2: getters.ts — conditionally hide panel when module not loaded ──
GETTERS_FILE="${BUILD_DIR}/src/store/gui/getters.ts"
echo "Patching store/gui/getters.ts..."

if grep -q "stealth_autoloader" "${GETTERS_FILE}"; then
    echo "  ✓ Already patched"
else
    # Find the mmu filter block and add ours after it
    # The existing pattern looks like:
    #   if (!rootState.printer?.mmu) {
    #       allPanels = allPanels.filter((name) => name !== 'mmu')
    #   }
    python3 - "${GETTERS_FILE}" <<'PYEOF'
import sys, re

path = sys.argv[1]
with open(path) as f:
    content = f.read()

# Find the mmu filter block and insert our block after it
mmu_pattern = r"(if \(!rootState\.printer\?\.mmu\) \{[^}]+\})"
replacement = r"""\1

        if (!rootState.printer?.stealth_autoloader) {
            allPanels = allPanels.filter((name) => name !== 'stealth-autoloader')
        }"""

new_content = re.sub(mmu_pattern, replacement, content, count=1)

if new_content == content:
    print("  WARNING: Could not find mmu filter block — inserting at end of getAllPossiblePanels")
    # Fallback: insert before the final return
    new_content = content.replace(
        "        return allPanels",
        """        if (!rootState.printer?.stealth_autoloader) {
            allPanels = allPanels.filter((name) => name !== 'stealth-autoloader')
        }

        return allPanels""",
        1
    )

with open(path, 'w') as f:
    f.write(new_content)

print("  ✓ Added stealth_autoloader conditional filter")
PYEOF
fi

# ── Patch 3: Dashboard.vue — import + register the component ──────────────
DASHBOARD_FILE="${BUILD_DIR}/src/pages/Dashboard.vue"
echo "Patching pages/Dashboard.vue..."

if grep -q "StealthAutoloaderPanel" "${DASHBOARD_FILE}"; then
    echo "  ✓ Already patched"
else
    python3 - "${DASHBOARD_FILE}" <<'PYEOF'
import sys, re

path = sys.argv[1]
with open(path) as f:
    content = f.read()

# Add import after the last panel import
mmu_import = "import MmuPanel from '@/components/panels/MmuPanel.vue'"
sa_import  = "import StealthAutoloaderPanel from '@/components/panels/StealthAutoloaderPanel.vue'"

if mmu_import in content:
    content = content.replace(mmu_import, mmu_import + "\n" + sa_import)
else:
    # Fallback: add after AfcPanel import
    afc_import = "import AfcPanel from '@/components/panels/AfcPanel.vue'"
    content = content.replace(afc_import, afc_import + "\n" + sa_import)

# Register in @Component decorator — find AfcPanel or MmuPanel in components list
for marker in ['AfcPanel,', 'MmuPanel,', 'AfcPanel']:
    if marker in content:
        content = content.replace(marker, marker + "\n            StealthAutoloaderPanel,", 1)
        break

with open(path, 'w') as f:
    f.write(content)

print("  ✓ Imported and registered StealthAutoloaderPanel")
PYEOF
fi

# ── Add i18n strings (English) ────────────────────────────────────────────
echo "Adding i18n translations..."
LOCALES_FILE="${BUILD_DIR}/src/locales/en.json"
python3 - "${LOCALES_FILE}" <<'PYEOF'
import sys, json

path = sys.argv[1]
with open(path) as f:
    data = json.load(f)

panels = data.setdefault('Panels', {})
if 'StealthAutoloaderPanel' not in panels:
    panels['StealthAutoloaderPanel'] = {
        'Headline':   'Stealth Autoloader',
        'Home':       'Home selector',
        'Engage':     'Engage drive',
        'Disengage':  'Disengage drive',
    }

with open(path, 'w') as f:
    json.dump(data, f, indent=4)

print("  ✓ Added English i18n strings")
PYEOF

# ── Install npm dependencies & build ─────────────────────────────────────
echo ""
echo "Installing npm dependencies (this takes 1-3 minutes)..."
cd "${BUILD_DIR}"
npm install --silent 2>&1 | tail -3

echo ""
echo "Building Mainsail (this takes 2-5 minutes)..."
npm run build 2>&1 | tail -10

echo ""
echo "=== Build complete! ==="

# ── Deploy to printer ─────────────────────────────────────────────────────
echo "Deploying to ${PRINTER_USER}@${PRINTER_IP}:~/mainsail/ ..."
scp -r "${BUILD_DIR}/dist/"* "${PRINTER_USER}@${PRINTER_IP}:~/mainsail/"

echo ""
echo "=== Done! ==="
echo ""
echo "The Stealth Autoloader panel will now appear automatically on your"
echo "Mainsail dashboard whenever the [stealth_autoloader] Klipper module is loaded."
echo ""
echo "If the panel doesn't appear: open Mainsail → Interface Settings → Dashboard"
echo "and make sure 'Stealth Autoloader' is visible in your layout."
echo ""
echo "Cleaning up build directory..."
rm -rf "${BUILD_DIR}"
