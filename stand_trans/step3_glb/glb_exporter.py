"""Direct high-detail visual GLB exporter."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import trimesh

from ..shared import materials as M
from ..shared.styles import resolve_style
from . import primitives as prim
from . import ship as ship
from . import textures as tx


def _boolean_backend_available() -> bool:
    try:
        import manifold3d  # noqa: F401
        return True
    except ImportError:
        try:
            import blender  # noqa: F401
            return True
        except ImportError:
            return False


_BOOLEAN_OK = _boolean_backend_available()


def _apply_wall_texture(mesh, g, param, angle, length, height):
    """Project an external image texture onto a wall mesh when its geometry carries a
    `texture` (file name relative to param['texture_dir']). Walls without a texture keep
    their flat material color — fully backward compatible.

    axis 跟随墙主轴(南北向墙沿 X → xz,东西向墙沿 Y → yz),使立面照片正立;
    texture_fit=='stretch' 时单张铺满整面(立面照片),否则按 ~3m 一铺平铺(无缝纹理)。
    """
    tex_name = g.get("texture")
    if not tex_name:
        return
    tex_dir = param.get("texture_dir", "")
    path = str(Path(tex_dir) / tex_name) if tex_dir else tex_name
    stretch = g.get("texture_fit") == "stretch"
    img = tx.load_external_image(path, 1024 if stretch else 512)   # 立面照片 1024;平铺墙面 512 足够
    if img is None:
        return
    axis = "xz" if abs(math.cos(angle)) >= abs(math.sin(angle)) else "yz"
    if stretch:
        ru = rv = 1.0
    else:
        ru = max(1.0, length / 3.0)
        rv = max(1.0, height / 3.0)
    prim.apply_planar_texture(mesh, img, axis=axis, repeat_u=ru, repeat_v=rv)


def _apply_flat_roof_texture(mesh, param, x0, y0, x1, y1):
    """平顶顶面贴图(top-down xy 投影):param['flat_roof_texture'] 提供则平铺到平顶 box 顶面。
    仅 flat 屋顶调用;装饰性斜顶/尖顶/穹顶不调用,保持原材质色。与墙体共用同一 webp →
    load_external_image lru_cache 命中同一 PIL.Image,_merge_textured_meshes 合并为一张贴图。"""
    tex_name = param.get("flat_roof_texture") if param else None
    if not tex_name:
        return
    tex_dir = param.get("texture_dir", "")
    path = str(Path(tex_dir) / tex_name) if tex_dir else tex_name
    img = tx.load_external_image(path, 512)
    if img is None:
        return
    ru = max(1.0, abs(x1 - x0) / 3.0)
    rv = max(1.0, abs(y1 - y0) / 3.0)
    prim.apply_planar_texture(mesh, img, axis="xy", repeat_u=ru, repeat_v=rv)


def collect_meshes(param: dict, bim: dict) -> tuple[list[trimesh.Trimesh], list[tuple], dict]:
    """Build the per-element visual meshes in Z-up building space.

    Returns (meshes, kinds, csg_stats): meshes each carry material face colors;
    kinds[i] = (structural_class, material_id, element_id) parallel to meshes.
    Shared by the GLB exporter and the litematic voxel exporter so both routes
    use identical geometry. The caller is responsible for any axis rotation.
    """
    style = resolve_style(param)
    detail = param.get("detail", {})
    materials = style["materials"]
    levels = {lv["name"]: lv for lv in bim.get("levels", [])}
    walls = {e["id"]: e for e in bim["elements"] if e["type"] == "Wall"}
    openings_by_wall = _index_openings_by_wall(bim["elements"], walls, levels)
    iwans_by_wall = _index_iwans_by_wall(param.get("iwans", []), walls) if detail.get("generate_iwans", True) else {}
    meshes: list[trimesh.Trimesh] = []
    kinds: list[tuple] = []   # parallel to meshes: (structural_class, material_id, element_id)
    csg_stats = {"walls_with_openings": 0, "boolean_failures": 0, "iwans_recessed": 0}
    preset = style.get("preset", "modern")

    def _emit(ms, feature):   # tag a feature-helper's meshes with (class, material, element_id)
        cls, mat = M.feature_kind(feature, preset)
        kinds.extend([(cls, mat, f"feat:{feature}")] * len(ms))   # synthetic id; not a structural member
        meshes.extend(ms)

    for elem in bim["elements"]:
        level = levels.get(elem.get("level")) or next(iter(levels.values()))
        z = float(level["elevation_m"])
        g = elem["geometry"]
        typ = elem["type"]
        n0 = len(meshes)
        if typ == "Wall":
            a, b = g["centerline"]
            angle = math.atan2(b[1] - a[1], b[0] - a[0])
            length = math.dist(a, b)
            thickness = g.get("thickness", 0.24)
            height = g.get("height", level["height_m"])
            wall_color = materials.get("loadbearing", materials["wall"]) if g.get("load_bearing") else materials["wall"]
            wall_mesh = prim.oriented_box_from_start(a, length, thickness, height, z, angle, wall_color)
            wall_openings = openings_by_wall.get(elem["id"], [])
            wall_iwans = iwans_by_wall.get(elem["id"], [])
            cut_boxes = []
            cut_boxes.extend(_build_opening_cut_boxes(wall_openings, angle, thickness, z))
            for iw in wall_iwans:
                cut_boxes.append(_build_iwan_cut_box(iw, elem, z))
            final_wall = wall_mesh
            if cut_boxes and _BOOLEAN_OK:
                cut_mesh, ok = _subtract_boxes(wall_mesh, cut_boxes, wall_color)
                if ok:
                    final_wall = cut_mesh
                    if wall_openings:
                        csg_stats["walls_with_openings"] += 1
                    if wall_iwans:
                        csg_stats["iwans_recessed"] += len(wall_iwans)
                else:
                    csg_stats["boolean_failures"] += 1
            _apply_wall_texture(final_wall, g, param, angle, length, height)
            meshes.append(final_wall)
        elif typ == "Column":
            col_height = g.get("height", level["height_m"])
            col_style = style.get("column_style")
            if g.get("shape") == "circle" or col_style in ("slender", "classical", "fluted", "apadana"):
                radius = float(g.get("radius", min(g.get("size", [0.45, 0.45])) / 2 if g.get("size") else 0.22))
                if col_style in ("fluted", "apadana"):
                    flute_count = int(g.get("flute_count") or style.get("flute_count", 20))
                    capital = g.get("capital_style") or style.get("capital_style", "bell")
                    meshes.extend(prim.fluted_column(
                        [g["center"][0], g["center"][1], z], radius, col_height,
                        materials["column"], materials["accent"],
                        flute_count=flute_count, capital_style=capital,
                    ))
                elif col_style in ("slender", "classical"):
                    meshes.extend(prim.ring_column([g["center"][0], g["center"][1], z], radius, col_height, materials["column"], materials["accent"]))
                else:
                    meshes.append(prim.cylinder([g["center"][0], g["center"][1], z], radius, col_height, materials["column"]))
            else:
                size = g.get("size", [0.45, 0.45])
                meshes.append(prim.box([g["center"][0], g["center"][1], z + col_height / 2], [size[0], size[1], col_height], materials["column"]))
        elif typ == "Slab":
            x0, y0, x1, y1 = _bbox(g["profile"])
            meshes.append(prim.box([(x0 + x1) / 2, (y0 + y1) / 2, z + g.get("height", 0.15) / 2], [x1 - x0, y1 - y0, g.get("height", 0.15)], materials["slab"]))
        elif typ == "Door":
            meshes.extend(_door_mesh(elem, walls, levels, style, materials, detail))
        elif typ == "Window":
            meshes.extend(_window_mesh(elem, walls, levels, style, materials, detail))
        elif typ == "Stair":
            x0, y0, x1, y1 = g["bbox"]
            height = g.get("height", level["height_m"])
            steps = int(g.get("riser_count") or 12)
            meshes.extend(_stair_mesh(x0, y0, x1, y1, z, height, steps, materials.get("stair", materials["slab"])))
        elif typ in ("DuctSegment", "PipeSegment"):
            s, e = g["start"], g["end"]
            length = math.dist(s, e)
            if length > 0:
                angle = math.atan2(e[1] - s[1], e[0] - s[0])
                dia = float(g.get("diameter", 0.3))
                offset = float(g.get("elevation_offset", -0.4))
                mep_z = z + level["height_m"] + offset
                color = [180, 180, 200, 255] if typ == "DuctSegment" else [140, 100, 75, 255]
                cx, cy = (s[0] + e[0]) / 2.0, (s[1] + e[1]) / 2.0
                cyl = trimesh.creation.cylinder(radius=dia / 2.0, height=length, sections=12)
                R = trimesh.geometry.align_vectors([0, 0, 1],
                                                   [math.cos(angle), math.sin(angle), 0])
                cyl.apply_transform(R)
                cyl.apply_translation([cx, cy, mep_z])
                meshes.append(prim.color_mesh(cyl, color))
        elif typ == "LightFixture":
            c = g["center"]
            light_z = z + level["height_m"] - 0.05
            meshes.append(prim.box([c[0], c[1], light_z - 0.03],
                                   [0.36, 0.36, 0.06], [240, 230, 180, 255]))
        elif typ == "Beam":
            if not detail.get("show_beams", True):
                continue
            s, e = g["start"], g["end"]
            length = math.dist(s, e)
            if length > 0:
                angle = math.atan2(e[1] - s[1], e[0] - s[0])
                bw = float(g.get("width", 0.4))
                bh = float(g.get("height", 0.6))
                beam_top = z + level["height_m"]
                meshes.append(prim.oriented_box_from_start(
                    [s[0], s[1]], length, bw, bh, beam_top - bh, angle,
                    materials.get("column", [180, 175, 160, 255])))
        elif typ == "Footing":
            # Underground; hidden in the architectural visual GLB by default.
            # Still emitted to the IFC as a real structural element.
            if not detail.get("show_foundations", False):
                continue
            c = g["center"]
            sz = g.get("size", [1.5, 1.5])
            ft_t = float(g.get("thickness", 0.6))
            top = float(g.get("top_z", z))
            meshes.append(prim.box([c[0], c[1], top - ft_t / 2.0],
                                   [sz[0], sz[1], ft_t],
                                   [110, 110, 110, 255]))
        elif typ == "Roof":
            meshes.extend(_roof_mesh(elem, levels, style, materials, param))
        elif typ == "Tree":
            c = g["center"]
            trunk_color = tuple(M.MATERIALS["timber"]["rgb"]) + (255,)
            fol_color = tuple(M.MATERIALS["foliage"]["rgb"]) + (255,)
            fn = prim.cypress_tree if g.get("species") == "cypress" else prim.palm_tree
            trunk_ms, fol_ms = fn([c[0], c[1], z], float(g.get("height", 8.0)),
                                  float(g.get("trunk_radius", 0.3)), float(g.get("canopy_radius", 2.5)),
                                  trunk_color, fol_color)
            for m in trunk_ms:
                meshes.append(m)
                kinds.append(("decoration", "timber", elem["id"]))     # 树干 = 木
            for m in fol_ms:
                meshes.append(m)
                kinds.append(("decoration", "foliage", elem["id"]))    # 叶冠 = foliage
            continue   # 已逐网格标材料,跳过下方统一标记
        elif typ == "Vehicle":
            c = g["center"]
            color = tuple(M.MATERIALS["vehicle_body"]["rgb"]) + (255,)
            meshes.extend(prim.vehicle([c[0], c[1], z], float(g.get("length", 4.5)),
                                       float(g.get("width", 2.0)), float(g.get("height", 1.6)),
                                       color, math.radians(float(g.get("heading_deg", 0.0))),
                                       kind=g.get("kind", "car")))
        elif typ == "Terrain":
            x0, y0, x1, y1 = _bbox(g["profile"])
            th = float(g.get("height", 0.5))
            # palace 院子地坪(reinforced_concrete)与楼内 slab 完全一致:GLB 同色(取 slab style 色)+
            # 顶面对齐(z+th/2 向上堆,顶面=slab 顶,消除 0.6m 错台);litematic 侧 block 已同为 light_gray_concrete。
            if g.get("material") == "reinforced_concrete":
                color = tuple(int(c) for c in materials["slab"][:3]) + (255,)
            else:
                color = tuple(M.MATERIALS.get(g.get("material", "sand"), M.MATERIALS["sand"])["rgb"]) + (255,)
            ztop = z + th / 2.0 if g.get("material") == "reinforced_concrete" else z - th / 2.0
            meshes.append(prim.box([(x0 + x1) / 2, (y0 + y1) / 2, ztop],
                                   [x1 - x0, y1 - y0, th], color))
            berm = float(g.get("berm_height", 0.0) or 0.0)
            if berm > 0:
                meshes.append(prim.box([(x0 + x1) / 2, (y0 + y1) / 2, z + berm / 2.0],
                                       [(x1 - x0) * 0.5, (y1 - y0) * 0.5, berm], color))
        # Tag everything this element produced with its (class, material, element_id).
        added = len(meshes) - n0
        if added:
            kinds.extend([(M.element_class(elem), M.element_material(elem, preset), elem["id"])] * added)

    _emit(_garden_meshes(param, levels, materials), "garden")
    _emit(_screen_meshes(param, walls, levels, materials), "screen")
    _emit(_muqarnas_meshes(param, walls, levels, materials), "muqarnas")
    if detail.get("generate_facade_bays", True):
        _emit(_facade_meshes(param, walls, levels, style, materials), "facade")
    if detail.get("generate_iwans", True):
        _emit(_iwan_meshes(param, walls, levels, style, materials), "iwan")
        _emit(_pishtaq_meshes(param, walls, levels, materials), "pishtaq")
    if detail.get("generate_domes", True):
        _emit(_dome_meshes(param, levels, style, materials), "dome")
        _emit(_vault_meshes(param, levels, style, materials), "vault")
    _emit(_arcade_meshes(param, levels, style, materials), "arcade")
    if detail.get("generate_decorations", True):
        _emit(_decoration_meshes(param, walls, levels, style, materials), "decoration")

    # Ships (aircraft carriers): a lofted hull + deck/island/water. Tagged per-mesh with
    # explicit (class, material, id) — NOT via _emit (which would stamp one kind for the
    # whole feature); the sea plane carries class "glb_only" so the voxel exporter drops it.
    if detail.get("generate_ships", True):
        for s in param.get("ships", []):
            ship_meshes, ship_kinds = ship.build_ship(s, levels)
            meshes.extend(ship_meshes)
            kinds.extend(ship_kinds)

    if not meshes:
        raise RuntimeError("visual exporter produced no meshes")
    return meshes, kinds, csg_stats


def _merge_textured_meshes(meshes):
    """Merge all meshes sharing the same external texture image into one mesh per image,
    so trimesh embeds each texture only once. Without this, N walls sharing a wall
    texture each embed a full copy of the image → GLB bloats to tens of MB. Plain
    (color-only) meshes are left untouched. The litematic path is unaffected — it
    consumes collect_meshes() directly, not this merged list.
    """
    plain = []
    groups: dict[int, tuple] = {}      # id(image) -> (image, [meshes])
    for m in meshes:
        vis = getattr(m, "visual", None)
        img = getattr(vis, "image", None)
        if isinstance(vis, trimesh.visual.TextureVisuals) and img is not None:
            groups.setdefault(id(img), (img, []))[1].append(m)
        else:
            plain.append(m)
    merged = list(plain)
    for img, group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        verts, faces, uvs, off = [], [], [], 0
        for m in group:
            verts.append(m.vertices)
            faces.append(m.faces + off)
            uvs.append(m.visual.uv)
            off += len(m.vertices)
        mm = trimesh.Trimesh(vertices=np.vstack(verts), faces=np.vstack(faces), process=False)
        mm.visual = trimesh.visual.TextureVisuals(uv=np.vstack(uvs), image=img)
        merged.append(mm)
    return merged


def build_visual_glb(param: dict, bim: dict, glb_path: str | Path) -> dict:
    meshes, _kinds, csg_stats = collect_meshes(param, bim)
    meshes = _merge_textured_meshes(meshes)
    scene = trimesh.Scene(meshes)
    scene.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    out = Path(glb_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    scene.export(str(out))
    bounds = scene.bounds
    return {
        "visual_glb": True,
        "visual_meshes": len(meshes),
        "components": len(meshes),
        "vertices": int(sum(len(m.vertices) for m in meshes)),
        "bbox_min_m": [round(float(v), 3) for v in bounds[0]],
        "bbox_max_m": [round(float(v), 3) for v in bounds[1]],
        "csg_backend": _BOOLEAN_OK,
        "csg_walls_cut": csg_stats["walls_with_openings"],
        "csg_failures": csg_stats["boolean_failures"],
        "iwans_recessed": csg_stats["iwans_recessed"],
    }


def _index_openings_by_wall(elements, walls, levels):
    by_wall: dict[str, list[dict]] = {}
    for elem in elements:
        if elem["type"] not in ("Door", "Window"):
            continue
        host_id = elem["geometry"].get("host_id")
        if host_id and host_id in walls:
            by_wall.setdefault(host_id, []).append(elem)
    return by_wall


def _index_iwans_by_wall(iwans, walls):
    by_wall: dict[str, list[dict]] = {}
    for it in iwans:
        wid = it.get("host_wall")
        if wid and wid in walls:
            by_wall.setdefault(wid, []).append(it)
    return by_wall


def _build_opening_cut_boxes(openings, wall_angle, wall_thickness, wall_z):
    nx = -math.sin(wall_angle)
    ny = math.cos(wall_angle)
    overshoot_perp = max(wall_thickness * 0.6, 0.15)
    overshoot_z = 0.01
    boxes = []
    for op in openings:
        g = op["geometry"]
        c = g["center"]
        width = float(g.get("width", 0.9))
        h = float(g.get("height", 2.1))
        sill = float(g.get("sill_height", 0.0))
        mid_x = c[0] + nx * wall_thickness / 2.0
        mid_y = c[1] + ny * wall_thickness / 2.0
        boxes.append(prim.box(
            [mid_x, mid_y, wall_z + sill + h / 2.0 + overshoot_z / 2.0],
            [width, wall_thickness + overshoot_perp, h + overshoot_z],
            [200, 0, 0, 255], wall_angle,
        ))
    return boxes


def _build_iwan_cut_box(item, host_wall, wall_z):
    a, b = host_wall["geometry"]["centerline"]
    wall_angle = math.atan2(b[1] - a[1], b[0] - a[0])
    wall_thickness = float(host_wall["geometry"].get("thickness", 0.24))
    nx, ny = _wall_into_material(host_wall)
    width = float(item.get("width_m", 5.0))
    height = float(item.get("height_m", 6.0))
    depth = float(item.get("depth_m", 1.5))
    overshoot = 0.2
    cut_depth = max(depth, wall_thickness) + 2 * overshoot
    center = item["center"]
    perp_shift = cut_depth / 2.0 - overshoot
    cx = center[0] + nx * perp_shift
    cy = center[1] + ny * perp_shift
    return prim.box(
        [cx, cy, wall_z + height / 2.0 + 0.01],
        [width, cut_depth, height + 0.04],
        [200, 0, 0, 255], wall_angle,
    )


def _subtract_boxes(wall_mesh, boxes, wall_color):
    if not boxes:
        return wall_mesh, True
    try:
        result = trimesh.boolean.difference([wall_mesh] + boxes)
        if result is None or len(result.vertices) == 0 or len(result.faces) == 0:
            return wall_mesh, False
        result.visual.face_colors = np.array(wall_color, dtype=np.uint8)
        return result, True
    except Exception:
        return wall_mesh, False


def _host_panel_offset(elem, walls):
    host = walls.get(elem["geometry"].get("host_id"))
    if not host:
        return 0.0, 0.0, 0.0
    thickness = float(host["geometry"].get("thickness", 0.24))
    a, b = host["geometry"]["centerline"]
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    return -math.sin(angle) * thickness / 2.0, math.cos(angle) * thickness / 2.0, thickness


def _wall_local_perpendicular(wall):
    """(+Y_local) unit vector — the side the wall mesh extrudes toward."""
    a, b = wall["geometry"]["centerline"]
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    return -math.sin(angle), math.cos(angle)


def _wall_into_material(wall):
    """Unit vector pointing into the surrounding wing material (away from the open
    side). For external walls this matches +Y_local; for courtyard walls it is
    inverted because +Y_local points into the open courtyard, not the wing."""
    nx, ny = _wall_local_perpendicular(wall)
    if wall["geometry"].get("category") == "courtyard":
        return -nx, -ny
    return nx, ny


def _wall_open_normal(wall):
    """Unit vector pointing into the open space facing this wall (outside the building
    for external walls; into the courtyard for courtyard walls)."""
    nx, ny = _wall_into_material(wall)
    return -nx, -ny


def _window_mesh(elem, walls, levels, style, materials, detail):
    g = elem["geometry"]
    level = levels[elem["level"]]
    z = level["elevation_m"] + g.get("sill_height", 0.9)
    angle = _host_angle(elem, walls)
    ox, oy, host_thickness = _host_panel_offset(elem, walls)
    center = [g["center"][0] + ox, g["center"][1] + oy, z]
    depth = style.get("frame_depth_m", 0.08)
    frame_w = style.get("frame_width_m", 0.12)
    meshes = []
    shape = g.get("shape") or style.get("window_shape", "rect")
    if shape in ("pointed_arch", "horseshoe_arch", "round_arch") and detail.get("generate_arches", True):
        if shape == "horseshoe_arch":
            meshes.append(prim.horseshoe_arch_panel(center, g["width"], g["height"], depth * 0.6, materials["glass"], angle))
        elif shape == "round_arch":
            meshes.append(prim.round_arch_panel(center, g["width"], g["height"], depth * 0.6, materials["glass"], angle))
        else:
            meshes.append(prim.pointed_arch_panel(center, g["width"], g["height"], depth * 0.6, materials["glass"], angle))
        if shape == "round_arch":
            # straight jambs framed up to the spring line (height - radius); a wider round-arch trim
            # hugs the full semicircle outline behind the glass (frame-coloured border).
            spring_frac = max(0.1, (g["height"] - g["width"] / 2.0) / g["height"])
            meshes.extend(prim.frame(center, g["width"], g["height"] * spring_frac, depth * 1.5, frame_w, materials["frame"], angle))
            meshes.append(prim.round_arch_panel(center, g["width"] * 1.12, g["height"], depth * 1.7, materials["frame"], angle))
        else:
            meshes.extend(prim.frame(center, g["width"], g["height"] * 0.62, depth * 1.5, frame_w, materials["frame"], angle))
            meshes.append(prim.pointed_arch_panel([center[0], center[1], center[2] + g["height"] * 0.58], g["width"] * 1.12, g["height"] * 0.42, depth * 1.7, materials["frame"], angle))
    elif shape == "curtain_grid":
        meshes.append(prim.box([center[0], center[1], center[2] + g["height"] / 2], [g["width"], depth * 0.5, g["height"]], materials["glass"], angle))
        meshes.extend(prim.grid_frame(center, g["width"], g["height"], depth * 1.4, frame_w, materials["frame"], angle, verticals=2, horizontals=2))
    else:
        meshes.append(prim.box([center[0], center[1], center[2] + g["height"] / 2], [g["width"], depth * 0.5, g["height"]], materials["glass"], angle))
        meshes.extend(prim.frame(center, g["width"], g["height"], depth * 1.3, frame_w, materials["frame"], angle))
    return meshes


def _door_mesh(elem, walls, levels, style, materials, detail):
    g = elem["geometry"]
    level = levels[elem["level"]]
    z = level["elevation_m"]
    angle = _host_angle(elem, walls)
    ox, oy, host_thickness = _host_panel_offset(elem, walls)
    center = [g["center"][0] + ox, g["center"][1] + oy, z]
    meshes = []
    shape = g.get("shape") or style.get("door_shape", "rect")
    if shape in ("pointed_arch", "horseshoe_arch") and detail.get("generate_arches", True):
        panel = prim.pointed_arch_panel(center, g["width"], g["height"], 0.12, materials["door"], angle)
        meshes.append(panel)
        meshes.append(prim.pointed_arch_panel([center[0], center[1], center[2] + g["height"] * 0.08], g["width"] * 1.18, g["height"] * 1.05, 0.16, materials["frame"], angle))
    else:
        meshes.append(prim.box([center[0], center[1], z + g["height"] / 2], [g["width"], 0.1, g["height"]], materials["door"], angle))
        meshes.extend(prim.frame(center, g["width"], g["height"], 0.18, style.get("frame_width_m", 0.12), materials["frame"], angle))
    return meshes


def _roof_mesh(elem, levels, style, materials, param=None):
    g = elem["geometry"]
    # Roof caps the top of ITS OWN level (so mixed-height buildings each get a roof at
    # their own top); falls back to the global tallest level if the level is unknown.
    _lvl = levels.get(elem.get("level"))
    top = (_lvl["elevation_m"] + _lvl["height_m"]) if _lvl else \
        max(lv["elevation_m"] + lv["height_m"] for lv in levels.values())
    x0, y0, x1, y1 = _bbox(g["profile"])
    rtype = g.get("roof_type", "flat")
    thickness = g.get("height", 0.2)
    holes = g.get("holes") or []
    meshes: list = []
    eave = float(g.get("eave_height_m", top))
    if rtype == "gable":
        ridge_h = float(g.get("ridge_height_m", eave + min(x1 - x0, y1 - y0) * 0.35))
        rs = g.get("ridge_start")
        re = g.get("ridge_end")
        ridge_along_x = True if (rs and re) and abs(re[0] - rs[0]) >= abs(re[1] - rs[1]) else (x1 - x0) >= (y1 - y0)
        meshes.extend(prim.gable_roof(x0, y0, x1, y1, eave, ridge_h, ridge_along_x, materials["roof"]))
    elif rtype == "hip":
        ridge_h = float(g.get("ridge_height_m", eave + min(x1 - x0, y1 - y0) * 0.32))
        meshes.extend(prim.hip_roof(x0, y0, x1, y1, eave, ridge_h, materials["roof"]))
    elif rtype == "pyramidal":
        apex_h = float(g.get("ridge_height_m", eave + max(x1 - x0, y1 - y0) * 0.4))
        meshes.extend(prim.pyramidal_roof(x0, y0, x1, y1, eave, apex_h, materials["roof"]))
    elif rtype == "tent_dome":
        radius = min(x1 - x0, y1 - y0) / 2.0
        height = float(g.get("ridge_height_m", eave + radius * 1.1)) - eave
        meshes.append(prim.tent_dome([(x0 + x1) / 2, (y0 + y1) / 2, eave], radius, height, materials["roof"], int(g.get("sides", 12))))
    elif rtype == "onion_dome":
        radius = min(x1 - x0, y1 - y0) / 2.0
        height = float(g.get("ridge_height_m", eave + radius * 1.6)) - eave
        meshes.append(prim.onion_dome([(x0 + x1) / 2, (y0 + y1) / 2, eave], radius, height, materials["accent"]))
    else:
        roof_mesh = prim.box([(x0 + x1) / 2, (y0 + y1) / 2, top + thickness / 2],
                             [x1 - x0, y1 - y0, thickness], materials["roof"])
        if holes and _BOOLEAN_OK:
            hole_boxes = []
            for hole in holes:
                hx0, hy0, hx1, hy1 = _bbox(hole)
                hole_boxes.append(prim.box(
                    [(hx0 + hx1) / 2, (hy0 + hy1) / 2, top + thickness / 2],
                    [hx1 - hx0, hy1 - hy0, thickness + 0.4],
                    [200, 0, 0, 255]))
            try:
                cut = trimesh.boolean.difference([roof_mesh] + hole_boxes)
                if cut is not None and len(cut.vertices) > 0:
                    cut.visual.face_colors = np.array(materials["roof"], dtype=np.uint8)
                    roof_mesh = cut
            except Exception:
                pass
        _apply_flat_roof_texture(roof_mesh, param, x0, y0, x1, y1)
        meshes.append(roof_mesh)
        if style.get("roof_detail") in ("tile_parapet", "parapet", "flat_mechanical"):
            h, t = 0.45, 0.18
            meshes.extend([
                prim.box([(x0 + x1) / 2, y0, top + h / 2], [x1 - x0, t, h], materials["accent"]),
                prim.box([(x0 + x1) / 2, y1, top + h / 2], [x1 - x0, t, h], materials["accent"]),
                prim.box([x0, (y0 + y1) / 2, top + h / 2], [t, y1 - y0, h], materials["accent"]),
                prim.box([x1, (y0 + y1) / 2, top + h / 2], [t, y1 - y0, h], materials["accent"]),
            ])
    return meshes


def _stair_mesh(x0, y0, x1, y1, z, height, steps, color):
    meshes = []
    depth = (y1 - y0) / max(steps, 1)
    for i in range(steps):
        h = height * (i + 1) / steps
        meshes.append(prim.box([(x0 + x1) / 2, y0 + depth * (i + 0.5), z + h / 2], [x1 - x0, depth, h], color))
    return meshes


def _facade_meshes(param, walls, levels, style, materials):
    meshes = []
    facades = list(param.get("facades", []))
    if not facades and style.get("facade_pattern") in ("arched_bays", "curtain_wall", "pilaster_bays", "arcade"):
        for wall_id, wall in walls.items():
            if wall["geometry"].get("category") == "external":
                facades.append({"id": f"{wall_id}_facade", "host_wall": wall_id, "pattern": style["facade_pattern"]})
    for f in facades:
        wall = walls.get(f["host_wall"])
        if not wall:
            continue
        g = wall["geometry"]
        a, b = g["centerline"]
        length = math.dist(a, b)
        angle = math.atan2(b[1] - a[1], b[0] - a[0])
        level = levels.get(wall["level"]) or next(iter(levels.values()))
        bay_count = int(f.get("bay_count") or max(2, length // 4))
        bay_w = length / bay_count
        for i in range(bay_count + 1):
            x = i * bay_w
            p = [a[0] + math.cos(angle) * x, a[1] + math.sin(angle) * x, level["elevation_m"]]
            meshes.append(_local_facade_box(p, angle, 0.10, 0.16, min(g.get("height", level["height_m"]), level["height_m"]), materials["accent"]))
        cornice = f.get("cornice_height_m", style.get("cornice_height_m", 0.22))
        if cornice:
            center = [a[0] + math.cos(angle) * length / 2, a[1] + math.sin(angle) * length / 2, level["elevation_m"] + g.get("height", level["height_m"]) - cornice / 2]
            meshes.append(prim.box(center, [length, 0.18, cornice], materials["decoration"], angle))
        band = f.get("tile_band_height_m", style.get("tile_band_height_m", 0.0))
        if band:
            center = [a[0] + math.cos(angle) * length / 2, a[1] + math.sin(angle) * length / 2, level["elevation_m"] + g.get("height", level["height_m"]) * 0.78]
            band_mesh = prim.box(center, [length, 0.16, band], materials["accent"], angle)
            pattern = style.get("tile_band_pattern", "kashi_star")
            if pattern and pattern != "none":
                tex = tx.get_texture(pattern)
                if tex is not None:
                    prim.apply_planar_texture(band_mesh, tex, axis="xz",
                                              repeat_u=max(2, int(length / 1.0)), repeat_v=1)
            meshes.append(band_mesh)
    return meshes


def _iwan_meshes(param, walls, levels, style, materials):
    meshes = []
    iwans = list(param.get("iwans", []))
    if not iwans and style.get("entrance_type") in ("iwan", "muqarnas_portal"):
        for door in param.get("doors", []):
            if "iwan" in str(door.get("style", "")) or door["id"].lower().endswith("door"):
                iwans.append({
                    "id": f"{door['id']}_iwan",
                    "level": door["level"],
                    "host_wall": door["host_id"],
                    "center": door["center"],
                    "width_m": door["width_m"] * 1.8,
                    "height_m": door["height_m"] * 1.75,
                    "depth_m": 1.5,
                })
    for item in iwans:
        wall = walls.get(item.get("host_wall"))
        if not wall:
            continue
        level = levels.get(item.get("level") or wall["level"]) or next(iter(levels.values()))
        meshes.extend(_recessed_iwan_assembly(item, wall, level, materials))
    return meshes


def _recessed_iwan_assembly(item, host_wall, level, materials):
    """Build interior surfaces and front frame of a recessed iwan (the wall hole is
    cut elsewhere via CSG). Uses the wall category to point the recess into the
    surrounding wing, regardless of whether the host is an external or courtyard
    wall."""
    a, b = host_wall["geometry"]["centerline"]
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    z = level["elevation_m"]
    center = item["center"]
    width = float(item.get("width_m", 5.0))
    height = float(item.get("height_m", 6.0))
    depth = float(item.get("depth_m", 1.5))
    nx, ny = _wall_into_material(host_wall)       # toward wing
    ox, oy = _wall_open_normal(host_wall)         # toward open side
    dxn, dyn = math.cos(angle), math.sin(angle)
    meshes: list = []

    side_t = 0.18
    for sign in (-1.0, 1.0):
        axis_offset = sign * (width / 2.0 + side_t / 2.0)
        perp_offset = depth / 2.0
        sx = center[0] + dxn * axis_offset + nx * perp_offset
        sy = center[1] + dyn * axis_offset + ny * perp_offset
        meshes.append(prim.box([sx, sy, z + height / 2.0],
                               [side_t, depth, height], materials["wall"], angle))

    back_t = 0.24
    perp_offset = depth + back_t / 2.0
    bx = center[0] + nx * perp_offset
    by = center[1] + ny * perp_offset
    meshes.append(prim.box([bx, by, z + height / 2.0],
                           [width + side_t * 2.1, back_t, height], materials["wall"], angle))

    vault_height = max(height * 0.22, 0.45)
    vault_z = z + height - vault_height
    perp_mid = depth / 2.0
    vx = center[0] + nx * perp_mid
    vy = center[1] + ny * perp_mid
    meshes.append(prim.barrel_vault([vx, vy, vault_z], width, vault_height, depth,
                                    materials["decoration"], angle))

    floor_t = 0.05
    flx = center[0] + nx * (depth / 2.0)
    fly = center[1] + ny * (depth / 2.0)
    meshes.append(prim.box([flx, fly, z + floor_t / 2.0],
                           [width, depth, floor_t], materials["accent"], angle))

    # Front pointed-arch frame projects slightly toward the open side of the wall.
    proj = 0.04
    px = center[0] + ox * proj
    py = center[1] + oy * proj
    meshes.append(prim.pointed_arch_panel([px, py, z], width * 1.08, height * 1.05, 0.18,
                                          materials["frame"], angle))
    spandrel_h = height * 0.30
    spandrel_z = z + height * 0.74
    sp_x = center[0] + ox * (proj + 0.02)
    sp_y = center[1] + oy * (proj + 0.02)
    meshes.append(prim.pointed_arch_panel([sp_x, sp_y, spandrel_z], width * 1.15, spandrel_h, 0.14,
                                          materials["accent"], angle))
    return meshes


def _muqarnas_meshes(param, walls, levels, materials):
    iwans_by_id = {iw["id"]: iw for iw in param.get("iwans", [])}
    meshes = []
    for mq in param.get("muqarnas", []):
        host_iwan_id = mq.get("host_iwan")
        if host_iwan_id and host_iwan_id in iwans_by_id:
            iwan = iwans_by_id[host_iwan_id]
            wall = walls.get(iwan.get("host_wall"))
            if not wall:
                continue
            angle = _wall_angle(wall)
            center = iwan["center"]
            iwan_w = float(iwan.get("width_m", 5.0))
            iwan_h = float(iwan.get("height_m", 6.0))
            iwan_d = float(iwan.get("depth_m", 1.5))
            level = levels.get(mq.get("level") or iwan.get("level") or wall["level"]) or next(iter(levels.values()))
            z = level["elevation_m"] + iwan_h * 0.68
            width = float(mq.get("width_m", iwan_w * 0.95))
            height = float(mq.get("height_m", iwan_h * 0.32))
            depth = float(mq.get("depth_m", iwan_d * 0.9))
            # Muqarnas hangs from the iwan soffit, so it sits inside the recess
            # (toward the wing material from the wall face).
            nx, ny = _wall_into_material(wall)
            cx = center[0] + nx * (depth / 2.0)
            cy = center[1] + ny * (depth / 2.0)
            mqcenter = [cx, cy, z]
        else:
            level = levels.get(mq.get("level")) or next(iter(levels.values()))
            center = mq.get("center", [0, 0])
            z = level["elevation_m"] + float(mq.get("base_height_m", level["height_m"] * 0.65))
            width = float(mq.get("width_m", 4.0))
            height = float(mq.get("height_m", 2.0))
            depth = float(mq.get("depth_m", 1.5))
            angle = float(mq.get("angle_rad", 0.0))
            mqcenter = [center[0], center[1], z]
        tiers = int(mq.get("tiers", 4))
        cells_base = int(mq.get("cells_base", 10))
        half = bool(mq.get("half", True))
        portal = prim.muqarnas_portal(mqcenter, width, height, depth,
                                      materials["decoration"], materials["accent"],
                                      tiers=tiers, cells_base=cells_base, half=half)
        # Rotate the muqarnas group to align with the host wall angle if non-zero
        if abs(angle) > 1e-6:
            R = trimesh.transformations.rotation_matrix(angle, [0, 0, 1], mqcenter)
            rotated = []
            for m in portal:
                mc = m.copy()
                mc.apply_transform(R)
                rotated.append(mc)
            meshes.extend(rotated)
        else:
            meshes.extend(portal)
    return meshes


def _screen_meshes(param, walls, levels, materials):
    if not param.get("screens"):
        return []
    apertures = {}
    for collection, default_sill in (("doors", 0.0), ("windows", 0.9)):
        for it in param.get(collection, []):
            apertures[it["id"]] = (it, collection, default_sill)
    color = materials.get("frame", [110, 75, 45, 255])
    accent = materials.get("decoration", [40, 170, 190, 255])
    meshes = []
    for sc in param.get("screens", []):
        host_id = sc.get("host_id")
        ap_info = apertures.get(host_id) if host_id else None
        if ap_info:
            ap, kind, default_sill = ap_info
            wall = walls.get(ap.get("host_id"))
            if not wall:
                continue
            level = levels.get(ap.get("level")) or next(iter(levels.values()))
            angle = _wall_angle(wall)
            center = ap["center"]
            panel_w = float(sc.get("panel_width_m", ap.get("width_m", 1.2)))
            panel_h = float(sc.get("panel_height_m", ap.get("height_m", 1.4)))
            sill = float(ap.get("sill_height_m", default_sill))
            z = level["elevation_m"] + sill + panel_h / 2.0
        else:
            wall_id = sc.get("host_wall")
            wall = walls.get(wall_id) if wall_id else None
            if not wall or "center" not in sc:
                continue
            level = levels.get(sc.get("level")) or next(iter(levels.values()))
            angle = _wall_angle(wall)
            center = sc["center"]
            panel_w = float(sc.get("panel_width_m", 1.2))
            panel_h = float(sc.get("panel_height_m", 1.5))
            z = level["elevation_m"] + float(sc.get("sill_m", 0.9)) + panel_h / 2.0
        thickness = float(sc.get("thickness_m", 0.045))
        cell = float(sc.get("cell_size_m", 0.18))
        pattern = sc.get("pattern", "lattice")
        host_thickness = float(wall["geometry"].get("thickness", 0.24))
        nx, ny = -math.sin(angle), math.cos(angle)
        cx = center[0] + nx * host_thickness / 2.0
        cy = center[1] + ny * host_thickness / 2.0
        meshes.extend(_lattice_screen([cx, cy, z], panel_w, panel_h, thickness,
                                      cell, angle, color, accent, pattern))
    return meshes


def _lattice_screen(center, width, height, thickness, cell, angle, color, accent, pattern):
    meshes = []
    bar_t = max(cell * 0.18, 0.020)
    frame_d = thickness * 1.4
    fr_t = bar_t * 1.5
    meshes.extend(prim.frame(center, width, height, frame_d, fr_t, color, angle))
    dxn, dyn = math.cos(angle), math.sin(angle)

    if pattern in ("lattice", "hex_grid", "fine_lattice", "coarse_lattice"):
        n_v = max(int(width / cell), 1)
        n_h = max(int(height / cell), 1)
        for i in range(1, n_v):
            offset = -width / 2.0 + width * i / n_v
            ox = center[0] + dxn * offset
            oy = center[1] + dyn * offset
            meshes.append(prim.box([ox, oy, center[2]], [bar_t, thickness, height],
                                   color, angle))
        for j in range(1, n_h):
            z_off = -height / 2.0 + height * j / n_h
            meshes.append(prim.box([center[0], center[1], center[2] + z_off],
                                   [width, thickness, bar_t], color, angle))
    elif pattern in ("8point_star", "rosette"):
        # Approximate via lattice plus rosette dots at intersections
        n_v = max(int(width / cell), 1)
        n_h = max(int(height / cell), 1)
        for i in range(1, n_v):
            offset = -width / 2.0 + width * i / n_v
            ox = center[0] + dxn * offset
            oy = center[1] + dyn * offset
            meshes.append(prim.box([ox, oy, center[2]], [bar_t, thickness, height], color, angle))
        for j in range(1, n_h):
            z_off = -height / 2.0 + height * j / n_h
            meshes.append(prim.box([center[0], center[1], center[2] + z_off],
                                   [width, thickness, bar_t], color, angle))
        # Stars at grid intersections (small accent disks)
        for i in range(1, n_v):
            for j in range(1, n_h):
                offset = -width / 2.0 + width * i / n_v
                z_off = -height / 2.0 + height * j / n_h
                ox = center[0] + dxn * offset
                oy = center[1] + dyn * offset
                meshes.append(prim.cylinder([ox, oy, center[2] + z_off],
                                            bar_t * 1.4, thickness * 1.2, accent, sections=8))
    return meshes


def _garden_meshes(param, levels, materials):
    meshes = []
    water_color = materials.get("water") or [60, 130, 180, 180]
    bottom_color = [50, 95, 130, 255]
    paving_a = materials.get("paving") or [205, 195, 175, 255]
    paving_b = [185, 168, 138, 255]
    rim_color = materials.get("frame", [230, 210, 170, 255])

    canals = list(param.get("canals", []))

    for pool in param.get("pools", []):
        lv = levels.get(pool.get("level")) or next(iter(levels.values()))
        z = float(lv["elevation_m"])
        poly = pool["polygon"]
        x0, y0, x1, y1 = _bbox(poly)
        w, d = x1 - x0, y1 - y0
        depth = float(pool.get("depth_m", 0.35))
        rim_h = float(pool.get("rim_height_m", 0.12))
        rim_t = 0.25
        meshes.append(prim.box([(x0 + x1) / 2, (y0 + y1) / 2, z - depth - 0.005],
                               [w * 0.97, d * 0.97, depth], bottom_color))
        meshes.append(prim.box([(x0 + x1) / 2, (y0 + y1) / 2, z - 0.015],
                               [w * 0.95, d * 0.95, 0.025], water_color))
        meshes.append(prim.box([(x0 + x1) / 2, y0, z + rim_h / 2],
                               [w + rim_t * 2, rim_t, rim_h], rim_color))
        meshes.append(prim.box([(x0 + x1) / 2, y1, z + rim_h / 2],
                               [w + rim_t * 2, rim_t, rim_h], rim_color))
        meshes.append(prim.box([x0, (y0 + y1) / 2, z + rim_h / 2],
                               [rim_t, d, rim_h], rim_color))
        meshes.append(prim.box([x1, (y0 + y1) / 2, z + rim_h / 2],
                               [rim_t, d, rim_h], rim_color))

    for canal in canals:
        lv = levels.get(canal.get("level")) or next(iter(levels.values()))
        z = float(lv["elevation_m"])
        s, e = canal["start"], canal["end"]
        dx, dy = e[0] - s[0], e[1] - s[1]
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        angle = math.atan2(dy, dx)
        cx, cy = (s[0] + e[0]) / 2, (s[1] + e[1]) / 2
        ww = float(canal.get("width_m", 1.0))
        depth = float(canal.get("depth_m", 0.20))
        meshes.append(prim.box([cx, cy, z - depth - 0.005],
                               [length, ww * 0.95, depth], bottom_color, angle))
        meshes.append(prim.box([cx, cy, z - 0.015], [length, ww * 0.92, 0.025], water_color, angle))
        nx, ny = -math.sin(angle), math.cos(angle)
        for sign in (-1.0, 1.0):
            ox = cx + nx * sign * (ww / 2 + 0.08)
            oy = cy + ny * sign * (ww / 2 + 0.08)
            meshes.append(prim.box([ox, oy, z + 0.05], [length, 0.16, 0.10], rim_color, angle))

    for g in param.get("gardens", []):
        lv = levels.get(g.get("level")) or next(iter(levels.values()))
        z = float(lv["elevation_m"])
        poly = g["polygon"]
        x0, y0, x1, y1 = _bbox(poly)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        canal_half = 0.65
        for axis in g.get("axis_canal_half_m_override", []):  # never iterated; just placeholder
            canal_half = float(axis)
        t = 0.04
        quads = [
            (x0, y0, cx - canal_half, cy - canal_half, paving_a),
            (cx + canal_half, y0, x1, cy - canal_half, paving_b),
            (x0, cy + canal_half, cx - canal_half, y1, paving_b),
            (cx + canal_half, cy + canal_half, x1, y1, paving_a),
        ]
        for qx0, qy0, qx1, qy1, color in quads:
            if qx1 > qx0 and qy1 > qy0:
                meshes.append(prim.box([(qx0 + qx1) / 2, (qy0 + qy1) / 2, z + t / 2],
                                       [qx1 - qx0, qy1 - qy0, t], color))
    return meshes


def _pishtaq_meshes(param, walls, levels, materials):
    iwans_by_id = {iw["id"]: iw for iw in param.get("iwans", [])}
    meshes = []
    for item in param.get("pishtaqs", []):
        host_iwan_id = item.get("host_iwan")
        iwan = iwans_by_id.get(host_iwan_id) if host_iwan_id else None
        if iwan:
            wall = walls.get(iwan.get("host_wall"))
            center = iwan["center"]
            iwan_w = float(iwan.get("width_m", 5.0))
            iwan_h = float(iwan.get("height_m", 6.0))
        else:
            wall = walls.get(item.get("host_wall"))
            if not wall or "center" not in item:
                continue
            center = item["center"]
            iwan_w = float(item.get("opening_width_m", 4.0))
            iwan_h = float(item.get("opening_height_m", 5.0))
        if not wall:
            continue
        level = levels.get(item.get("level") or wall["level"]) or next(iter(levels.values()))
        z = level["elevation_m"]
        angle = _wall_angle(wall)
        outer_w = float(item.get("width_m", iwan_w * 1.30))
        outer_h = float(item.get("height_m", iwan_h * 1.40))
        thickness = float(item.get("frame_thickness_m", 0.45))
        proj = float(item.get("projection_m", 0.18))
        # Pishtaq projects toward the open side of the host wall (outside for external
        # walls; into the courtyard for courtyard walls).
        ox, oy = _wall_open_normal(wall)
        dxn, dyn = math.cos(angle), math.sin(angle)
        front_offset = proj + thickness / 2.0
        fx = ox * front_offset
        fy = oy * front_offset

        # Top bar (above the iwan crown)
        top_h = max(outer_h - iwan_h, 0.0)
        if top_h > 0:
            tcx = center[0] + fx
            tcy = center[1] + fy
            tcz = z + iwan_h + top_h / 2.0
            meshes.append(prim.box([tcx, tcy, tcz], [outer_w, thickness, top_h],
                                   materials["accent"], angle))
            if item.get("calligraphy_band", True):
                band_h = min(top_h * 0.40, 0.55)
                band_z = z + outer_h - band_h / 2.0
                band_proj = front_offset + 0.04
                bcx = center[0] + ox * band_proj
                bcy = center[1] + oy * band_proj
                band_mesh = prim.box([bcx, bcy, band_z],
                                     [outer_w * 1.04, thickness * 0.6, band_h],
                                     materials["decoration"], angle)
                tex = tx.get_texture("calligraphy_band")
                if tex is not None:
                    # Texture is applied in world XZ — fine for a wall-aligned strip
                    prim.apply_planar_texture(band_mesh, tex, axis="xz",
                                              repeat_u=max(1, int(outer_w / 1.5)), repeat_v=1)
                meshes.append(band_mesh)
        # Side bars
        side_w = (outer_w - iwan_w) / 2.0
        if side_w > 0:
            for sign in (-1.0, 1.0):
                axis_off = sign * (iwan_w / 2.0 + side_w / 2.0)
                scx = center[0] + dxn * axis_off + fx
                scy = center[1] + dyn * axis_off + fy
                scz = z + outer_h / 2.0
                meshes.append(prim.box([scx, scy, scz], [side_w, thickness, outer_h],
                                       materials["accent"], angle))
        # Vertical accent strips on the inner edges
        strip_w = max(0.18, side_w * 0.35)
        for sign in (-1.0, 1.0):
            axis_off = sign * (iwan_w / 2.0 + strip_w / 2.0)
            stripe_proj = front_offset + 0.02
            sx = center[0] + dxn * axis_off + ox * stripe_proj
            sy = center[1] + dyn * axis_off + oy * stripe_proj
            sz = z + iwan_h * 0.55
            meshes.append(prim.box([sx, sy, sz], [strip_w, thickness * 0.5, iwan_h * 0.95],
                                   materials["decoration"], angle))
    return meshes


def _dome_meshes(param, levels, style, materials):
    meshes = []
    domes = list(param.get("domes", []))
    if not domes and style.get("roof_detail") == "dome":
        top = max(lv["elevation_m"] + lv["height_m"] for lv in levels.values())
        domes.append({"id": "auto_dome", "center": [0, 0], "radius_m": 3.0, "height_m": 2.2, "base_height_m": top})
    for item in domes:
        base_z = item.get("base_height_m")
        if base_z is None:
            lv = levels.get(item.get("level")) or max(levels.values(), key=lambda x: x["elevation_m"])
            base_z = lv["elevation_m"] + lv["height_m"]
        meshes.extend(_assemble_dome(item, base_z, materials))
    return meshes


def _assemble_dome(item, base_z, materials):
    """Compose dome with optional pendentives, drum (with windows), shell, finial."""
    cx, cy = item["center"][0], item["center"][1]
    radius = float(item.get("radius_m", 3.0))
    shell_h = float(item.get("height_m", 2.0))
    shape = item.get("shape", "hemisphere")
    meshes: list = []
    z = float(base_z)

    pend_size = float(item.get("pendentive_size_m", 0.0) or 0.0)
    pend_height = float(item.get("pendentive_height_m", 0.0) or 0.0)
    if pend_size > 0:
        if pend_height <= 0:
            pend_height = pend_size * 0.45
        meshes.extend(prim.dome_pendentives([cx, cy, z], pend_size, z + pend_height, radius, materials["accent"]))
        z += pend_height

    drum_h = float(item.get("drum_height_m", 0.0) or 0.0)
    if drum_h > 0:
        meshes.extend(_drum_with_windows([cx, cy, z], radius * 1.02, drum_h,
                                         int(item.get("drum_window_count", 12)),
                                         materials["wall"], materials["glass"], materials["accent"]))
        z += drum_h
    else:
        meshes.append(prim.cylinder([cx, cy, z - 0.18], radius * 1.04, 0.18, materials["accent"]))

    shell_center = [cx, cy, z]
    tile_pattern = item.get("tile_pattern", "kashi_star")
    if shape == "onion":
        outer = prim.onion_dome(shell_center, radius, shell_h, materials["accent"])
        if tile_pattern and tile_pattern != "none":
            tex = tx.get_texture(tile_pattern)
            if tex is not None:
                prim.apply_cylindrical_texture(outer, tex, (cx, cy), repeat_u=8.0, repeat_v=3.0)
        meshes.append(outer)
        meshes.append(prim.dome([cx, cy, z], radius * 0.86, shell_h * 0.5, materials["roof"]))
    elif shape == "tent":
        meshes.append(prim.tent_dome(shell_center, radius, shell_h, materials["accent"], sides=int(item.get("tent_sides", 12))))
    else:
        outer = prim.dome(shell_center, radius, shell_h, materials["accent"])
        if tile_pattern and tile_pattern != "none":
            tex = tx.get_texture(tile_pattern)
            if tex is not None:
                prim.apply_cylindrical_texture(outer, tex, (cx, cy), repeat_u=6.0, repeat_v=2.0)
        meshes.append(outer)

    finial_h = float(item.get("finial_height_m", 0.0) or 0.0)
    if finial_h > 0:
        apex_z = z + shell_h
        meshes.extend(prim.dome_finial([cx, cy, apex_z], finial_h, radius * 0.16,
                                       materials["accent"], materials["decoration"]))
    return meshes


def _drum_with_windows(center, radius, height, window_count, wall_color, glass_color, accent_color):
    """Cylinder with N tall arched windows cut around its perimeter."""
    cx, cy, cz = center
    drum = prim.cylinder([cx, cy, cz], radius, height, wall_color, sections=48)
    if window_count <= 0 or not _BOOLEAN_OK:
        return [drum]
    cut_boxes = []
    win_h = height * 0.58
    win_z_center = cz + height * 0.18 + win_h / 2.0
    arc_step = math.tau / window_count
    win_arc = arc_step * 0.45
    for i in range(window_count):
        a = i * arc_step
        nx = math.cos(a)
        ny = math.sin(a)
        wx = cx + nx * radius
        wy = cy + ny * radius
        chord = 2 * radius * math.sin(win_arc / 2.0)
        local_angle = a + math.pi / 2.0
        cut = prim.box([wx, wy, win_z_center],
                       [chord, radius * 1.4, win_h], [50, 50, 70, 255], local_angle)
        cut_boxes.append(cut)
    try:
        result = trimesh.boolean.difference([drum] + cut_boxes)
        if result is None or len(result.vertices) == 0:
            return [drum]
        result.visual.face_colors = np.array(wall_color, dtype=np.uint8)
    except Exception:
        return [drum]
    # Glass infill inside each window
    glass = []
    pane_h = win_h * 0.88
    pane_z = cz + height * 0.18 + pane_h / 2.0
    for i in range(window_count):
        a = i * arc_step
        nx = math.cos(a)
        ny = math.sin(a)
        chord = 2 * radius * math.sin(win_arc / 2.0) * 0.78
        gx = cx + nx * radius * 0.95
        gy = cy + ny * radius * 0.95
        glass.append(prim.box([gx, gy, pane_z], [chord, 0.04, pane_h], glass_color, a + math.pi / 2.0))
    # Decorative top band
    band_h = height * 0.10
    band = prim.cylinder([cx, cy, cz + height - band_h], radius * 1.03, band_h, accent_color, sections=48)
    return [result, band, *glass]


def _vault_meshes(param, levels, style, materials):
    meshes = []
    for item in param.get("vaults", []):
        lv = levels.get(item.get("level")) or next(iter(levels.values()))
        base_z = item.get("base_height_m", lv["elevation_m"] + lv["height_m"])
        angle = math.radians(item.get("angle_deg", 0.0))
        meshes.append(prim.barrel_vault(
            [item["center"][0], item["center"][1], base_z],
            item.get("length_m", 6.0),
            item.get("radius_m", 1.5),
            item.get("depth_m", 4.0),
            materials["roof"],
            angle,
        ))
    return meshes


def _arcade_meshes(param, levels, style, materials):
    meshes = []
    for arcade in param.get("arcades", []):
        lv = levels.get(arcade.get("level")) or next(iter(levels.values()))
        start = arcade.get("start", [0, 0])
        end = arcade.get("end", [10, 0])
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        angle = math.atan2(dy, dx)
        bay_w = arcade.get("bay_width_m", 3.0)
        count = max(1, int(length / bay_w))
        height = arcade.get("height_m", lv["height_m"] * 0.85)
        depth = arcade.get("depth_m", 0.35)
        for i in range(count):
            t = (i + 0.5) / count
            center = [start[0] + dx * t, start[1] + dy * t, lv["elevation_m"]]
            meshes.append(prim.pointed_arch_panel(center, bay_w * 0.8, height, depth, materials["frame"], angle))
            meshes.append(prim.pointed_arch_panel([center[0], center[1], center[2] + 0.08], bay_w * 0.58, height * 0.82, depth * 1.1, materials["glass"], angle))
        for t in [0.0] + [(i + 1) / count for i in range(count)]:
            p = [start[0] + dx * t, start[1] + dy * t, lv["elevation_m"]]
            meshes.append(prim.box([p[0], p[1], p[2] + height / 2], [0.22, depth * 1.15, height], materials["column"], angle))
    return meshes


def _decoration_meshes(param, walls, levels, style, materials):
    meshes = []
    for dec in param.get("decorations", []):
        if dec.get("type") != "tile_band":
            continue
        wall = walls.get(dec.get("host_wall") or dec.get("host"))
        if not wall:
            continue
        g = wall["geometry"]
        a, b = g["centerline"]
        length = math.dist(a, b)
        angle = _wall_angle(wall)
        level = levels.get(wall["level"]) or next(iter(levels.values()))
        h = dec.get("height_m", style.get("tile_band_height_m", 0.35))
        z = level["elevation_m"] + dec.get("z_m", g.get("height", level["height_m"]) * 0.75)
        band_mesh = prim.box([a[0] + math.cos(angle) * length / 2,
                              a[1] + math.sin(angle) * length / 2, z],
                             [length, 0.14, h], materials["decoration"], angle)
        pattern = dec.get("pattern", style.get("tile_band_pattern", "kashi_lotus"))
        if pattern and pattern != "none":
            tex = tx.get_texture(pattern)
            if tex is not None:
                prim.apply_planar_texture(band_mesh, tex, axis="xz",
                                          repeat_u=max(2, int(length / 1.2)), repeat_v=1)
        meshes.append(band_mesh)
    return meshes


def _local_facade_box(base, angle, width, depth, height, color):
    return prim.box([base[0], base[1], base[2] + height / 2], [width, depth, height], color, angle)


def _host_angle(elem, walls):
    host = walls.get(elem["geometry"].get("host_id"))
    return _wall_angle(host) if host else elem["geometry"].get("angle_rad", 0.0)


def _wall_angle(wall):
    a, b = wall["geometry"]["centerline"]
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _bbox(profile):
    xs = [p[0] for p in profile]
    ys = [p[1] for p in profile]
    return min(xs), min(ys), max(xs), max(ys)
