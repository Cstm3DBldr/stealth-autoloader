# sa_moonraker.py — Autoloader Moonraker component
#
# Registers REST endpoints and WebSocket events for the autoloader.
# Symlink to activate:
#   ln -sf ~/autoloader/moonraker/sa_moonraker.py \
#           ~/moonraker/moonraker/components/sa_moonraker.py
#
# Endpoints:
#   GET  /machine/autoloader/status
#   GET  /machine/autoloader/brands
#   GET  /machine/autoloader/filaments?brand=<path>&material=<type>
#   POST /machine/autoloader/set_material
#   POST /machine/autoloader/load
#   POST /machine/autoloader/unload
#   POST /machine/autoloader/home

from __future__ import annotations
import os
import sys
import logging


logger = logging.getLogger('moonraker.sa_moonraker')

# ── Filament DB ───────────────────────────────────────────────────────────────
_DB_SEARCH_PATHS = [
    os.path.expanduser("~/autoloader/klipperscreen"),
    os.path.expanduser("~/autoloader"),
    os.path.dirname(os.path.abspath(__file__)),
]
_BRANDS_DIR = os.path.expanduser("~/autoloader/filaments/brands")

_db = None
for _p in _DB_SEARCH_PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import sa_filament_db as _db
except ImportError:
    logger.warning("sa_moonraker: sa_filament_db not found — brand endpoints unavailable")


class AutoloaderComponent:
    def __init__(self, config):
        self.server  = config.get_server()
        self.klippy  = self.server.lookup_component('klippy_connection')

        # Register REST endpoints
        self.server.register_endpoint(
            "/machine/autoloader/status",
            ['GET'],
            self._handle_status)
        self.server.register_endpoint(
            "/machine/autoloader/brands",
            ['GET'],
            self._handle_brands)
        self.server.register_endpoint(
            "/machine/autoloader/filaments",
            ['GET'],
            self._handle_filaments)
        self.server.register_endpoint(
            "/machine/autoloader/set_material",
            ['POST'],
            self._handle_set_material)
        self.server.register_endpoint(
            "/machine/autoloader/load",
            ['POST'],
            self._handle_load)
        self.server.register_endpoint(
            "/machine/autoloader/unload",
            ['POST'],
            self._handle_unload)
        self.server.register_endpoint(
            "/machine/autoloader/home",
            ['POST'],
            self._handle_home)

        # Subscribe to status updates and re-emit as autoloader events
        self.server.register_event_handler(
            "server:klippy_ready", self._on_klippy_ready)

    async def _on_klippy_ready(self):
        try:
            kapi = self.server.lookup_component('klippy_apis')
            await kapi.subscribe_objects({'autoloader': None})
        except Exception as e:
            logger.warning("sa_moonraker: subscribe failed: %s", e)

    # ── Status ────────────────────────────────────────────────────────────────

    async def _handle_status(self, web_request):
        try:
            kapi = self.server.lookup_component('klippy_apis')
            result = await kapi.query_objects({'autoloader': None})
            return result.get('autoloader', {})
        except Exception as e:
            raise self.server.error("SA status query failed: %s" % e, 500)

    # ── Brand discovery ───────────────────────────────────────────────────────

    async def _handle_brands(self, web_request):
        if _db is None:
            raise self.server.error("sa_filament_db not available", 503)
        brands = _db.scan_brands(_BRANDS_DIR)
        return {
            'brands': [
                {'display_name': name, 'filepath': path}
                for name, path in brands
            ]
        }

    # ── Filament query ────────────────────────────────────────────────────────

    async def _handle_filaments(self, web_request):
        if _db is None:
            raise self.server.error("sa_filament_db not available", 503)
        brand_path = web_request.get_str('brand', '')
        material   = web_request.get_str('material', '')
        if not brand_path or not os.path.isfile(brand_path):
            raise self.server.error("brand filepath required and must exist", 400)
        brand_data = _db.load_brand(brand_path)
        lines = _db.get_product_lines(brand_data, material or None)
        result = []
        for line_id, pl in lines:
            result.append({
                'line_id':      line_id,
                'display_name': pl['display_name'],
                'material':     pl['material'],
                'description':  pl['description'],
                'load_temp':    pl['load_temp'],
                'unload_temp':  pl['unload_temp'],
                'purge_speed':  pl['purge_speed'],
                'purge_length': pl['purge_length'],
                'bed_temp':     pl['bed_temp'],
                'notes':        pl['notes'],
                'colors':       pl['colors'],
            })
        return {'brand': brand_data['display_name'], 'product_lines': result}

    # ── GCode helpers ─────────────────────────────────────────────────────────

    async def _run_gcode(self, gcode):
        try:
            kapi = self.server.lookup_component('klippy_apis')
            await kapi.run_gcode(gcode)
        except Exception as e:
            raise self.server.error("GCode error: %s" % e, 500)

    # ── Set material ─────────────────────────────────────────────────────────

    async def _handle_set_material(self, web_request):
        path         = web_request.get_int('tool', None)
        material     = web_request.get_str('material',     '')
        brand        = web_request.get_str('brand',        '')
        line_id      = web_request.get_str('line',         '')
        color_name   = web_request.get_str('color_name',   '')
        color_hex    = web_request.get_str('color_hex',    '')
        load_temp    = web_request.get_float('load_temp',  200.)
        unload_temp  = web_request.get_float('unload_temp', 185.)
        purge_speed  = web_request.get_float('purge_speed', 5.)
        purge_length = web_request.get_float('purge_length', 30.)
        if path is None:
            raise self.server.error("'tool' parameter required", 400)
        cmd = (
            'SA_SET_MATERIAL TOOL=%d MATERIAL=%s BRAND="%s" LINE=%s '
            'COLOR_NAME="%s" COLOR_HEX=%s '
            'LOAD_TEMP=%.0f UNLOAD_TEMP=%.0f '
            'PURGE_SPEED=%.1f PURGE_LENGTH=%.0f'
            % (path, material, brand, line_id,
               color_name, color_hex,
               load_temp, unload_temp, purge_speed, purge_length))
        await self._run_gcode(cmd)
        return {'result': 'ok'}

    # ── Load ─────────────────────────────────────────────────────────────────

    async def _handle_load(self, web_request):
        path = web_request.get_int('tool', None)
        if path is None:
            raise self.server.error("'tool' parameter required", 400)
        await self._run_gcode("SA_LOAD TOOL=%d" % path)
        return {'result': 'ok'}

    # ── Unload ───────────────────────────────────────────────────────────────

    async def _handle_unload(self, web_request):
        path = web_request.get_int('tool', None)
        if path is None:
            raise self.server.error("'tool' parameter required", 400)
        await self._run_gcode("SA_UNLOAD TOOL=%d" % path)
        return {'result': 'ok'}

    # ── Home ─────────────────────────────────────────────────────────────────

    async def _handle_home(self, web_request):
        await self._run_gcode("SA_HOME")
        return {'result': 'ok'}


def load_component(config):
    return AutoloaderComponent(config)
