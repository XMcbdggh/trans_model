"""Real construction materials + engineering structural classification.

Gives the voxel model an engineering-grade marking instead of an ad-hoc visual
role: each element carries a real material (density, compressive strength,
blast-overpressure threshold from the blast-protection literature, e.g. UFC
3-340-02 ranges) and a structural-system class (foundation, primary vertical,
primary horizontal, ...). Materials drive the Minecraft block, the display
colour, AND the blast resistance, so appearance and damage stay consistent.
"""
from __future__ import annotations

# id -> properties. blast_kPa = peak overpressure that destroys the element.
MATERIALS = {
    "reinforced_concrete": {"name": "钢筋混凝土", "block": "minecraft:light_gray_concrete",
                            "rgb": (150, 152, 150), "density": 2400, "fc_MPa": 30, "blast_kPa": 130},
    "stone_masonry":       {"name": "石砌体", "block": "minecraft:smooth_sandstone",
                            "rgb": (206, 194, 150), "density": 2600, "fc_MPa": 12, "blast_kPa": 55},  # fc=砌体抗压(含砂浆缝),远低于钢混,不应高于 C30
    "brick_masonry":       {"name": "砖砌体", "block": "minecraft:bricks",
                            "rgb": (150, 84, 66), "density": 1900, "fc_MPa": 12, "blast_kPa": 35},
    "steel":               {"name": "钢", "block": "minecraft:iron_block",
                            "rgb": (176, 180, 186), "density": 7850, "fc_MPa": 250, "blast_kPa": 240},
    "timber":              {"name": "木", "block": "minecraft:dark_oak_planks",
                            "rgb": (106, 74, 44), "density": 600, "fc_MPa": 20, "blast_kPa": 20},
    "glass":               {"name": "玻璃", "block": "minecraft:light_gray_stained_glass",
                            "rgb": (205, 203, 196), "density": 2500, "fc_MPa": 6, "blast_kPa": 7},  # fc 取低:玻璃作易损构件,与 blast_kPa=7"一炸即碎"一致
    "tile":                {"name": "饰面/瓷砖", "block": "minecraft:orange_terracotta",
                            "rgb": (162, 84, 38), "density": 2000, "fc_MPa": 15, "blast_kPa": 25},
    "concrete_light":      {"name": "轻质混凝土/砌块", "block": "minecraft:gray_concrete",
                            "rgb": (120, 122, 125), "density": 1200, "fc_MPa": 8, "blast_kPa": 28},
    "copper":              {"name": "金属管线", "block": "minecraft:cut_copper",
                            "rgb": (192, 107, 79), "density": 8900, "fc_MPa": 200, "blast_kPa": 30},
    # ── 非建筑场景元素(树/车/地形)的材料。fc_MPa≈1 → 不参与结构承载;blast_kPa 低 → 易毁。
    #    各用独立 minecraft 块(block_resistance() 按块去重),前端 litematic.ts 同步配色。
    "foliage":             {"name": "树冠/枝叶", "block": "minecraft:oak_leaves",
                            "rgb": (74, 110, 54),  "density": 200,  "fc_MPa": 1, "blast_kPa": 20},   # 5→20:加固树冠(原太易被炸毁)
    "sand":                {"name": "沙土/沙地", "block": "minecraft:sand",
                            "rgb": (214, 197, 145),"density": 1500, "fc_MPa": 1, "blast_kPa": 10},
    "soil":                {"name": "土壤/回填", "block": "minecraft:dirt",
                            "rgb": (120, 92, 62),  "density": 1600, "fc_MPa": 1, "blast_kPa": 12},
    "vehicle_body":        {"name": "车身钢板", "block": "minecraft:black_concrete",
                            "rgb": (40, 42, 46),   "density": 1200, "fc_MPa": 1, "blast_kPa": 45},
}
DEFAULT_MATERIAL = "reinforced_concrete"

# Cementitious materials that carry a concrete grade + reinforcement.
CONCRETE_MATERIALS = {"reinforced_concrete", "concrete_light"}

