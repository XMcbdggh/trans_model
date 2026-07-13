"""Translate a declarative Building Spec (Layer 1) into a valid param.json (Layer 2).

This covers the common cases an image+text produces (a walled site with a few
rectangular multi-storey buildings, windows, entrances, domes, landscape and
vehicles). For irregular / bespoke layouts the LLM can instead write a small
``build_scene.py`` directly against :class:`SceneBuilder` -- see
``../examples/build_scene_example.py``. Both paths emit the same param.json.

The Building Spec schema is ../schema/building-spec.schema.json.
"""
from __future__ import annotations

from .builder import SceneBuilder

_ALL_FACADES = ("south", "east", "north", "west")


def spec_to_param(spec: dict) -> dict:
    """Build a param.json dict from a Building Spec dict."""
    meta = spec.get("meta", {})
    name = meta.get("name") or "generated_scene"
    style = meta.get("style") or "modern"
    b = SceneBuilder(name, style=style)

    # A shared ground level for all site props (perimeter, terrain, trees, ...).
    b.add_level("G", 0.0, 4.0)

    _emit_site(b, spec.get("site") or {})

    for i, bs in enumerate(spec.get("buildings") or []):
        _emit_building(b, bs, index=i)

    _emit_features(b, spec)
    return b.to_param()


# --------------------------------------------------------------------- site
def _emit_site(b: SceneBuilder, site: dict) -> None:
    w = site.get("width_m")
    d = site.get("depth_m")
    ground = site.get("ground") or {}
    if w and d:
        b.add_terrain("site_ground", "G", [0.0, 0.0, float(w), float(d)],
                      surface=ground.get("surface", "sand"),
                      material=ground.get("material"),
                      thickness_m=float(ground.get("thickness_m", 0.5)))
    pw = site.get("perimeter_wall")
    if pw and w and d:
        b.perimeter_wall([0.0, 0.0, float(w), float(d)], "G",
                         thickness=float(pw.get("thickness_m", 1.0)),
                         height_m=float(pw.get("height_m", 5.0)),
                         material=pw.get("material", "stone_masonry"),
                         corner_towers=bool(pw.get("corner_towers", True)),
                         tower_size=float(pw.get("tower_size_m", 6.0)))


# ----------------------------------------------------------------- building
def _emit_building(b: SceneBuilder, bs: dict, index: int) -> None:
    bid = bs.get("id") or f"bld{index}"
    bbox = bs["footprint"]                     # [x0, y0, x1, y1] (required)
    floors = max(1, int(bs.get("floors", 1)))
    fh = float(bs.get("floor_height_m", 3.5))
    material = bs.get("material", "reinforced_concrete")
    roof_type = (bs.get("roof") or {}).get("type", "flat")
    col_spacing = float(bs.get("columns_spacing_m", 0.0))

    # optional basement (levels below grade)
    basement = bs.get("basement")
    if basement:
        n_b = max(0, int(basement.get("floors", 0)))
        bh = float(basement.get("floor_height_m", fh))
        bbbox = basement.get("footprint", bbox)
        b_names = [f"{bid}_B{n_b - i}" for i in range(n_b)]        # deepest first
        # elevations: B{n} lowest ... B1 just below grade
        for i, nm in enumerate(b_names):
            b.add_level(nm, -bh * (n_b - i), bh)
        handle_b = b.box_building(f"{bid}_ug", bbbox, b_names, material="reinforced_concrete",
                                  ext_thickness=1.0, slab_thickness=0.6, roof_type=None,
                                  column_spacing_m=col_spacing)
        # a stair shaft connecting grade down to the deepest basement
        _add_basement_stairs(b, bid, bbbox, ["G"] + list(reversed(b_names)))

    # above-grade floors
    a_names = [f"{bid}_F{i + 1}" for i in range(floors)]
    b.stack_levels(a_names, 0.0, fh)
    handle = b.box_building(f"{bid}", bbox, a_names, material=material,
                            ext_thickness=float(bs.get("wall_thickness_m", 0.4)),
                            slab_thickness=0.4, roof_type=roof_type,
                            column_spacing_m=col_spacing)

    # windows
    win = bs.get("windows")
    if win:
        per = win.get("per_facade") or {}
        shape = win.get("shape", "rect")
        for facade in _ALL_FACADES:
            count = int(per.get(facade, 0))
            if count > 0:
                b.add_windows(handle, facade, count,
                              width_m=float(win.get("width_m", 1.4)),
                              height_m=float(win.get("height_m", 1.6)),
                              sill_m=float(win.get("sill_m", 0.9)), shape=shape)

    # entrance (ground floor)
    ent = bs.get("entrance")
    if ent:
        facade = ent.get("facade", "south")
        ground = a_names[0]
        dshape = ent.get("shape", "rect")
        b.add_door(handle, facade, ground, width_m=float(ent.get("width_m", 2.0)),
                   height_m=float(ent.get("height_m", 2.6)), shape=dshape,
                   door_id=f"{bid}_entry")
        if ent.get("type") == "iwan":
            x0, y0, x1, y1 = bbox
            cx = (x0 + x1) / 2.0
            cy = y0 if facade in ("south",) else (y1 if facade == "north" else (x1 if facade == "east" else x0))
            center = [cx, y0] if facade == "south" else [cx, y1]
            b.add_iwan(f"{bid}_iwan", ground, handle["facade_walls"][ground][facade], center,
                       width_m=float(ent.get("width_m", 4.0)), depth_m=2.5,
                       height_m=min(fh * floors, fh * 1.5), arch_height_m=fh * 0.6)

    # per-building dome sitting on the roof
    dome = bs.get("dome")
    if dome:
        x0, y0, x1, y1 = bbox
        b.add_dome(f"{bid}_dome", a_names[-1], [(x0 + x1) / 2.0, (y0 + y1) / 2.0],
                   float(dome.get("radius_m", min(x1 - x0, y1 - y0) / 3.0)),
                   float(dome.get("height_m", 4.0)), shape=dome.get("shape", "onion"),
                   base_height_m=fh * floors)


