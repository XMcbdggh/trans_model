"""Validation and normalization for Parametric Building JSON."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .schema import COLLECTIONS, UNITS, arr, bbox, fail, num, obj, point, polygon, polygon_area, pos, text


def load_parametric(path: str | Path, unit_override: str | None = None) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(data, unit_override)
    return normalize(data, unit_override)


def validate(data: dict, unit_override: str | None = None) -> None:
    obj(data, "$")
    project = obj(data.get("project"), "$.project")
    text(project.get("name"), "$.project.name")
    unit = (unit_override or project.get("unit") or "m").lower()
    if unit not in UNITS:
        fail("$.project.unit", "expected m, cm, or mm")
    if "style" in data:
        obj(data["style"], "$.style")
    if "detail" in data:
        obj(data["detail"], "$.detail")
    if "materials" in data:
        obj(data["materials"], "$.materials")

    levels = arr(data.get("levels"), "$.levels")
    if not levels:
        fail("$.levels", "at least one level is required")
    level_names: set[str] = set()
    for i, lv in enumerate(levels):
        p = f"$.levels[{i}]"
        obj(lv, p)
        name = text(lv.get("name"), f"{p}.name")
        if name in level_names:
            fail(f"{p}.name", f"duplicate level {name!r}")
        level_names.add(name)
        num(lv.get("elevation_m"), f"{p}.elevation_m")
        pos(lv.get("height_m"), f"{p}.height_m")

    for key in COLLECTIONS:
        if key in data:
            arr(data[key], f"$.{key}")

    ids: dict[str, str] = {}
    wall_ids: set[str] = set()
    for i, wall in enumerate(data.get("walls", [])):
        p = f"$.walls[{i}]"
        _common(wall, p, level_names, ids)
        wall_ids.add(wall["id"])
        if point(wall.get("start"), f"{p}.start") == point(wall.get("end"), f"{p}.end"):
            fail(f"{p}.end", "wall length must be > 0")
        pos(wall.get("thickness_m"), f"{p}.thickness_m")
        if "height_m" in wall:
            pos(wall["height_m"], f"{p}.height_m")

    for i, col in enumerate(data.get("columns", [])):
        p = f"$.columns[{i}]"
        _common(col, p, level_names, ids)
        point(col.get("center"), f"{p}.center")
        shape = col.get("shape", "rect")
        if shape not in ("rect", "circle"):
            fail(f"{p}.shape", "expected rect or circle")
        if shape == "circle":
            pos(col.get("radius_m"), f"{p}.radius_m")
        else:
            size = col.get("size")
            if not isinstance(size, list) or len(size) != 2:
                fail(f"{p}.size", "expected [width, depth]")
            pos(size[0], f"{p}.size[0]")
            pos(size[1], f"{p}.size[1]")

    for i, slab in enumerate(data.get("slabs", [])):
        p = f"$.slabs[{i}]"
        _common(slab, p, level_names, ids)
        _profile(slab, p)
        pos(slab.get("thickness_m"), f"{p}.thickness_m")

    for collection, sill in (("doors", 0.0), ("windows", 0.9)):
        for i, item in enumerate(data.get(collection, [])):
            p = f"$.{collection}[{i}]"
            _common(item, p, level_names, ids)
            host = text(item.get("host_id"), f"{p}.host_id")
            if host not in wall_ids:
                fail(f"{p}.host_id", f"unknown wall id {host!r}")
            point(item.get("center"), f"{p}.center")
            pos(item.get("width_m"), f"{p}.width_m")
            pos(item.get("height_m"), f"{p}.height_m")
            num(item.get("sill_height_m", sill), f"{p}.sill_height_m")

    for i, stair in enumerate(data.get("stairs", [])):
        p = f"$.stairs[{i}]"
        _id(stair, p, ids)
        for key in ("from_level", "to_level"):
            name = text(stair.get(key), f"{p}.{key}")
            if name not in level_names:
                fail(f"{p}.{key}", f"unknown level {name!r}")
        bbox(stair.get("bbox"), f"{p}.bbox")
        if "width_m" in stair:
            pos(stair["width_m"], f"{p}.width_m")
        if "riser_count" in stair and int(pos(stair["riser_count"], f"{p}.riser_count")) != stair["riser_count"]:
            fail(f"{p}.riser_count", "expected integer")

    for i, roof in enumerate(data.get("roofs", [])):
        p = f"$.roofs[{i}]"
        _common(roof, p, level_names, ids)
        rtype = roof.get("type", "flat")
        if rtype not in ("flat", "gable", "hip", "pyramidal", "tent_dome", "onion_dome"):
            fail(f"{p}.type", "expected flat, gable, hip, pyramidal, tent_dome, or onion_dome")
        _profile(roof, p)
        if "thickness_m" in roof:
            pos(roof["thickness_m"], f"{p}.thickness_m")
        if "ridge_height_m" in roof:
            pos(roof["ridge_height_m"], f"{p}.ridge_height_m")
        if rtype == "gable":
            point(roof.get("ridge_start"), f"{p}.ridge_start")
            point(roof.get("ridge_end"), f"{p}.ridge_end")
            pos(roof.get("eave_height_m"), f"{p}.eave_height_m")
            pos(roof.get("ridge_height_m"), f"{p}.ridge_height_m")

    for i, item in enumerate(data.get("facades", [])):
        p = f"$.facades[{i}]"
        obj(item, p)
        text(item.get("id"), f"{p}.id")
        text(item.get("host_wall"), f"{p}.host_wall")
        if item["host_wall"] not in wall_ids:
            fail(f"{p}.host_wall", f"unknown wall id {item['host_wall']!r}")
        if "bay_count" in item:
            pos(item["bay_count"], f"{p}.bay_count")

    for collection in ("iwans", "domes", "vaults", "arcades", "decorations"):
        for i, item in enumerate(data.get(collection, [])):
            p = f"$.{collection}[{i}]"
            obj(item, p)
            text(item.get("id"), f"{p}.id")
            if "level" in item and item["level"] not in level_names:
                fail(f"{p}.level", f"unknown level {item['level']!r}")

    for i, room in enumerate(data.get("rooms", [])):
        p = f"$.rooms[{i}]"
        _common(room, p, level_names, ids)
        _profile(room, p)
        if "height_m" in room:
            pos(room["height_m"], f"{p}.height_m")

    # 非建筑场景元素:trees/vehicles 仿 columns(点位),terrain 仿 slabs(轮廓)
    for i, tree in enumerate(data.get("trees", [])):
        p = f"$.trees[{i}]"
        _common(tree, p, level_names, ids)
        point(tree.get("center"), f"{p}.center")
        for k in ("height_m", "trunk_radius_m", "canopy_radius_m"):
            if k in tree:
                pos(tree[k], f"{p}.{k}")
    for i, veh in enumerate(data.get("vehicles", [])):
        p = f"$.vehicles[{i}]"
        _common(veh, p, level_names, ids)
        point(veh.get("center"), f"{p}.center")
        for k in ("length_m", "width_m", "height_m"):
            if k in veh:
                pos(veh[k], f"{p}.{k}")
        if "heading_deg" in veh:
            num(veh["heading_deg"], f"{p}.heading_deg")
    for i, terr in enumerate(data.get("terrain", [])):
        p = f"$.terrain[{i}]"
        _common(terr, p, level_names, ids)
        _profile(terr, p)
        for k in ("thickness_m", "berm_height_m"):
            if k in terr:
                pos(terr[k], f"{p}.{k}")

    for collection in ("pishtaqs", "gardens", "pools", "canals", "screens", "muqarnas",
                       "beams", "footings", "mep"):
        for i, item in enumerate(data.get(collection, [])):
            p = f"$.{collection}[{i}]"
            obj(item, p)
            text(item.get("id"), f"{p}.id")
            if "level" in item and item["level"] not in level_names:
                fail(f"{p}.level", f"unknown level {item['level']!r}")


def normalize(data: dict, unit_override: str | None = None) -> dict:
    out = deepcopy(data)
    scale = UNITS[(unit_override or out["project"].get("unit") or "m").lower()]
    out["project"]["unit"] = "m"
    out["project"].setdefault("north_angle_deg", 0.0)
    out.setdefault("style", {})
    if "style" not in out["project"]:
        out["project"]["style"] = out["style"].get("preset", out["project"].get("architectural_style", "modern"))
    out.setdefault("materials", {})
    out.setdefault("detail", {})
    out["detail"].setdefault("level", "medium")
    out["detail"].setdefault("generate_window_frames", True)
    out["detail"].setdefault("generate_arches", True)
    out["detail"].setdefault("generate_iwans", True)
    out["detail"].setdefault("generate_domes", True)
    out["detail"].setdefault("generate_facade_bays", True)
    out["detail"].setdefault("generate_decorations", True)
    for lv in out["levels"]:
        lv["elevation_m"] = _n(lv["elevation_m"], scale)
        lv["height_m"] = _n(lv["height_m"], scale)
    heights = {lv["name"]: lv["height_m"] for lv in out["levels"]}
    for w in out.get("walls", []):
        w["start"] = _pt(w["start"], scale)
        w["end"] = _pt(w["end"], scale)
        w["thickness_m"] = _n(w["thickness_m"], scale)
        w["height_m"] = _n(w["height_m"], scale) if "height_m" in w else heights[w["level"]]
        w.setdefault("category", "internal")
    for c in out.get("columns", []):
        c["center"] = _pt(c["center"], scale)
        c["height_m"] = _n(c["height_m"], scale) if "height_m" in c else heights[c["level"]]
        c["shape"] = c.get("shape", "rect")
        if c["shape"] == "circle":
            c["radius_m"] = _n(c["radius_m"], scale)
        else:
            c["size"] = [_n(c["size"][0], scale), _n(c["size"][1], scale)]
    for s in out.get("slabs", []):
        _normalize_profile(s, scale)
        s["thickness_m"] = _n(s["thickness_m"], scale)
    for d in out.get("doors", []):
        _opening(d, scale, 0.0)
    for w in out.get("windows", []):
        _opening(w, scale, 0.9)
    for s in out.get("stairs", []):
        s["bbox"] = [_n(v, scale) for v in s["bbox"]]
        if "width_m" in s:
            s["width_m"] = _n(s["width_m"], scale)
        s.setdefault("riser_count", None)
    for r in out.get("roofs", []):
        r["type"] = r.get("type", "flat")
        _normalize_profile(r, scale)
        r["thickness_m"] = _n(r.get("thickness_m", 0.2), scale)
        if "ridge_height_m" in r:
            r["ridge_height_m"] = _n(r["ridge_height_m"], scale)
        if "holes" in r:
            r["holes"] = [[_pt(p, scale) for p in hole] for hole in r["holes"]]
        if r["type"] == "gable":
            r["ridge_start"] = _pt(r["ridge_start"], scale)
            r["ridge_end"] = _pt(r["ridge_end"], scale)
            r["eave_height_m"] = _n(r["eave_height_m"], scale)
    for f in out.get("facades", []):
        for key in ("bay_width_m", "arch_height_m", "tile_band_height_m", "base_height_m", "cornice_height_m"):
            if key in f:
                f[key] = _n(f[key], scale)
    for iwan in out.get("iwans", []):
        if "center" in iwan:
            iwan["center"] = _pt(iwan["center"], scale)
        for key in ("width_m", "depth_m", "height_m", "arch_height_m"):
            if key in iwan:
                iwan[key] = _n(iwan[key], scale)
    for dome in out.get("domes", []):
        if "center" in dome:
            dome["center"] = _pt(dome["center"], scale)
        for key in ("radius_m", "height_m", "base_height_m",
                    "pendentive_size_m", "pendentive_height_m",
                    "drum_height_m", "finial_height_m"):
            if key in dome:
                dome[key] = _n(dome[key], scale)
    for vault in out.get("vaults", []):
        if "center" in vault:
            vault["center"] = _pt(vault["center"], scale)
        for key in ("length_m", "radius_m", "depth_m", "base_height_m", "angle_deg"):
            if key in vault and key != "angle_deg":
                vault[key] = _n(vault[key], scale)
    for room in out.get("rooms", []):
        _normalize_profile(room, scale)
        if "height_m" in room:
            room["height_m"] = _n(room["height_m"], scale)
        room.setdefault("function", "general")
        room.setdefault("name", room["id"])
        room.setdefault("public", False)
    for p in out.get("pishtaqs", []):
        for k in ("width_m", "height_m", "frame_thickness_m", "projection_m",
                  "opening_width_m", "opening_height_m"):
            if k in p:
                p[k] = _n(p[k], scale)
        if "center" in p:
            p["center"] = _pt(p["center"], scale)
    for pool in out.get("pools", []):
        _normalize_profile(pool, scale)
        for k in ("depth_m", "rim_height_m"):
            if k in pool:
                pool[k] = _n(pool[k], scale)
    for canal in out.get("canals", []):
        if "start" in canal:
            canal["start"] = _pt(canal["start"], scale)
        if "end" in canal:
            canal["end"] = _pt(canal["end"], scale)
        for k in ("width_m", "depth_m"):
            if k in canal:
                canal[k] = _n(canal[k], scale)
    for garden in out.get("gardens", []):
        _normalize_profile(garden, scale)
    for sc in out.get("screens", []):
        for k in ("panel_width_m", "panel_height_m", "thickness_m", "cell_size_m"):
            if k in sc:
                sc[k] = _n(sc[k], scale)
        if "center" in sc:
            sc["center"] = _pt(sc["center"], scale)
    for mq in out.get("muqarnas", []):
        for k in ("width_m", "height_m", "depth_m"):
            if k in mq:
                mq[k] = _n(mq[k], scale)
        if "center" in mq:
            mq["center"] = _pt(mq["center"], scale)
    for beam in out.get("beams", []):
        if "start" in beam:
            beam["start"] = _pt(beam["start"], scale)
        if "end" in beam:
            beam["end"] = _pt(beam["end"], scale)
        for k in ("width_m", "height_m"):
            if k in beam:
                beam[k] = _n(beam[k], scale)
    for ft in out.get("footings", []):
        if "center" in ft:
            ft["center"] = _pt(ft["center"], scale)
        if "size_m" in ft:
            ft["size_m"] = [_n(ft["size_m"][0], scale), _n(ft["size_m"][1], scale)]
        if "thickness_m" in ft:
            ft["thickness_m"] = _n(ft["thickness_m"], scale)
        if "top_z" in ft:
            ft["top_z"] = _n(ft["top_z"], scale)
    for mep in out.get("mep", []):
        if "start" in mep:
            mep["start"] = _pt(mep["start"], scale)
        if "end" in mep:
            mep["end"] = _pt(mep["end"], scale)
        if "center" in mep:
            mep["center"] = _pt(mep["center"], scale)
        for k in ("width_m", "height_m", "diameter_m", "elevation_offset_m"):
            if k in mep:
                mep[k] = _n(mep[k], scale)
    for arcade in out.get("arcades", []):
        if "start" in arcade:
            arcade["start"] = _pt(arcade["start"], scale)
        if "end" in arcade:
            arcade["end"] = _pt(arcade["end"], scale)
        for key in ("bay_width_m", "depth_m", "height_m"):
            if key in arcade:
                arcade[key] = _n(arcade[key], scale)
    # 非建筑场景元素:补默认值 + 单位归一(尺寸默认值已是米,scale 对 m 为 1.0)
    for tree in out.get("trees", []):
        tree["center"] = _pt(tree["center"], scale)
        tree.setdefault("species", "palm")
        tree["height_m"] = _n(tree.get("height_m", 8.0), scale)
        tree["trunk_radius_m"] = _n(tree.get("trunk_radius_m", 0.3), scale)
        tree["canopy_radius_m"] = _n(tree.get("canopy_radius_m", 2.5), scale)
    for veh in out.get("vehicles", []):
        veh["center"] = _pt(veh["center"], scale)
        veh.setdefault("kind", "car")
        veh.setdefault("heading_deg", 0.0)
        veh["length_m"] = _n(veh.get("length_m", 4.5), scale)
        veh["width_m"] = _n(veh.get("width_m", 2.0), scale)
        veh["height_m"] = _n(veh.get("height_m", 1.6), scale)
    for terr in out.get("terrain", []):
        _normalize_profile(terr, scale)
        terr.setdefault("surface", "sand")
        terr["thickness_m"] = _n(terr.get("thickness_m", 0.5), scale)
        if "berm_height_m" in terr:
            terr["berm_height_m"] = _n(terr["berm_height_m"], scale)
    for key in COLLECTIONS:
        out.setdefault(key, [])
    return out


def _common(item, path, level_names, ids):
    _id(item, path, ids)
    level = text(item.get("level"), f"{path}.level")
    if level not in level_names:
        fail(f"{path}.level", f"unknown level {level!r}")


def _id(item, path, ids):
    obj(item, path)
    eid = text(item.get("id"), f"{path}.id")
    if eid in ids:
        fail(f"{path}.id", f"duplicate id already used at {ids[eid]}")
    ids[eid] = path


def _profile(item, path):
    if ("polygon" in item) == ("bbox" in item):
        fail(path, "provide exactly one of polygon or bbox")
    poly = polygon(item["polygon"], f"{path}.polygon") if "polygon" in item else _bbox_poly(bbox(item["bbox"], f"{path}.bbox"))
    if polygon_area(poly) <= 0:
        fail(path, "profile area must be > 0")


def _n(value, scale):
    return round(float(value) * scale, 6)


def _pt(value, scale):
    return [_n(value[0], scale), _n(value[1], scale)]


def _bbox_poly(b):
    return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]


def _normalize_profile(item, scale):
    if "polygon" in item:
        item["polygon"] = [_pt(p, scale) for p in item["polygon"]]
    else:
        b = [_n(v, scale) for v in item.pop("bbox")]
        item["polygon"] = _bbox_poly(b)


def _opening(item, scale, default_sill):
    item["center"] = _pt(item["center"], scale)
    item["width_m"] = _n(item["width_m"], scale)
    item["height_m"] = _n(item["height_m"], scale)
    item["sill_height_m"] = _n(item.get("sill_height_m", default_sill), scale)