# Period-plausible reinforcement defaults per structural class (for concrete members only).
# 配筋率 reinforcement_ratio_percent = As/Ag*100. Units: mm for bar/spacing/cover, % for ratio.
# These are DEFAULTS — a per-element geometry["reinforcement"] override (any subset) wins.
_REINF_ZERO = {
    "rebar_main_dia_mm": None, "rebar_main_spacing_mm": None,
    "stirrup_dia_mm": None, "stirrup_spacing_mm": None,
    "concrete_cover_mm": None, "reinforcement_ratio_percent": 0.0,
}
_REINF_BY_CLASS = {
    "foundation":         {"rebar_main_dia_mm": 25, "rebar_main_spacing_mm": 150,
                           "stirrup_dia_mm": 12, "stirrup_spacing_mm": 200,
                           "concrete_cover_mm": 50, "reinforcement_ratio_percent": 0.6},
    "primary_vertical":   {"rebar_main_dia_mm": 25, "rebar_main_spacing_mm": 150,
                           "stirrup_dia_mm": 10, "stirrup_spacing_mm": 100,
                           "concrete_cover_mm": 35, "reinforcement_ratio_percent": 1.2},  # 柱纵筋常用 0.8-1.5%,2.0% 偏高
    "primary_horizontal": {"rebar_main_dia_mm": 22, "rebar_main_spacing_mm": 150,
                           "stirrup_dia_mm": 8, "stirrup_spacing_mm": 150,
                           "concrete_cover_mm": 30, "reinforcement_ratio_percent": 1.5},
    "floor":              {"rebar_main_dia_mm": 12, "rebar_main_spacing_mm": 150,
                           "stirrup_dia_mm": None, "stirrup_spacing_mm": None,
                           "concrete_cover_mm": 20, "reinforcement_ratio_percent": 0.8},
    "roof":               {"rebar_main_dia_mm": 12, "rebar_main_spacing_mm": 150,
                           "stirrup_dia_mm": None, "stirrup_spacing_mm": None,
                           "concrete_cover_mm": 20, "reinforcement_ratio_percent": 0.8},
}


def concrete_grade(material: str, fc_MPa) -> str | None:
    """混凝土强度等级 from fc (concrete only): fc 30 -> 'C30'. Non-concrete -> None."""
    if material in CONCRETE_MATERIALS:
        try:
            return f"C{int(round(float(fc_MPa)))}"
        except (TypeError, ValueError):
            return None
    return None


def reinforcement_for(cls: str, material: str, overrides: dict | None = None) -> dict:
    """Reinforcement spec for a (class, material): class-defaults for concrete, zeros
    otherwise; a per-element ``overrides`` dict (any subset, None values skipped) wins.
    Always returns the full field set so consumers can read uniformly."""
    if material in CONCRETE_MATERIALS:
        base = dict(_REINF_BY_CLASS.get(cls, _REINF_BY_CLASS["floor"]))
    else:
        base = dict(_REINF_ZERO)
    if overrides:
        for k, v in overrides.items():
            if v is not None and k in base:
                base[k] = v
    return base

# Engineering structural-system classes (ordered; index used in the sidecar).
STRUCT_CLASSES = [
    "foundation", "primary_vertical", "primary_horizontal", "floor", "roof",
    "envelope", "opening", "partition", "decoration", "service", "other",
]
CLASS_INFO = {
    "foundation":         ("基础", "#6b4f2a"),
    "primary_vertical":   ("主竖向承重(柱/承重墙)", "#d11507"),
    "primary_horizontal": ("主水平(梁/转换)", "#ff5db0"),
    "floor":              ("楼板", "#8a8f96"),
    "roof":               ("屋顶/大跨", "#9b5cff"),
    "envelope":           ("围护(幕墙/非承重外墙)", "#27c4d6"),
    "opening":            ("洞口/门窗(薄弱点)", "#ffe24a"),
    "partition":          ("隔墙(非承重)", "#c9b48a"),
    "decoration":         ("装饰/面层", "#39c06a"),
    "service":            ("管线/设备", "#5a6470"),
    "other":              ("其他", "#7a7f87"),
}
CLASS_INDEX = {c: i for i, c in enumerate(STRUCT_CLASSES)}

