"""Semantic voxel exporter: BIM JSON -> Litematica (.litematic).

Route A (semantic voxelization): reuses the exact per-element meshes that the
visual GLB exporter builds (walls with cut openings, arches, domes, iwans,
fluted columns ...), then voxelizes each mesh on one shared global lattice and
maps its material colour to a Minecraft block via a style-aware palette.

Fidelity is bounded by the voxel pitch. ``blocks_per_meter=4`` (pitch 0.25 m)
keeps 0.2 m walls at ~1 block and preserves window frames and dome/arch
silhouettes; coarser settings trade detail for block count.

Axis mapping: building space is X east / Y north / Z up. Minecraft is Y up, so a
building voxel (x, y, z) is written to region coordinates (x, z, y).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import trimesh
from litemapy import BlockState, Region, Schematic

from ..shared import materials as M
from ..shared.styles import resolve_style
from ..step3_glb import collect_meshes

_MAT_ORDER = list(M.MATERIALS)
_MAT_INDEX = {m: i for i, m in enumerate(_MAT_ORDER)}
# Overlap priority by structural class (higher wins the shared voxel).
_CLASS_PRIO = {"opening": 1, "service": 1, "decoration": 2, "envelope": 2}


# Material role -> Minecraft block id, per style family. Roles match the keys of
# ``style["materials"]`` plus a few literal colours used inside glb_exporter.
_BLOCKS = {
    "modern": {
        "wall": "minecraft:light_gray_concrete",
        "column": "minecraft:white_concrete",
        "slab": "minecraft:smooth_stone",
        "roof": "minecraft:gray_concrete",
        "frame": "minecraft:gray_concrete",
        "glass": "minecraft:light_blue_stained_glass",
        "accent": "minecraft:cyan_concrete",
        "door": "minecraft:iron_block",
        "decoration": "minecraft:gray_concrete",
        "loadbearing": "minecraft:gray_concrete",
        "stair": "minecraft:smooth_stone",
    },
    "persian": {
        "wall": "minecraft:smooth_sandstone",
        "column": "minecraft:smooth_sandstone",
        "slab": "minecraft:cut_sandstone",
        "roof": "minecraft:cut_sandstone",
        "frame": "minecraft:smooth_sandstone",
        "glass": "minecraft:light_blue_stained_glass",
        "accent": "minecraft:cyan_glazed_terracotta",
        "door": "minecraft:dark_oak_planks",
        "decoration": "minecraft:cyan_glazed_terracotta",
        "loadbearing": "minecraft:stone_bricks",
        "stair": "minecraft:smooth_stone",
    },
    "classical": {
        "wall": "minecraft:smooth_quartz",
        "column": "minecraft:quartz_pillar",
        "slab": "minecraft:smooth_quartz",
        "roof": "minecraft:stone_bricks",
        "frame": "minecraft:quartz_block",
        "glass": "minecraft:white_stained_glass",
        "accent": "minecraft:chiseled_quartz_block",
        "door": "minecraft:stripped_oak_log",
        "decoration": "minecraft:chiseled_quartz_block",
        "loadbearing": "minecraft:stone_bricks",
        "stair": "minecraft:smooth_quartz",
    },
}
_BLOCKS["islamic"] = _BLOCKS["persian"]

# Literal colours emitted by glb_exporter that are not in style["materials"].
_LITERAL_ROLES = {
    (180, 180, 200, 255): ("mep", "minecraft:light_gray_concrete"),
    (140, 100, 75, 255): ("mep", "minecraft:cut_copper"),
    (240, 230, 180, 255): ("light", "minecraft:glowstone"),
    (110, 110, 110, 255): ("footing", "minecraft:gray_concrete"),
}

# Lower priority blocks never overwrite higher ones in a shared voxel.
_PRIORITY = {"glass": 1, "mep": 1, "light": 1}

# Representative display RGB per block id, for the browser voxel preview.
BLOCK_DISPLAY_RGB = {
    "minecraft:light_gray_concrete": (158, 158, 151),
    "minecraft:white_concrete": (207, 213, 214),
    "minecraft:gray_concrete": (54, 57, 61),
    "minecraft:cyan_concrete": (21, 119, 136),
    "minecraft:smooth_stone": (158, 158, 158),
    "minecraft:stone_bricks": (122, 122, 122),
    "minecraft:iron_block": (220, 222, 224),
    "minecraft:cut_copper": (192, 107, 79),
    "minecraft:glowstone": (203, 171, 95),
    "minecraft:light_blue_stained_glass": (101, 165, 222),
    "minecraft:white_stained_glass": (230, 235, 236),
    "minecraft:smooth_sandstone": (220, 206, 158),
    "minecraft:cut_sandstone": (216, 202, 152),
    "minecraft:cyan_glazed_terracotta": (32, 140, 156),
    "minecraft:cyan_terracotta": (26, 96, 108),
    "minecraft:orange_terracotta": (162, 84, 38),
    "minecraft:light_gray_stained_glass": (205, 203, 196),
    "minecraft:dark_oak_planks": (66, 43, 20),
    "minecraft:smooth_quartz": (236, 233, 226),
    "minecraft:quartz_pillar": (236, 233, 226),
    "minecraft:quartz_block": (236, 233, 226),
    "minecraft:chiseled_quartz_block": (231, 227, 219),
    "minecraft:stripped_oak_log": (193, 150, 90),
    "minecraft:bricks": (150, 84, 66),
    # 非建筑场景元素(树/车/地形),对齐 materials.py 新增材料 + 前端 litematic.ts
    "minecraft:oak_leaves": (74, 110, 54),
    "minecraft:sand": (214, 197, 145),
    "minecraft:dirt": (120, 92, 62),
    "minecraft:black_concrete": (40, 42, 46),
}
_DEFAULT_RGB = (150, 150, 150)


_VOXEL_CACHE: dict[str, tuple] = {}


def litematic_to_voxels(path: str | Path) -> dict:
    """Parse a .litematic into a compact voxel payload for the browser viewer.

    Returns {available, dims:[w,h,l], palette:[hex...], palette_ids:[block_id...],
    blocks:[x,y,z,idx,...]} in Minecraft region coordinates (x east, y up, z).
    Air is omitted. palette_ids[i] is the minecraft block id for palette index i
    (same index space as palette), used for material-aware blast resistance.

    Result is cached per (path, mtime) so repeated calls (voxel fetch, blast,
    export) on the same file are instant.
    """
    key = str(path)
    try:
        mtime = os.path.getmtime(key)
    except OSError:
        mtime = None
    cached = _VOXEL_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    result = _parse_litematic(key)
    _VOXEL_CACHE[key] = (mtime, result)
    return result


def _parse_litematic(path: str) -> dict:
    schem = Schematic.load(path)
    region = next(iter(schem.regions.values()))
    dims = [abs(int(region.width)), abs(int(region.height)), abs(int(region.length))]
    try:
        # Fast path: read litemapy's internal palette-index array directly. The
        # array is (x, y, z) and np.argwhere yields x->y->z order, identical to
        # region.block_positions(), so downstream alignment is preserved.
        blk = np.asarray(region._Region__blocks)
        pal = region._Region__palette
        palette_ids = [b.id for b in pal]
        air = np.array([i for i, b in enumerate(pal) if b.id == "minecraft:air"], dtype=blk.dtype)
        mask = ~np.isin(blk, air) if len(air) else np.ones(blk.shape, bool)
        coords = np.argwhere(mask)
        pidx = blk[mask].astype(np.int64)
        flat = np.empty((len(coords), 4), dtype=np.int64)
        flat[:, :3] = coords
        flat[:, 3] = pidx
        blocks = flat.reshape(-1).tolist()
        palette_hex = ["#%02x%02x%02x" % BLOCK_DISPLAY_RGB.get(b, _DEFAULT_RGB) for b in palette_ids]
        return {
            "available": True, "dims": dims, "palette": palette_hex,
            "palette_ids": palette_ids, "blocks": blocks, "count": len(coords),
        }
    except Exception:
        pass  # fall back to the portable per-cell scan
    palette_idx: dict[str, int] = {}
    palette_hex, palette_ids, blocks = [], [], []
    for x, y, z in region.block_positions():
        bid = region[x, y, z].id
        if bid == "minecraft:air":
            continue
        idx = palette_idx.get(bid)
        if idx is None:
            idx = palette_idx[bid] = len(palette_hex)
            r, g, b = BLOCK_DISPLAY_RGB.get(bid, _DEFAULT_RGB)
            palette_hex.append(f"#{r:02x}{g:02x}{b:02x}")
            palette_ids.append(bid)
        blocks.extend((int(x), int(y), int(z), idx))
    return {
        "available": True, "dims": dims, "palette": palette_hex,
        "palette_ids": palette_ids, "blocks": blocks, "count": len(blocks) // 4,
    }


def _family(preset: str) -> str:
    return preset if preset in _BLOCKS else "modern"


def _palette(param: dict) -> tuple[dict, dict]:
    """Return (rgba_tuple -> (role, block_id)) and the resolved family blocks."""
    style = resolve_style(param)
    family = _family(style.get("preset", "modern"))
    blocks = _BLOCKS[family]
    materials = style["materials"]
    color_role: dict[tuple, tuple] = {}
    for role, rgba in materials.items():
        block = blocks.get(role, blocks["wall"])
        color_role[tuple(int(c) for c in rgba)] = (role, block)
    color_role.update(_LITERAL_ROLES)
    return color_role, blocks


def _mesh_color(mesh: trimesh.Trimesh) -> tuple | None:
    fc = getattr(mesh.visual, "face_colors", None)
    if fc is None or len(fc) == 0:
        return None
    vals, counts = np.unique(np.asarray(fc), axis=0, return_counts=True)
    row = vals[int(counts.argmax())]
    if len(row) == 3:
        row = np.append(row, 255)
    return tuple(int(c) for c in row[:4])


def _resolve_block(rgba, color_role, default_block) -> tuple:
    if rgba is None:
        return ("wall", default_block)
    if rgba in color_role:
        return color_role[rgba]
    # Nearest known colour by RGB distance.
    target = np.array(rgba[:3], dtype=float)
    best, best_d = None, 1e18
    for known, role_block in color_role.items():
        d = float(np.sum((np.array(known[:3], dtype=float) - target) ** 2))
        if d < best_d:
            best, best_d = role_block, d
    return best or ("wall", default_block)


def _voxel_indices(mesh: trimesh.Trimesh, pitch: float, origin: np.ndarray) -> np.ndarray:
    try:
        vg = mesh.voxelized(pitch=pitch)
        pts = vg.points
        try:
            filled = vg.fill()
            if len(filled.points) >= len(pts):
                pts = filled.points
        except Exception:
            pass
    except Exception:
        # 大平整长方体(如 terrain 沙地 216×138m)会让 trimesh voxelize_subdivide 报 "max_iter exceeded"。
        # 退回 AABB 解析填充:仅当 mesh 近似实心长方体(volume≈bbox)才安全,直接按 bbox 网格铺体素中心 —
        # 否则 terrain 被 skip,网格模型(litematic)就没有地面(实景 GLB 不体素化故不受影响)。
        try:
            bmin, bmax = mesh.bounds
            ext = np.asarray(bmax) - np.asarray(bmin)
            bbox_vol = float(np.prod(ext))
            if bbox_vol <= 0 or abs(float(mesh.volume)) < 0.9 * bbox_vol:
                return np.empty((0, 3), dtype=np.int64)
            xs = np.arange(bmin[0] + pitch / 2, bmax[0], pitch)
            ys = np.arange(bmin[1] + pitch / 2, bmax[1], pitch)
            zs = np.arange(bmin[2] + pitch / 2, bmax[2], pitch)
            if xs.size == 0 or ys.size == 0 or zs.size == 0:
                return np.empty((0, 3), dtype=np.int64)
            gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
            pts = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
        except Exception:
            return np.empty((0, 3), dtype=np.int64)
    if len(pts) == 0:
        return np.empty((0, 3), dtype=np.int64)
    return np.round((np.asarray(pts) - origin) / pitch).astype(np.int64)


def build_litematic(param: dict, bim: dict, litematic_path: str | Path,
                    blocks_per_meter: float = 4.0,
                    name: str | None = None) -> dict:
    pitch = 1.0 / float(blocks_per_meter)
    meshes, kinds, _ = collect_meshes(param, bim)
    # Ships tag their sea plane class "glb_only": show it in the GLB but NEVER voxelize it
    # (a block ocean is wasteful and not a strike target). Drop it BEFORE gmin so the huge
    # water plane can't distort the voxel grid origin/extent either.
    keep = [i for i, k in enumerate(kinds) if not (k and k[0] == "glb_only")]
    if len(keep) != len(meshes):
        meshes = [meshes[i] for i in keep]
        kinds = [kinds[i] for i in keep]
    default_block = M.MATERIALS[M.DEFAULT_MATERIAL]["block"]
    default_cls = M.CLASS_INDEX["other"]

    gmin = np.min([m.bounds[0] for m in meshes], axis=0)
    origin = np.floor(gmin / pitch) * pitch

    # (i, j, k) -> (priority, block_id, class_idx, material_idx, element_idx, rgb). Material
    # drives the block; structural class drives the engineering classification; element_idx
    # links the voxel back to its source BIM member (-1 = none/feature); rgb is the real
    # display colour taken from the source mesh's face colour (see below).
    grid: dict[tuple, tuple] = {}
    element_order: list[str] = []     # element_idx -> BIM element id
    element_index: dict[str, int] = {}
    skipped = 0
    for mi, mesh in enumerate(meshes):
        idx = _voxel_indices(mesh, pitch, origin)
        if len(idx) == 0:
            skipped += 1
            continue
        cls, mat, elem_id = kinds[mi] if mi < len(kinds) else ("other", M.DEFAULT_MATERIAL, None)
        mdef = M.MATERIALS.get(mat, M.MATERIALS[M.DEFAULT_MATERIAL])
        block = mdef["block"]
        cls_idx = M.CLASS_INDEX.get(cls, default_cls)
        mat_idx = _MAT_INDEX.get(mat, _MAT_INDEX[M.DEFAULT_MATERIAL])
        # Display colour: reuse the visual mesh's ACTUAL face colour (the rich GLB palette
        # — teal domes, blue water/glass, terracotta roofs …) instead of the coarse
        # material→block colour, so the voxel view isn't monotonous. Falls back to the
        # material's own rgb when a mesh carries no face colours. Alpha is dropped (voxels
        # are opaque), so translucent glass shows as its blue rather than a washed-out grey.
        mc = _mesh_color(mesh)
        rgb = tuple(int(c) for c in (mc[:3] if mc else mdef["rgb"]))
        if elem_id is None:
            elem_idx = -1
        else:
            elem_idx = element_index.get(elem_id)
            if elem_idx is None:
                elem_idx = element_index[elem_id] = len(element_order)
                element_order.append(elem_id)
        prio = _CLASS_PRIO.get(cls, 3)
        for i, j, k in idx:
            key = (int(i), int(j), int(k))
            prev = grid.get(key)
            if prev is None or prio > prev[0]:
                grid[key] = (prio, block, cls_idx, mat_idx, elem_idx, rgb)

    if not grid:
        raise RuntimeError("voxelization produced no blocks")

    keys = np.array(list(grid.keys()))
    dims = keys.max(axis=0) + 1  # building x, y, z spans (z = up)
    wx, wy, wz = int(dims[0]), int(dims[1]), int(dims[2])

    # Region: Minecraft Y is up, so width=x, height=z(up), length=y.
    region = Region(0, 0, 0, wx, wz, wy)
    states: dict[str, BlockState] = {}
    per_block: dict[str, int] = {}
    for (i, j, k), (_, block, _ci, _mi, _ei, _rgb) in grid.items():
        bs = states.get(block)
        if bs is None:
            bs = states[block] = BlockState(block)
        region[i, k, (wy - 1) - j] = bs  # (x, y=up=z, z=mirror(building-y)) — 翻转深度轴,使网格朝向与 GLB(-90°X)一致
        per_block[block] = per_block.get(block, 0) + 1

    schem_name = name or param.get("project", {}).get("name", "stand_trans")
    Schematic(name=schem_name, author="stand_trans",
              description=f"Voxelized at {blocks_per_meter:g} blocks/m (pitch {pitch:g} m)",
              regions={"main": region}).save(str(Path(litematic_path)))
    out = Path(litematic_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Collapse-consequence criticality: per (x,y) column count the solid voxels
    # ABOVE each cell (k = up axis); primary-vertical structure carrying the most
    # above it is the highest-value target. Normalised to 0-255.
    solid = np.zeros((wx, wy, wz), dtype=bool)
    solid[keys[:, 0], keys[:, 1], keys[:, 2]] = True
    cum = np.cumsum(solid, axis=2)
    above = (cum[:, :, -1:] - cum)           # count with k' > k
    pv = M.CLASS_INDEX["primary_vertical"]
    # Normalise by the most-loaded vertical-structure voxel (a real column/wall),
    # NOT the global tallest column (dome/finial) which carries no support below —
    # otherwise every column stays low (blue/green) and red/yellow never appears.
    amax = 1
    for (gi, gj, gk), gcell in grid.items():
        if gcell[2] == pv:
            a = int(above[gi, gj, gk])
            if a > amax:
                amax = a

    # Per-voxel sidecar aligned to litematic_to_voxels() order. 深度轴已翻转(见 region 写盘):
    # 读到的 MC (x, y, z) 映射回 grid key (i, j, k) = (x, (wy-1)-z, y)。
    try:
        vx = litematic_to_voxels(out)
        blk = vx["blocks"]
        classes, materials_idx, crit, element_ids = [], [], [], []
        # Per-voxel real display colour (the GLB mesh face colour recorded in `grid`),
        # deduped into a compact palette + per-voxel index, ALIGNED to this same voxel
        # order. pipeline_runner injects these into voxels.json so the browser voxel view
        # shows the rich ~20-colour GLB palette instead of the ~9 monotonous block colours.
        default_rgb = tuple(int(c) for c in M.MATERIALS[M.DEFAULT_MATERIAL]["rgb"])
        color_palette: list[str] = []
        color_index: dict[str, int] = {}
        colors: list[int] = []
        for n in range(vx["count"]):
            mx, my, mz = blk[n * 4], blk[n * 4 + 1], blk[n * 4 + 2]
            cell = grid.get((mx, (wy - 1) - mz, my))
            ci = cell[2] if cell else default_cls
            mi2 = cell[3] if cell else _MAT_INDEX[M.DEFAULT_MATERIAL]
            ei = cell[4] if cell else -1
            rgb = cell[5] if cell else default_rgb
            classes.append(ci)
            materials_idx.append(mi2)
            element_ids.append(ei)
            chex = "#%02x%02x%02x" % (rgb[0], rgb[1], rgb[2])
            cidx = color_index.get(chex)
            if cidx is None:
                cidx = color_index[chex] = len(color_palette)
                color_palette.append(chex)
            colors.append(cidx)
            c = int(255 * above[mx, (wy - 1) - mz, my] / amax) if ci == pv else 0
            crit.append(c)

        # Criticality v2: replace the count-above heuristic with redundancy-aware,
        # element-removal collapse consequence (per BIM member, broadcast to its
        # voxels). Falls back to the count-above `crit` above if the structural graph
        # can't be built.
        crit_qa = None
        top_aimpoints = None
        try:
            from ..shared import structure as _structure
            counts: dict[str, int] = {}
            for ei in element_ids:
                if ei >= 0:
                    counts[element_order[ei]] = counts.get(element_order[ei], 0) + 1
            graph = _structure.build_support_graph(bim)
            elem_crit = _structure.criticality_v2(graph, counts)
            crit = [elem_crit.get(element_order[ei], 0) if ei >= 0 else 0 for ei in element_ids]
            crit_qa = graph["qa"]

            # Strike ranking: per member, the shallowest voxel (least drill depth) and
            # its depth = solid cells above it; combine criticality x reachability.
            n_elem = len(element_order)
            best_depth = [1 << 30] * n_elem
            aim_vox: list = [None] * n_elem
            for nn in range(vx["count"]):
                ei = element_ids[nn]
                if ei < 0:
                    continue
                mx, my, mz = blk[nn * 4], blk[nn * 4 + 1], blk[nn * 4 + 2]
                d = int(above[mx, (wy - 1) - mz, my])
                if d < best_depth[ei]:
                    best_depth[ei] = d
                    aim_vox[ei] = [int(mx), int(my), int(mz)]
            member_depth = {element_order[i]: best_depth[i] for i in range(n_elem) if aim_vox[i]}
            member_aim = {element_order[i]: aim_vox[i] for i in range(n_elem) if aim_vox[i]}
            top_aimpoints = _structure.top_aimpoints(
                graph, elem_crit, counts, member_depth, member_aim,
                blocks_per_meter=blocks_per_meter)
        except Exception:
            pass

        # Per-member engineering params (class/material/grade/dimensions/wall thickness/
        # rebar/配筋率/fc/capacity), keyed by BIM id to match element_table — drives the
        # viewer's structure-readout panel. Own try/except so it degrades to {} on error.
        members = {}
        try:
            from ..shared import structure as _structure
            members = _structure.all_member_params(bim)
        except Exception:
            members = {}

        # Room function annotations (地下指挥中心/涉密会议室等),供查看器 HUD 标注。
        # polygon 形心 + 楼层中部标高 → 前端体素系 [i, k, (wy-1)-j](与 litematic_to_voxels 一致),
        # 故 palace 布局平移后标注自动跟随。Own try/except,失败降级为空列表。
        room_annotations = []
        try:
            lvl_elev = {lv.get("name"): float(lv.get("elevation_m", 0.0)) for lv in param.get("levels", [])}
            lvl_h = {lv.get("name"): float(lv.get("height_m", 4.0)) for lv in param.get("levels", [])}
            for rm in param.get("rooms", []):
                poly = rm.get("polygon") or []
                if not poly:
                    continue
                cx_m = sum(p[0] for p in poly) / len(poly)
                cy_m = sum(p[1] for p in poly) / len(poly)
                lv = rm.get("level")
                z_m = lvl_elev.get(lv, 0.0) + lvl_h.get(lv, 4.0) * 0.5   # 房间中部标高
                ii = int(round((cx_m - origin[0]) / pitch))
                jj = int(round((cy_m - origin[1]) / pitch))
                kk = int(round((z_m - origin[2]) / pitch))
                room_annotations.append({
                    "id": rm.get("id"), "level": lv,
                    "name": rm.get("name"), "function": rm.get("function"),
                    "center_voxel": [ii, kk, (wy - 1) - jj],
                })
        except Exception:
            room_annotations = []

        sidecar = out.parent / (out.stem + ".voxelclass.json")
        sidecar.write_text(json.dumps({
            "version": 3,
            "classes": classes,
            "class_legend": [{"key": c, "name": M.CLASS_INFO[c][0], "color": M.CLASS_INFO[c][1]}
                             for c in M.STRUCT_CLASSES],
            # Per-voxel real display colours (from the GLB mesh face colours) + palette,
            # aligned to voxels.json order; pipeline_runner injects these into voxels.json.
            "color_palette": color_palette,
            "colors": colors,
            "materials": materials_idx,
            "material_legend": [{"id": m, "name": M.MATERIALS[m]["name"],
                                 "color": "#%02x%02x%02x" % tuple(M.MATERIALS[m]["rgb"]),
                                 "density": M.MATERIALS[m]["density"],
                                 "fc_MPa": M.MATERIALS[m]["fc_MPa"],
                                 "blast_kPa": M.MATERIALS[m]["blast_kPa"]} for m in _MAT_ORDER],
            "criticality": crit,
            # Per-voxel link to source BIM member: element_ids[n] indexes element_table,
            # or -1 for none/non-structural feature. Lets the blast/structure engine
            # aggregate voxel damage to members and project member failure back to voxels.
            "element_ids": element_ids,
            "element_table": element_order,   # element_idx -> BIM element id
            "members": members,               # member_id -> engineering params (v3)
            "criticality_qa": crit_qa,        # support-graph QA (orphans, edges) or null
            "top_aimpoints": top_aimpoints,   # ranked high-value strike points or null
            "room_annotations": room_annotations,  # 房间功能标注(中文 name/function + center_voxel),供查看器 HUD
            # Voxel row (region Y) of building elevation 0 = ground/1F floor, so the
            # viewer can sit the grid at ground level and drop basements below it.
            "ground_y": int(round(-float(origin[2]) / pitch)),
        }), encoding="utf-8")
    except Exception:
        pass

    return {
        "litematic": True,
        "blocks_per_meter": float(blocks_per_meter),
        "pitch_m": round(pitch, 4),
        "block_count": int(sum(per_block.values())),
        "grid_x_y_z": [wx, wy, wz],
        "blocks_per_type": dict(sorted(per_block.items(), key=lambda kv: -kv[1])),
        "meshes_in": len(meshes),
        "meshes_skipped": skipped,
        "origin_m": [round(float(v), 3) for v in origin],
    }
