# sa_filament_db.py — Filament brand database loader for Stealth Autoloader
#
# Scans a directory of .cfg files, each describing one brand's product lines
# and colors.  No code changes needed to add a new brand — drop a .cfg file
# in the brands folder and it appears in the load wizard automatically.
#
# File format (ini-style):
#   [sa_brand]
#   name: brandid
#   display_name: Brand Name
#
#   [sa_product_line lineid]
#   brand: brandid
#   display_name: Product Line
#   material: PLA
#   description: ...
#   load_temp: 210
#   unload_temp: 195
#   purge_speed: 5
#   purge_length: 30
#   bed_temp: 35
#   notes: optional
#
#   [sa_color lineid.colorid]
#   product_line: lineid
#   color_name: Color Name
#   color_hex: #RRGGBB

import os
import configparser
import logging

logger = logging.getLogger('sa_filament_db')


def scan_brands(brands_dir):
    """Return list of (display_name, filepath) tuples for all .cfg files found.

    Results are sorted alphabetically by display_name.
    """
    results = []
    try:
        for entry in sorted(os.scandir(brands_dir), key=lambda e: e.name.lower()):
            if entry.is_file() and entry.name.lower().endswith('.cfg'):
                try:
                    display_name = _read_brand_display_name(entry.path)
                    results.append((display_name, entry.path))
                except Exception as e:
                    logger.warning("sa_filament_db: skipping %s: %s", entry.name, e)
    except FileNotFoundError:
        logger.error("sa_filament_db: brands directory not found: %s", brands_dir)
    return results


def _read_brand_display_name(filepath):
    """Fast read — only extract the [sa_brand] display_name."""
    cp = configparser.RawConfigParser()
    cp.read(filepath, encoding='utf-8')
    for section in cp.sections():
        if section.strip().lower() == 'sa_brand':
            return cp.get(section, 'display_name', fallback=os.path.basename(filepath))
    return os.path.basename(filepath)


def load_brand(filepath):
    """Parse a brand .cfg file.

    Returns a dict:
    {
        'brand_name':    str,
        'display_name':  str,
        'product_lines': {
            'lineid': {
                'display_name': str,
                'material':     str,
                'description':  str,
                'load_temp':    float,
                'unload_temp':  float,
                'purge_speed':  float,
                'purge_length': float,
                'bed_temp':     float,
                'notes':        str,
                'colors': [
                    {'id': str, 'name': str, 'hex': str},
                    ...
                ]
            },
            ...
        }
    }
    """
    cp = configparser.RawConfigParser()
    cp.read(filepath, encoding='utf-8')

    brand_name   = ''
    display_name = os.path.basename(filepath)
    product_lines = {}

    for section in cp.sections():
        s = section.strip()

        if s.lower() == 'sa_brand':
            brand_name   = cp.get(section, 'name',         fallback='')
            display_name = cp.get(section, 'display_name', fallback=brand_name)

        elif s.lower().startswith('sa_product_line '):
            line_id = s[len('sa_product_line '):].strip()
            product_lines[line_id] = {
                'display_name': cp.get(section, 'display_name', fallback=line_id),
                'material':     cp.get(section, 'material',     fallback=''),
                'description':  cp.get(section, 'description',  fallback=''),
                'load_temp':    _getfloat(cp, section, 'load_temp',    200.0),
                'unload_temp':  _getfloat(cp, section, 'unload_temp',  185.0),
                'purge_speed':  _getfloat(cp, section, 'purge_speed',    5.0),
                'purge_length': _getfloat(cp, section, 'purge_length',  30.0),
                'bed_temp':     _getfloat(cp, section, 'bed_temp',       0.0),
                'notes':        cp.get(section, 'notes', fallback=''),
                'colors': [],
            }

        elif s.lower().startswith('sa_color '):
            color_key = s[len('sa_color '):].strip()
            parts     = color_key.split('.', 1)
            line_id   = parts[0]
            color_id  = parts[1] if len(parts) > 1 else color_key
            color_name = cp.get(section, 'color_name', fallback=color_id)
            color_hex  = cp.get(section, 'color_hex',  fallback='#808080')
            if line_id in product_lines:
                product_lines[line_id]['colors'].append({
                    'id':   color_id,
                    'name': color_name,
                    'hex':  color_hex,
                })

    return {
        'brand_name':    brand_name,
        'display_name':  display_name,
        'product_lines': product_lines,
    }


def get_materials(brand_data):
    """Return sorted list of unique material type strings for a loaded brand."""
    seen = set()
    result = []
    for pl in brand_data['product_lines'].values():
        m = pl['material']
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return sorted(result)


def get_product_lines(brand_data, material=None):
    """Return list of (line_id, line_dict) filtered by material (all if None)."""
    items = brand_data['product_lines'].items()
    if material:
        items = [(k, v) for k, v in items if v['material'] == material]
    return list(items)


def get_colors(brand_data, line_id):
    """Return list of {'id', 'name', 'hex'} dicts for the given product line."""
    pl = brand_data['product_lines'].get(line_id)
    if pl is None:
        return []
    return pl['colors']


def _getfloat(cp, section, key, default):
    try:
        return float(cp.get(section, key))
    except Exception:
        return default
