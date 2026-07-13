"""Convert normalized parametric JSON to BIM JSON."""
from __future__ import annotations

import math

from ..step1_normalize.schema import ValidationError, polygon_area


def to_bim(param: dict) -> dict:
    _resolve_openings(param)
    bim = {
        "project": {
            "name": param["project"]["name"],
            "unit": "m",
            "style": param.get("style", {}).get("preset") or param["project"].get("style", "modern"),
        },
        "levels": sorted(param["levels"], key=lambda x: x["elevation_m"]),
        "elements": [],
        "stats": {},
    }
    for w in param["walls"]:
        _add(bim, w["id"], "Wall", w["level"], {
            "centerline": [w["start"], w["end"]],
            "start": w["start"],
            "end": w["end"],
            "thickness": w["thickness_m"],
            "height": w["height_m"],
            "category": w.get("category", "internal"),
            "load_bearing": bool(w.get("load_bearing", False)),
            "material": w.get("material"),
            "reinforcement": w.get("reinforcement"),   # optional per-element 配筋 override
            "texture": w.get("texture"),               # optional 外墙贴图文件(相对 param['texture_dir'])
            "texture_fit": w.get("texture_fit"),       # "stretch"=单张铺满整面 / 默认按尺寸平铺
        })
    for c in param["columns"]:
        geom = {"center": c["center"], "height": c["height_m"], "shape": c.get("shape", "rect"),
                "material": c.get("material"), "reinforcement": c.get("reinforcement")}
        if c.get("shape") == "circle":
            geom["radius"] = c["radius_m"]
            geom["profile"] = _circle(c["center"], c["radius_m"])
        else:
            geom["size"] = c["size"]
            geom["profile"] = _rect(c["center"], c["size"])
        _add(bim, c["id"], "Column", c["level"], geom)
    for s in param["slabs"]:
        _add(bim, s["id"], "Slab", s["level"], {"profile": s["polygon"], "height": s["thickness_m"],
                                                 "material": s.get("material"),
                                                 "reinforcement": s.get("reinforcement")})
    for d in param["doors"]:
        _add_opening(bim, d, "Door")
    for w in param["windows"]:
        _add_opening(bim, w, "Window")
    levels = {lv["name"]: lv for lv in bim["levels"]}
    for s in param["stairs"]:
        h = levels[s["to_level"]]["elevation_m"] - levels[s["from_level"]]["elevation_m"]
        _add(bim, s["id"], "Stair", s["from_level"], {
            "bbox": s["bbox"],
            "height": h if h > 0 else levels[s["from_level"]]["height_m"],
            "from_level": s["from_level"],
            "to_level": s["to_level"],
            "width_m": s.get("width_m"),
            "riser_count": s.get("riser_count"),
        })
    auto_roof_holes_by_level = _derive_pishtaq_roof_holes(param)
    for r in param["roofs"]:
        geom = {"profile": r["polygon"], "height": r["thickness_m"], "roof_type": r.get("type", "flat"),
                "material": r.get("material")}
        if "ridge_height_m" in r:
            geom["ridge_height_m"] = r["ridge_height_m"]
        explicit_holes = list(r.get("holes", []))
        auto_holes = auto_roof_holes_by_level.get(r["level"], [])
        if explicit_holes or auto_holes:
            geom["holes"] = explicit_holes + auto_holes
        if r.get("type") == "gable":
            geom.update({
                "ridge_start": r["ridge_start"],
                "ridge_end": r["ridge_end"],
                "eave_height_m": r["eave_height_m"],
            })
        if "eave_height_m" in r and r.get("type") != "gable":
            geom["eave_height_m"] = r["eave_height_m"]
        if "sides" in r:
            geom["sides"] = r["sides"]
        _add(bim, r["id"], "Roof", r["level"], geom)
    levels_by_name = {lv["name"]: lv for lv in bim["levels"]}
    default_level = bim["levels"][0]["name"] if bim["levels"] else "1F"
    for room in param.get("rooms", []):
        lv = levels_by_name[room["level"]]
        height = room.get("height_m") or lv["height_m"]
        _add(bim, room["id"], "Space", room["level"], {
            "profile": room["polygon"],
            "height": height,
            "name": room.get("name", room["id"]),
            "function": room.get("function", "general"),
            "zone": room.get("zone"),
            "public": room.get("public", False),
        })

    iwans_by_id = {iw["id"]: iw for iw in param.get("iwans", [])}
    for p in param.get("pishtaqs", []):
        host = iwans_by_id.get(p.get("host_iwan"))
        if host:
            center = host["center"]
            outer_w = float(p.get("width_m", host["width_m"] * 1.3))
            outer_h = float(p.get("height_m", host["height_m"] * 1.4))
            host_wall = host["host_wall"]
            level = p.get("level") or host.get("level") or default_level
        else:
            center = p.get("center", [0.0, 0.0])
            outer_w = float(p.get("width_m", 8.0))
            outer_h = float(p.get("height_m", 10.0))
            host_wall = p.get("host_wall")
            level = p.get("level") or default_level
        _add(bim, p["id"], "Pishtaq", level, {
            "center": center,
            "width": outer_w,
            "height": outer_h,
            "thickness": float(p.get("frame_thickness_m", 0.5)),
            "projection": float(p.get("projection_m", 0.18)),
            "host_wall": host_wall,
            "host_iwan": p.get("host_iwan"),
            "calligraphy_band": bool(p.get("calligraphy_band", True)),
        })

    for pool in param.get("pools", []):
        level = pool.get("level") or default_level
        _add(bim, pool["id"], "Pool", level, {
            "profile": pool["polygon"],
            "depth": float(pool.get("depth_m", 0.35)),
            "rim_height": float(pool.get("rim_height_m", 0.12)),
        })

    for canal in param.get("canals", []):
        level = canal.get("level") or default_level
        _add(bim, canal["id"], "Canal", level, {
            "start": canal["start"],
            "end": canal["end"],
            "width": float(canal.get("width_m", 1.0)),
            "depth": float(canal.get("depth_m", 0.20)),
        })

    for g in param.get("gardens", []):
        level = g.get("level") or default_level
        _add(bim, g["id"], "Garden", level, {
            "profile": g["polygon"],
            "paving_pattern": g.get("paving_pattern", "charbagh_4quad"),
        })

    for sc in param.get("screens", []):
        level = sc.get("level") or default_level
        _add(bim, sc["id"], "Screen", level, {
            "host_id": sc.get("host_id"),
            "host_wall": sc.get("host_wall"),
            "pattern": sc.get("pattern", "lattice"),
            "cell_size": float(sc.get("cell_size_m", 0.18)),
            "thickness": float(sc.get("thickness_m", 0.045)),
            "panel_width": float(sc.get("panel_width_m", 1.2)),
            "panel_height": float(sc.get("panel_height_m", 1.4)),
        })

    for mq in param.get("muqarnas", []):
        level = mq.get("level") or default_level
        _add(bim, mq["id"], "Muqarnas", level, {
            "host_iwan": mq.get("host_iwan"),
            "tiers": int(mq.get("tiers", 4)),
            "cells_base": int(mq.get("cells_base", 10)),
            "half": bool(mq.get("half", True)),
        })

    # 非建筑场景元素(树/车/地形):非结构,带显式 material(canopy=foliage/trunk=timber、
    # vehicle_body、sand/soil),体素化后按各自 blast_kPa 可投弹。
    for tree in param.get("trees", []):
        _add(bim, tree["id"], "Tree", tree["level"], {
            "center": tree["center"],
            "species": tree.get("species", "palm"),
            "height": float(tree.get("height_m", 8.0)),
            "trunk_radius": float(tree.get("trunk_radius_m", 0.3)),
            "canopy_radius": float(tree.get("canopy_radius_m", 2.5)),
            "material": "foliage",   # 名义材料(成员表用);树干网格在 collect_meshes 单独标 timber
        })
    for veh in param.get("vehicles", []):
        _add(bim, veh["id"], "Vehicle", veh["level"], {
            "center": veh["center"],
            "kind": veh.get("kind", "car"),
            "length": float(veh.get("length_m", 4.5)),
            "width": float(veh.get("width_m", 2.0)),
            "height": float(veh.get("height_m", 1.6)),
            "heading_deg": float(veh.get("heading_deg", 0.0)),
            "material": "vehicle_body",
        })
    for terr in param.get("terrain", []):
        surf = terr.get("surface", "sand")
        _add(bim, terr["id"], "Terrain", terr["level"], {
            "profile": terr["polygon"],
            "surface": surf,
            "height": float(terr.get("thickness_m", 0.5)),
            "berm_height": float(terr.get("berm_height_m", 0.0) or 0.0),
            # 优先用显式 material 字段(如 reinforced_concrete,使院子地面可与楼板统一);否则按 surface 推断
            "material": terr.get("material") or ("sand" if surf == "sand" else "soil"),
        })

    auto_struct = param.get("auto_structure", True)
    explicit_beams = list(param.get("beams", []))
    beams_to_add = explicit_beams if explicit_beams else (_derive_beams(bim) if auto_struct else [])
    for beam in beams_to_add:
        _add(bim, beam["id"], "Beam", beam["level"], {
            "start": beam["start"],
            "end": beam["end"],
            "width": float(beam.get("width_m", 0.4)),
            "height": float(beam.get("height_m", 0.6)),
            "load_bearing": True,
        })

    explicit_footings = list(param.get("footings", []))
    footings_to_add = explicit_footings if explicit_footings else (
        _derive_footings(bim) if auto_struct else [])
    for ft in footings_to_add:
        _add(bim, ft["id"], "Footing", ft.get("level", default_level), {
            "center": ft["center"],
            "size": list(ft.get("size_m", [1.5, 1.5])),
            "thickness": float(ft.get("thickness_m", 0.6)),
            "top_z": float(ft.get("top_z", 0.0)),
        })

    auto_mep = param.get("auto_mep", True)
    explicit_mep = list(param.get("mep", []))
    mep_items = explicit_mep if explicit_mep else (_derive_mep(bim) if auto_mep else [])
    for m in mep_items:
        _add(bim, m["id"], m["type"], m.get("level", default_level), m["geometry"])
    counts = {}
    for e in bim["elements"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    bim["stats"] = {"elements": counts, "components": sum(counts.values())}
    return bim


def _resolve_openings(param):
    walls = {w["id"]: w for w in param["walls"]}
    for item in param["doors"] + param["windows"]:
        wall = walls[item["host_id"]]
        projected, dist, t, angle, length = _project(item["center"], wall["start"], wall["end"])
        tol = max(wall["thickness_m"] * 0.75, 0.05)
        if dist > tol:
            raise ValidationError(f"{item['id']}.center: {dist:.3f}m from host wall {item['host_id']}")
        if item["width_m"] > length:
            raise ValidationError(f"{item['id']}.width_m: wider than host wall")
        half = item["width_m"] / (2 * length)
        if t - half < -1e-6 or t + half > 1 + 1e-6:
            raise ValidationError(f"{item['id']}.center: opening extends beyond host wall")
        item["center"] = [round(projected[0], 6), round(projected[1], 6)]
        item["angle_rad"] = angle
        item["host_param"] = round(t, 6)
    for item in param["slabs"] + param["roofs"]:
        if polygon_area(item["polygon"]) <= 0:
            raise ValidationError(f"{item['id']}.polygon: area must be > 0")


def _add(bim, eid, typ, level, geometry):
    bim["elements"].append({
        "id": eid,
        "type": typ,
        "level": level,
        "geometry": geometry,
        "source": {"kind": "parametric_json", "source_id": eid},
        "confidence": 1.0,
        "warnings": [],
    })


def _add_opening(bim, item, typ):
    _add(bim, item["id"], typ, item["level"], {
        "center": item["center"],
        "width": item["width_m"],
        "height": item["height_m"],
        "sill_height": item["sill_height_m"],
        "host_id": item["host_id"],
        "host_param": item.get("host_param"),
        "angle_rad": item.get("angle_rad", 0.0),
    })


def _project(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    length = math.sqrt(length2)
    if length <= 0:
        raise ValidationError("wall length must be > 0")
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2
    tc = max(0.0, min(1.0, t))
    q = [a[0] + dx * tc, a[1] + dy * tc]
    return q, math.dist(p, q), tc, math.atan2(dy, dx), length


def _rect(center, size):
    cx, cy = center
    sx, sy = size[0] / 2, size[1] / 2
    return [[cx - sx, cy - sy], [cx + sx, cy - sy], [cx + sx, cy + sy], [cx - sx, cy + sy]]


def _derive_pishtaq_roof_holes(param: dict, margin: float = 0.6) -> dict:
    """For each pishtaq, compute its plan footprint as a roof hole on its level."""
    iwans_by_id = {iw["id"]: iw for iw in param.get("iwans", [])}
    walls_by_id = {w["id"]: w for w in param.get("walls", [])}
    by_level: dict[str, list] = {}
    for p in param.get("pishtaqs", []):
        iwan = iwans_by_id.get(p.get("host_iwan"))
        if iwan is None:
            continue
        host_wall = walls_by_id.get(iwan.get("host_wall"))
        if host_wall is None:
            continue
        a, b = host_wall["start"], host_wall["end"]
        angle = math.atan2(b[1] - a[1], b[0] - a[0])
        local_nx, local_ny = -math.sin(angle), math.cos(angle)
        if host_wall.get("category") == "courtyard":
            ox, oy = local_nx, local_ny
        else:
            ox, oy = -local_nx, -local_ny
        dxn, dyn = math.cos(angle), math.sin(angle)
        outer_w = float(p.get("width_m", float(iwan.get("width_m", 5.0)) * 1.3))
        thickness = float(p.get("frame_thickness_m", 0.45))
        projection = float(p.get("projection_m", 0.18))
        front_offset = projection + thickness / 2.0
        cx = iwan["center"][0] + ox * front_offset
        cy = iwan["center"][1] + oy * front_offset
        half_axis = outer_w / 2.0 + margin
        half_perp = thickness / 2.0 + projection + margin
        local_corners = [(-half_axis, -half_perp), (half_axis, -half_perp),
                         (half_axis, half_perp), (-half_axis, half_perp)]
        corners = []
        for lx, ly in local_corners:
            wx = cx + dxn * lx + ox * ly
            wy = cy + dyn * lx + oy * ly
            corners.append([wx, wy])
        level = p.get("level") or iwan.get("level") or host_wall.get("level")
        if level:
            by_level.setdefault(level, []).append(corners)
    return by_level


def _derive_beams(bim, max_span=12.0, align_tol=0.6):
    levels_cols: dict[str, list[dict]] = {}
    for e in bim["elements"]:
        if e["type"] == "Column":
            levels_cols.setdefault(e["level"], []).append(e)
    beams = []
    seen = set()
    counter = 0
    for level, cols in levels_cols.items():
        for ca in cols:
            xa, ya = ca["geometry"]["center"]
            for axis, primary, secondary in (("x", 0, 1), ("y", 1, 0)):
                nearest = None
                for cb in cols:
                    if cb is ca:
                        continue
                    xb, yb = cb["geometry"]["center"]
                    if abs((yb if secondary == 1 else xb) - (ya if secondary == 1 else xa)) > align_tol:
                        continue
                    if (xb if primary == 0 else yb) <= (xa if primary == 0 else ya):
                        continue
                    dist = math.dist([xa, ya], [xb, yb])
                    if dist > max_span:
                        continue
                    if nearest is None or dist < nearest[0]:
                        nearest = (dist, cb)
                if not nearest:
                    continue
                _, cb = nearest
                key = tuple(sorted([ca["id"], cb["id"]]))
                if key in seen:
                    continue
                seen.add(key)
                counter += 1
                beams.append({
                    "id": f"auto_beam_{level}_{counter:03d}",
                    "level": level,
                    "start": list(ca["geometry"]["center"]),
                    "end": list(cb["geometry"]["center"]),
                    "width_m": 0.40,
                    "height_m": 0.60,
                })
    return beams


def _derive_mep(bim):
    mep = []
    counter = 0
    spaces = [e for e in bim["elements"] if e["type"] == "Space"]
    for sp in spaces:
        func = sp["geometry"].get("function", "general")
        if func in ("courtyard", "garden", "water", "ceremonial"):
            continue
        xs = [p[0] for p in sp["geometry"]["profile"]]
        ys = [p[1] for p in sp["geometry"]["profile"]]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        counter += 1
        mep.append({
            "id": f"auto_light_{counter:03d}",
            "type": "LightFixture",
            "level": sp["level"],
            "geometry": {"center": [cx, cy], "elevation_offset": -0.05},
        })
    duct_counter = 0
    for lv in bim["levels"]:
        slabs = [e for e in bim["elements"]
                 if e["type"] == "Slab" and e["level"] == lv["name"]]
        if not slabs:
            continue
        profile = slabs[0]["geometry"]["profile"]
        x_min, x_max = min(p[0] for p in profile), max(p[0] for p in profile)
        y_min, y_max = min(p[1] for p in profile), max(p[1] for p in profile)
        w, d = x_max - x_min, y_max - y_min
        cx, cy = (x_min + x_max) / 2, (y_min + y_max) / 2
        duct_counter += 1
        if w >= d:
            start, end = [x_min + 1.5, cy], [x_max - 1.5, cy]
            pipe_s, pipe_e = [x_min + 1.5, cy + 0.6], [x_max - 1.5, cy + 0.6]
        else:
            start, end = [cx, y_min + 1.5], [cx, y_max - 1.5]
            pipe_s, pipe_e = [cx + 0.6, y_min + 1.5], [cx + 0.6, y_max - 1.5]
        mep.append({
            "id": f"auto_duct_{lv['name']}_{duct_counter:03d}",
            "type": "DuctSegment",
            "level": lv["name"],
            "geometry": {"start": start, "end": end, "diameter": 0.40,
                         "elevation_offset": -0.40, "system": "HVAC"},
        })
        mep.append({
            "id": f"auto_pipe_cw_{lv['name']}_{duct_counter:03d}",
            "type": "PipeSegment",
            "level": lv["name"],
            "geometry": {"start": pipe_s, "end": pipe_e, "diameter": 0.06,
                         "elevation_offset": -0.55, "system": "ColdWater"},
        })
        mep.append({
            "id": f"auto_pipe_dr_{lv['name']}_{duct_counter:03d}",
            "type": "PipeSegment",
            "level": lv["name"],
            "geometry": {"start": pipe_s, "end": pipe_e, "diameter": 0.10,
                         "elevation_offset": -0.75, "system": "Drainage"},
        })
    return mep


def _derive_footings(bim):
    groups: dict[tuple, list[dict]] = {}
    for e in bim["elements"]:
        if e["type"] != "Column":
            continue
        x, y = e["geometry"]["center"]
        key = (round(x, 1), round(y, 1))
        groups.setdefault(key, []).append(e)
    levels = {lv["name"]: lv for lv in bim["levels"]}
    footings = []
    for idx, ((x, y), cols) in enumerate(sorted(groups.items())):
        lowest_z = min(levels[c["level"]]["elevation_m"] for c in cols)
        c0 = cols[0]
        if c0["geometry"].get("shape") == "circle":
            col_size = max(c0["geometry"].get("radius", 0.25) * 2, 0.3)
        else:
            sz = c0["geometry"].get("size", [0.4, 0.4])
            col_size = max(sz)
        footing_w = max(col_size * 3.0, 1.2)
        footings.append({
            "id": f"auto_footing_{idx:03d}",
            "level": cols[0]["level"],
            "center": [x, y],
            "size_m": [footing_w, footing_w],
            "thickness_m": 0.60,
            "top_z": lowest_z,
        })
    return footings


def _circle(center, radius, segments=24):
    cx, cy = center
    return [[round(cx + math.cos(i * math.tau / segments) * radius, 6),
             round(cy + math.sin(i * math.tau / segments) * radius, 6)] for i in range(segments)]
