"""Architectural style registry for visual GLB generation."""
from __future__ import annotations

from copy import deepcopy


BASE = {
    "materials": {
        "wall": [190, 176, 154, 255],
        "accent": [80, 130, 170, 255],
        "frame": [235, 226, 205, 255],
        "glass": [90, 160, 210, 150],
        "roof": [120, 120, 120, 255],
        "column": [210, 200, 180, 255],
        "slab": [150, 150, 145, 255],
        "door": [110, 72, 45, 255],
        "decoration": [230, 210, 150, 255],
        "loadbearing": [120, 110, 100, 255],
        "stair": [140, 140, 150, 255],
    },
    "window_shape": "rect",
    "door_shape": "rect",
    "column_style": "rect",
    "entrance_type": "flat_portal",
    "roof_detail": "parapet",
    "facade_pattern": "regular_bays",
    "frame_depth_m": 0.08,
    "frame_width_m": 0.12,
    "cornice_height_m": 0.22,
    "tile_band_height_m": 0.0,
}


STYLES = {
    "modern": {
        "materials": {
            "wall": [180, 188, 190, 255],
            "accent": [55, 65, 75, 255],
            "frame": [35, 42, 48, 255],
            "glass": [95, 170, 220, 145],
            "roof": [95, 98, 102, 255],
            "column": [175, 178, 182, 255],
            "decoration": [80, 90, 100, 255],
        },
        "window_shape": "curtain_grid",
        "door_shape": "glass",
        "column_style": "square",
        "entrance_type": "glass_lobby",
        "roof_detail": "flat_mechanical",
        "facade_pattern": "curtain_wall",
        "frame_width_m": 0.07,
    },
    "persian": {
        "materials": {
            "wall": [178, 128, 82, 255],
            "accent": [31, 154, 175, 255],
            "frame": [238, 218, 173, 255],
            "glass": [65, 145, 185, 150],
            "roof": [153, 107, 63, 255],
            "column": [210, 184, 138, 255],
            "door": [92, 54, 32, 255],
            "decoration": [28, 178, 190, 255],
            "loadbearing": [120, 92, 64, 255],
            "stair": [150, 132, 110, 255],
        },
        "window_shape": "pointed_arch",
        "door_shape": "pointed_arch",
        "column_style": "fluted",
        "flute_count": 20,
        "capital_style": "bell",
        "entrance_type": "iwan",
        "roof_detail": "tile_parapet",
        "facade_pattern": "arched_bays",
        "tile_band_height_m": 0.35,
        "cornice_height_m": 0.3,
    },
    "classical": {
        "materials": {
            "wall": [205, 198, 184, 255],
            "accent": [170, 158, 138, 255],
            "frame": [235, 230, 218, 255],
            "glass": [120, 170, 205, 145],
            "roof": [100, 95, 90, 255],
            "column": [225, 220, 205, 255],
            "decoration": [180, 170, 150, 255],
        },
        "window_shape": "tall_rect",
        "door_shape": "pediment",
        "column_style": "classical",
        "entrance_type": "portico",
        "roof_detail": "pediment",
        "facade_pattern": "pilaster_bays",
        "frame_width_m": 0.16,
        "cornice_height_m": 0.38,
    },
    "islamic": {
        "materials": {
            "wall": [202, 183, 142, 255],
            "accent": [36, 135, 196, 255],
            "frame": [245, 225, 175, 255],
            "glass": [80, 160, 200, 140],
            "roof": [60, 148, 188, 255],
            "column": [220, 205, 168, 255],
            "decoration": [32, 164, 198, 255],
        },
        "window_shape": "horseshoe_arch",
        "door_shape": "horseshoe_arch",
        "column_style": "slender",
        "entrance_type": "muqarnas_portal",
        "roof_detail": "dome",
        "facade_pattern": "arcade",
        "tile_band_height_m": 0.45,
        "cornice_height_m": 0.28,
    },
}


ALIASES = {
    "persian_inspired_administrative": "persian",
    "persian_courtyard": "persian",
    "office": "modern",
    "administrative": "modern",
    "neoclassical": "classical",
}


def resolve_style(param: dict) -> dict:
    project_style = param.get("project", {}).get("style") or param.get("project", {}).get("architectural_style")
    preset = param.get("style", {}).get("preset") or project_style or "modern"
    return get_style(str(preset), param.get("style", {}), param.get("materials", {}))


def get_style(preset: str, overrides: dict | None = None, material_overrides: dict | None = None) -> dict:
    key = ALIASES.get(preset, preset)
    style = deepcopy(BASE)
    _deep_update(style, STYLES.get(key, STYLES["modern"]))
    style["preset"] = key
    if overrides:
        _deep_update(style, {k: v for k, v in overrides.items() if k != "preset"})
    if material_overrides:
        style.setdefault("materials", {}).update(material_overrides)
    return style


def _deep_update(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_update(dst[key], value)
        else:
            dst[key] = deepcopy(value)