# Auto material by (structural class, style family). Period-plausible defaults.
_AUTO = {
    "modern":    {"primary_vertical": "reinforced_concrete", "primary_horizontal": "steel",
                  "floor": "reinforced_concrete", "roof": "reinforced_concrete",
                  "partition": "concrete_light", "envelope": "glass", "opening": "steel",
                  "foundation": "reinforced_concrete", "decoration": "concrete_light"},
    "persian":   {"primary_vertical": "stone_masonry", "primary_horizontal": "timber",
                  "floor": "brick_masonry", "roof": "brick_masonry",
                  "partition": "brick_masonry", "envelope": "stone_masonry", "opening": "timber",
                  "foundation": "stone_masonry", "decoration": "tile"},
    "classical": {"primary_vertical": "stone_masonry", "primary_horizontal": "stone_masonry",
                  "floor": "reinforced_concrete", "roof": "timber",
                  "partition": "brick_masonry", "envelope": "stone_masonry", "opening": "timber",
                  "foundation": "stone_masonry", "decoration": "stone_masonry"},
}
_AUTO["islamic"] = _AUTO["persian"]


def _family(preset: str) -> str:
    return preset if preset in _AUTO else "modern"


def element_class(elem: dict) -> str:
    """Structural-system class from element type (+ load_bearing for walls)."""
    typ = elem.get("type")
    g = elem.get("geometry", {})
    if typ == "Wall":
        return "primary_vertical" if g.get("load_bearing") else "partition"
    return {
        "Column": "primary_vertical", "Beam": "primary_horizontal", "Footing": "foundation",
        "Slab": "floor", "Roof": "roof", "Window": "opening", "Door": "opening",
        "Stair": "primary_horizontal",   # structural circulation element
        "Pishtaq": "decoration", "Space": "other",
        "DuctSegment": "service", "PipeSegment": "service", "LightFixture": "service",
        # 非建筑场景元素:均非结构(不入支撑图、criticality=0)
        "Tree": "decoration", "Vehicle": "other", "Terrain": "other",
    }.get(typ, "other")


def element_material(elem: dict, preset: str) -> str:
    """Explicit geometry.material wins; else auto by class + style family."""
    g = elem.get("geometry", {})
    mat = g.get("material")
    if mat in MATERIALS:
        return mat
    if elem.get("type") == "Window":
        return "glass"
    cls = element_class(elem)
    fam = _family(preset)
    return _AUTO[fam].get(cls, DEFAULT_MATERIAL)


def feature_kind(feature: str, preset: str) -> tuple[str, str]:
    """(class, material) for param-only features collected outside bim['elements']."""
    fam = _family(preset)
    table = {
        "iwan": ("envelope", _AUTO[fam].get("envelope", "stone_masonry")),
        "pishtaq": ("decoration", _AUTO[fam].get("decoration", "tile")),
        "dome": ("roof", _AUTO[fam].get("roof", "brick_masonry")),
        "vault": ("roof", _AUTO[fam].get("roof", "brick_masonry")),
        "arcade": ("primary_horizontal", _AUTO[fam].get("primary_horizontal", "stone_masonry")),
        "facade": ("envelope", _AUTO[fam].get("envelope", "stone_masonry")),
        "screen": ("decoration", "tile"),
        "muqarnas": ("decoration", "tile"),
        "decoration": ("decoration", _AUTO[fam].get("decoration", "tile")),
        "garden": ("other", "stone_masonry"), "pool": ("other", "stone_masonry"),
        "canal": ("other", "stone_masonry"),
    }
    return table.get(feature, ("other", DEFAULT_MATERIAL))


def block_resistance() -> dict:
    """minecraft block id -> blast overpressure threshold (kPa), from materials."""
    out = {}
    for m in MATERIALS.values():
        out.setdefault(m["block"], m["blast_kPa"])
    return out