def _add_basement_stairs(b: SceneBuilder, bid, bbox, level_chain):
    x0, y0, x1, y1 = bbox
    sx0, sy0 = x0 + 2.0, y0 + 2.0
    shaft = [sx0, sy0, sx0 + 4.0, sy0 + 4.0]
    for i in range(len(level_chain) - 1):
        b.add_stair(f"{bid}_ugstair_{i}", level_chain[i], level_chain[i + 1], shaft,
                    width_m=2.0, riser_count=24)


# ----------------------------------------------------------------- features
def _emit_features(b: SceneBuilder, spec: dict) -> None:
    for i, dm in enumerate(spec.get("domes") or []):
        b.add_dome(dm.get("id", f"dome{i}"), dm.get("level", "G"),
                   dm["center"], float(dm["radius_m"]), float(dm.get("height_m", 4.0)),
                   shape=dm.get("shape", "hemisphere"),
                   base_height_m=float(dm.get("base_height_m", 0.0)))
    for i, pl in enumerate(spec.get("pools") or []):
        b.add_pool(pl.get("id", f"pool{i}"), pl.get("level", "G"),
                   _rect_or_poly(pl["footprint"]), depth_m=float(pl.get("depth_m", 0.4)))
    for i, gd in enumerate(spec.get("gardens") or []):
        b.add_garden(gd.get("id", f"garden{i}"), gd.get("level", "G"),
                     _rect_or_poly(gd["footprint"]))
    for i, veg in enumerate(spec.get("vegetation") or []):
        kind = veg.get("kind", "palm")
        for j, pos in enumerate(veg.get("positions") or []):
            b.add_tree(f"veg{i}_{j}", "G", pos, species=kind,
                       height_m=float(veg.get("height_m", 8.5 if kind == "palm" else 6.5)),
                       canopy_radius_m=float(veg.get("canopy_radius_m", 2.6)))
    for i, v in enumerate(spec.get("vehicles") or []):
        b.add_vehicle(v.get("id", f"veh{i}"), "G", v["center"], kind=v.get("kind", "car"),
                      heading_deg=float(v.get("heading_deg", 0.0)),
                      length_m=float(v.get("length_m", 4.5)),
                      width_m=float(v.get("width_m", 2.0)),
                      height_m=float(v.get("height_m", 1.6)))


def _rect_or_poly(fp):
    """Accept either a rectangle [x0,y0,x1,y1] or an explicit polygon [[x,y],...]."""
    if fp and isinstance(fp[0], (int, float)) and len(fp) == 4:
        x0, y0, x1, y1 = fp
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return fp
