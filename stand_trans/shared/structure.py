"""Structural support graph + progressive-collapse (alternate-load-path) model.

This is the "response" half of the engine. The blast model (`blast.py`) decides how
much *load* each voxel takes; this module decides how the *structure* responds —
which members fail, and whether their failure cascades (disproportionate collapse).

Design choices (see plans/async-stargazing-gem.md):
  * Graph is at the BIM-MEMBER level (hundreds of nodes), not voxels (~290k). The
    cascade therefore runs in microseconds-to-low-ms and stays interactive.
  * Support edges are INFERRED from independent geometry primitives (the BIM has no
    explicit support links) by vertical adjacency + horizontal footprint overlap —
    the same proximity-inference style already used by to_bim `_derive_beams`/
    `_derive_footings`.
  * Pure numpy/python, no I/O (mirrors blast.py). Callers pass in voxel counts.

Coordinate system: BIM metres, Z-up building space (matches bim.json / to_bim).
"""
from __future__ import annotations

import math

from . import materials as M

# Classes that participate in the load path (everything else is dead load only).
STRUCTURAL = {"foundation", "primary_vertical", "primary_horizontal", "floor", "roof"}

# 轴压承载力仅对受压构件有意义(柱/承重墙/基础);梁/板/屋顶受弯,fc×面积不代表承载力 → None。
_COMPRESSION = {"foundation", "primary_vertical"}
_FY_MPA = 400.0   # 主筋屈服强度(HRB400),用于受压构件含配筋的轴压承载力

# Which member classes may support a member of a given class (load flows down into
# these). A column rests on a column/footing, never on the floor it carries; a slab
# rests on columns/walls/beams. Keeps inference from creating upward "support".
_ALLOWED_SUPPORTERS = {
    "primary_vertical":   {"foundation", "primary_vertical"},
    "foundation":         set(),                                  # grounded roots
    "primary_horizontal": {"foundation", "primary_vertical"},
    "floor":              {"foundation", "primary_vertical", "primary_horizontal"},
    "roof":               {"foundation", "primary_vertical", "primary_horizontal"},
}

_Z_TOL = 0.4          # vertical adjacency tolerance (m) for "S top meets M bottom"
_XY_TOL = 0.30        # horizontal footprint overlap slack (m)
_G = 9.81             # gravity (m/s^2)
_MAX_ITERS = 200      # cascade fixed-point safety cap
_DESTROY = 0.85       # member damage >= this -> blast-failed (lost its section)


# --------------------------------------------------------------------------- #
# Geometry helpers — per-element footprint bbox (xy) and z-extent.
# --------------------------------------------------------------------------- #
def _bbox_of_points(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _member_geometry(elem: dict, level: dict):
    """Return (bbox_xy, base_z, top_z, connect_z) in building metres, or None.

    connect_z = the z plane where the member meets its supporters below:
      beams hang at the top of their level (columns rise to the beam top), so they
      connect at top_z; everything else connects at its base.
    """
    g = elem.get("geometry", {})
    typ = elem.get("type")
    e = float(level["elevation_m"])
    lh = float(level.get("height_m", 3.5))

    if typ == "Wall":
        a, b = g["centerline"]
        th = float(g.get("thickness", 0.24))
        bb = _bbox_of_points([a, b])
        bb = (bb[0] - th / 2, bb[1] - th / 2, bb[2] + th / 2, bb[3] + th / 2)
        h = float(g.get("height", lh))
        return bb, e, e + h, e
    if typ == "Column":
        cx, cy = g["center"]
        if g.get("shape") == "circle":
            r = float(g.get("radius", 0.25))
            half = (r, r)
        else:
            sz = g.get("size", [0.45, 0.45])
            half = (sz[0] / 2, sz[1] / 2)
        bb = (cx - half[0], cy - half[1], cx + half[0], cy + half[1])
        h = float(g.get("height", lh))
        return bb, e, e + h, e
    if typ == "Beam":
        s, t = g["start"], g["end"]
        w = float(g.get("width", 0.4))
        bb = _bbox_of_points([s, t])
        bb = (bb[0] - w / 2, bb[1] - w / 2, bb[2] + w / 2, bb[3] + w / 2)
        bh = float(g.get("height", 0.6))
        top = e + lh
        return bb, top - bh, top, top              # connect at top (column tops)
    if typ in ("Slab",):
        bb = _bbox_of_points(g["profile"])
        h = float(g.get("height", 0.18))
        return bb, e, e + h, e
    if typ == "Roof":
        bb = _bbox_of_points(g["profile"])
        h = float(g.get("height", 0.22))
        base = e + lh                              # roof sits atop its level
        return bb, base, base + h, base
    if typ == "Footing":
        cx, cy = g["center"]
        sz = g.get("size", [1.2, 1.2])
        bb = (cx - sz[0] / 2, cy - sz[1] / 2, cx + sz[0] / 2, cy + sz[1] / 2)
        th = float(g.get("thickness", 0.6))
        top = float(g.get("top_z", e))
        return bb, top - th, top, top - th
    if typ == "Stair":
        x0, y0, x1, y1 = g["bbox"]
        h = float(g.get("height", lh))
        return (x0, y0, x1, y1), e, e + h, e
    return None


def _bbox_overlap(a, b, tol=_XY_TOL):
    return not (a[2] + tol < b[0] or b[2] + tol < a[0] or
                a[3] + tol < b[1] or b[3] + tol < a[1])


def _section_area(elem: dict) -> float:
    """Cross-sectional area (m^2) resisting load — for capacity + self weight."""
    g = elem.get("geometry", {})
    typ = elem.get("type")
    if typ == "Column":
        if g.get("shape") == "circle":
            return math.pi * float(g.get("radius", 0.25)) ** 2
        sz = g.get("size", [0.45, 0.45])
        return float(sz[0]) * float(sz[1])
    if typ == "Wall":
        a, b = g["centerline"]
        return float(g.get("thickness", 0.24)) * math.dist(a, b)
    if typ == "Beam":
        return float(g.get("width", 0.4)) * float(g.get("height", 0.6))
    if typ in ("Slab", "Roof"):
        bb = _bbox_of_points(g["profile"])
        return (bb[2] - bb[0]) * (bb[3] - bb[1])
    if typ == "Footing":
        sz = g.get("size", [1.2, 1.2])
        return float(sz[0]) * float(sz[1])
    if typ == "Stair":
        x0, y0, x1, y1 = g["bbox"]
        return (x1 - x0) * (y1 - y0)
    if typ == "Tree":
        return math.pi * float(g.get("canopy_radius", 2.5)) ** 2
    if typ == "Vehicle":
        return float(g.get("length", 4.5)) * float(g.get("width", 2.0))
    if typ == "Terrain":
        bb = _bbox_of_points(g["profile"])
        return (bb[2] - bb[0]) * (bb[3] - bb[1])
    return 0.25


def _axial_capacity_N(cls: str, fc_MPa: float, area_m2: float, rho_percent: float) -> float | None:
    """受压构件(柱/承重墙/基础)轴压承载力 N = 0.85·fc·Ac + fy·As(砌体 ρ=0 → 退化为 0.85·fc·A)。
    受弯/受剪构件(梁/板/屋顶)的"轴压×面积"无工程意义 → None(前端显 '—')。"""
    if cls not in _COMPRESSION:
        return None
    rho = max(0.0, float(rho_percent)) / 100.0          # 配筋率 % → 比值
    return round((0.85 * fc_MPa + _FY_MPA * rho) * area_m2 * 1e6, 1)


# --------------------------------------------------------------------------- #
# Per-member engineering parameters (for the viewer's structure-readout panel)
# --------------------------------------------------------------------------- #
def member_params(elem: dict, level: dict, preset: str = "modern") -> dict:
    """Engineering parameters of a single BIM element for display + the members sidecar:
    class, material, concrete grade, dimensions (incl. wall thickness), reinforcement
    (rebar dia/spacing, stirrups, cover, 配筋率), fc and axial capacity. Pure data."""
    cls = M.element_class(elem)
    material = M.element_material(elem, preset)
    props = M.MATERIALS.get(material, M.MATERIALS[M.DEFAULT_MATERIAL])
    fc = float(props["fc_MPa"])
    g = elem.get("geometry", {})
    typ = elem.get("type")
    lh = float(level.get("height_m", 3.5))
    dims: dict = {"kind": (typ or "other").lower()}
    if typ == "Wall":
        a, b = g.get("centerline", [[0, 0], [0, 0]])
        dims.update(thickness_m=float(g.get("thickness", 0.24)),
                    length_m=round(math.dist(a, b), 3),
                    height_m=float(g.get("height", lh)))
    elif typ == "Column":
        if g.get("shape") == "circle":
            dims.update(shape="circle", radius_m=float(g.get("radius", 0.25)))
        else:
            dims.update(shape="rect", size_m=[float(s) for s in g.get("size", [0.45, 0.45])])
        dims.update(height_m=float(g.get("height", lh)))
    elif typ == "Beam":
        s, t = g.get("start", [0, 0]), g.get("end", [0, 0])
        dims.update(width_m=float(g.get("width", 0.4)), height_m=float(g.get("height", 0.6)),
                    length_m=round(math.dist(s, t), 3))
    elif typ == "Slab":
        dims.update(thickness_m=float(g.get("height", 0.18)))
    elif typ == "Roof":
        dims.update(thickness_m=float(g.get("height", 0.22)))
    elif typ == "Footing":
        dims.update(size_m=[float(s) for s in g.get("size", [1.2, 1.2])],
                    thickness_m=float(g.get("thickness", 0.6)))
    elif typ == "Tree":
        dims.update(height_m=float(g.get("height", 8.0)),
                    canopy_radius_m=float(g.get("canopy_radius", 2.5)),
                    trunk_radius_m=float(g.get("trunk_radius", 0.3)))
    elif typ == "Vehicle":
        dims.update(length_m=float(g.get("length", 4.5)),
                    width_m=float(g.get("width", 2.0)),
                    height_m=float(g.get("height", 1.6)))
    elif typ == "Terrain":
        dims.update(thickness_m=float(g.get("height", 0.5)))
    area = _section_area(elem)
    dims["section_area_m2"] = round(area, 4)
    reinf = M.reinforcement_for(cls, material, g.get("reinforcement"))
    return {
        "class": cls,
        "material": material,
        "material_name": props.get("name"),
        "concrete_grade": M.concrete_grade(material, fc),
        "fc_MPa": fc,
        "dimensions": dims,
        "rebar": reinf,
        "reinforcement_ratio_percent": reinf.get("reinforcement_ratio_percent", 0.0),
        "capacity_N": _axial_capacity_N(cls, fc, area, reinf.get("reinforcement_ratio_percent", 0.0)),
    }


def all_member_params(bim: dict, preset: str | None = None) -> dict:
    """{element_id: member_params} over ALL elements (incl. non-structural partitions /
    envelope), so the viewer can inspect any picked member. Keyed by BIM element id to
    match the sidecar's element_table."""
    levels = {lv["name"]: lv for lv in bim.get("levels", [])}
    default_level = next(iter(levels.values())) if levels else {"elevation_m": 0.0, "height_m": 3.5}
    preset = preset or (bim.get("project", {}) or {}).get("style", "modern")
    out: dict = {}
    for elem in bim.get("elements", []):
        eid = elem.get("id")
        if not eid:
            continue
        level = levels.get(elem.get("level")) or default_level
        try:
            out[eid] = member_params(elem, level, preset=preset)
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------- #
# Support graph
# --------------------------------------------------------------------------- #
def build_support_graph(bim: dict, preset: str | None = None) -> dict:
    """Infer a member-level support graph from a BIM model.

    Returns a dict with parallel arrays indexed by node:
      ids, index{id->i}, cls, base_z, top_z, connect_z, bbox,
      capacity_N, self_weight_N, supported_by[set], supports[set],
      grounded_root[bool], plus a `qa` report.
    """
    levels = {lv["name"]: lv for lv in bim.get("levels", [])}
    default_level = next(iter(levels.values())) if levels else {"elevation_m": 0.0, "height_m": 3.5}
    preset = preset or (bim.get("project", {}) or {}).get("style", "modern")

    ids, cls, base_z, top_z, connect_z, bbox = [], [], [], [], [], []
    capacity_N, self_weight_N = [], []
    index: dict[str, int] = {}
    for elem in bim.get("elements", []):
        c = M.element_class(elem)
        if c not in STRUCTURAL:
            continue
        level = levels.get(elem.get("level")) or default_level
        geom = _member_geometry(elem, level)
        if geom is None:
            continue
        bb, bz, tz, cz = geom
        mat = M.element_material(elem, preset)
        props = M.MATERIALS.get(mat, M.MATERIALS[M.DEFAULT_MATERIAL])
        area = _section_area(elem)
        height = max(tz - bz, 0.05)
        i = len(ids)
        index[elem["id"]] = i
        ids.append(elem["id"]); cls.append(c)
        base_z.append(bz); top_z.append(tz); connect_z.append(cz); bbox.append(bb)
        # Compressive capacity ~ fc * area; horizontals use flexural proxy fc*area.
        capacity_N.append(float(props["fc_MPa"]) * 1e6 * area)
        self_weight_N.append(float(props["density"]) * area * height * _G)

    n = len(ids)
    supported_by = [set() for _ in range(n)]
    supports = [set() for _ in range(n)]
    grade = min(base_z) if base_z else 0.0
    has_foundation = any(c == "foundation" for c in cls)
    grounded_root = [False] * n
    for i in range(n):
        if cls[i] == "foundation" or (not has_foundation and base_z[i] <= grade + 0.5):
            grounded_root[i] = True

    # Edge inference: S supports M iff
    #   (1) S's class is a legal supporter of M's class (a column rests on a
    #       column/footing — never on the slab/beam at its own floor line),
    #   (2) S's top plane meets M's connection plane (|S.top_z - M.connect_z| <= z_tol),
    #   (3) S genuinely starts BELOW that plane (excludes same-level members), and
    #   (4) their xy footprints overlap.
    eps = 0.05
    for mi in range(n):
        allowed = _ALLOWED_SUPPORTERS.get(cls[mi], {"foundation", "primary_vertical"})
        for si in range(n):
            if si == mi or cls[si] not in allowed:
                continue
            if abs(top_z[si] - connect_z[mi]) > _Z_TOL:
                continue
            if base_z[si] >= connect_z[mi] - eps:
                continue
            if not _bbox_overlap(bbox[si], bbox[mi]):
                continue
            supported_by[mi].add(si)
            supports[si].add(mi)

    # QA: members that are elevated but have neither supporters nor ground footing.
    orphans = [ids[i] for i in range(n)
               if not grounded_root[i] and not supported_by[i] and base_z[i] > grade + 0.5]
    qa = {
        "nodes": n,
        "edges": sum(len(s) for s in supports),
        "grounded_roots": int(sum(grounded_root)),
        "orphans": orphans,
        "orphan_count": len(orphans),
        "has_foundation": has_foundation,
    }
    return {
        "ids": ids, "index": index, "cls": cls,
        "base_z": base_z, "top_z": top_z, "connect_z": connect_z, "bbox": bbox,
        "capacity_N": capacity_N, "self_weight_N": self_weight_N,
        "supported_by": supported_by, "supports": supports,
        "grounded_root": grounded_root, "qa": qa,
    }


# --------------------------------------------------------------------------- #
# Progressive collapse (alternate load path) cascade
# --------------------------------------------------------------------------- #
def _grounded_set(graph: dict, removed: set) -> set:
    """Members with a downward support path to a grounded root, ignoring `removed`."""
    n = len(graph["ids"])
    supported_by = graph["supported_by"]
    grounded_root = graph["grounded_root"]
    grounded = set()
    # iterate to fixed point (a node is grounded if any supporter is grounded/root)
    changed = True
    while changed:
        changed = False
        for i in range(n):
            if i in removed or i in grounded:
                continue
            if grounded_root[i] or any(s in grounded for s in supported_by[i] if s not in removed):
                grounded.add(i); changed = True
    return grounded


def _route_demand(graph: dict, active: set) -> list:
    """Gravity demand (N) carried by each active member: self + everything resting on
    it, pushed down equally among its supporters. Top-down over connect_z."""
    n = len(graph["ids"])
    self_w = graph["self_weight_N"]
    supported_by = graph["supported_by"]
    incoming = [0.0] * n
    order = sorted((i for i in active), key=lambda i: graph["connect_z"][i], reverse=True)
    demand = [0.0] * n
    for i in order:
        total = self_w[i] + incoming[i]
        demand[i] = total
        sups = [s for s in supported_by[i] if s in active]
        if sups:
            share = total / len(sups)
            for s in sups:
                incoming[s] += share
    return demand


def run_collapse(graph: dict, removed_init, member_damage=None) -> dict:
    """Fixed-point cascade. `removed_init` = iterable of node indices removed up front
    (e.g. blast-destroyed); `member_damage` (optional list aligned to nodes) reduces
    residual capacity and removes members at/above _DESTROY.

    Returns {"failed": set, "iters": int, "reason": {idx: 'blast'|'unsupported'|'overload'}}.
    """
    n = len(graph["ids"])
    cap = graph["capacity_N"]
    removed = set(int(i) for i in removed_init)
    reason = {i: "blast" for i in removed}
    if member_damage is not None:
        for i in range(n):
            if member_damage[i] >= _DESTROY and i not in removed:
                removed.add(i); reason[i] = "blast"
    resid = [cap[i] * (1.0 - (member_damage[i] if member_damage is not None else 0.0))
             for i in range(n)]

    iters = 0
    while iters < _MAX_ITERS:
        iters += 1
        active = set(i for i in range(n) if i not in removed)
        grounded = _grounded_set(graph, removed)
        newly = set()
        # (a) anything active but not grounded has lost its load path -> falls.
        for i in active:
            if i not in grounded:
                newly.add(i)
                reason.setdefault(i, "unsupported")
        # (b) overload: demand exceeds residual capacity (verticals/foundation only —
        #     horizontals redistribute rather than crush).
        demand = _route_demand(graph, grounded)
        for i in grounded:
            if graph["cls"][i] in ("primary_vertical", "foundation"):
                if resid[i] > 1e-6 and demand[i] > resid[i]:
                    newly.add(i); reason.setdefault(i, "overload")
        newly -= removed
        if not newly:
            break
        removed |= newly
    return {"failed": removed, "iters": iters, "reason": reason}


# --------------------------------------------------------------------------- #
# Criticality v2 — redundancy-aware, element-removal based.
# --------------------------------------------------------------------------- #
def criticality_v2(graph: dict, voxel_counts: dict | None = None) -> dict:
    """For each structural member, notionally remove it, run the cascade, and score it
    by the total *voxel volume* of all members that fail as a consequence (its own
    voxels + everything it brings down). Redundant members score low; sole supporters
    of large tributary areas score high.

    `voxel_counts`: {element_id: voxel_count}. If None, every member counts as 1.
    Returns {element_id: score_0_255}.
    """
    ids = graph["ids"]
    n = len(ids)

    def vol(i):
        if voxel_counts is None:
            return 1.0
        return float(voxel_counts.get(ids[i], 0)) + 1.0   # +1 so zero-voxel members still rank

    raw = [0.0] * n
    for i in range(n):
        res = run_collapse(graph, [i])
        # INDUCED collapse only: volume of everything that falls *as a consequence*,
        # excluding the struck member's own voxels. Otherwise a big but non-load-bearing
        # member (e.g. a wide roof slab) wins on sheer size despite cascading nothing.
        raw[i] = sum(vol(j) for j in res["failed"] if j != i)

    mx = max(raw) if raw else 0.0
    if mx <= 0:
        # Degenerate (nothing cascades anywhere) -> rank by own tributary size so the
        # map isn't blank.
        raw = [vol(i) for i in range(n)]
        mx = max(raw) if raw and max(raw) > 0 else 1.0
    return {ids[i]: int(round(255.0 * raw[i] / mx)) for i in range(n)}


def induced_collapse(graph: dict, member_idx: int, voxel_counts: dict) -> int:
    """Voxel volume that fails as a consequence of removing one member (excl. itself)."""
    ids = graph["ids"]
    res = run_collapse(graph, [member_idx])
    return int(sum(voxel_counts.get(ids[j], 0) for j in res["failed"] if j != member_idx))


def top_aimpoints(graph: dict, criticality: dict, voxel_counts: dict,
                  member_depth: dict, member_aim: dict, blocks_per_meter: float = 4.0,
                  n_top: int = 8, depth_scale_cells: float = 12.0) -> list:
    """Rank reachable high-value strike points.

    A point's value combines *consequence* (criticality + induced collapse volume) with
    *feasibility* (reachability = how shallow it is to drill to, since a buried member is
    high-consequence but hard to hit). Returns up to ``n_top`` dicts, best first:
        {member_id, class, aim:[x,y,z], azimuth_deg, dive_deg, criticality,
         penetration_cells, reachability, strike_value, expected_collapse_volume}

    `member_depth[id]` = solid cells above the member's shallowest voxel (drill depth);
    `member_aim[id]`   = that voxel [x,y,z] in litematic voxel coords. Members absent
    from these (no reachable voxel) are skipped.
    """
    ids = graph["ids"]
    cls = graph["cls"]
    cls_by_id = {ids[i]: cls[i] for i in range(len(ids))}
    out = []
    for i, eid in enumerate(ids):
        aim = member_aim.get(eid)
        if aim is None:
            continue
        depth = float(member_depth.get(eid, 0))
        crit = float(criticality.get(eid, 0))
        if crit <= 0:
            continue
        reach = 1.0 / (1.0 + depth / max(depth_scale_cells, 1.0))
        sv = crit * reach
        out.append({
            "member_id": eid,
            "class": cls_by_id.get(eid, "other"),
            "aim": aim,
            "azimuth_deg": 0.0,
            "dive_deg": 90.0,                      # straight down onto the shallow point
            "criticality": int(round(crit)),
            "penetration_cells": int(depth),
            "reachability": round(reach, 3),
            "strike_value": round(sv, 1),
            "expected_collapse_volume": induced_collapse(graph, i, voxel_counts),
        })
    out.sort(key=lambda d: d["strike_value"], reverse=True)
    return out[:n_top]


def aggregate_member_damage(damage, element_ids, n_members: int):
    """Mean per-voxel damage over each member's voxels -> member damage[n_members].
    `element_ids[k]` = member index of voxel k (or -1). Used by the blast cascade."""
    import numpy as np
    eid = np.asarray(element_ids)
    dmg = np.asarray(damage, dtype=float)
    out = np.zeros(n_members, dtype=float)
    valid = eid >= 0
    if not valid.any():
        return out
    sums = np.bincount(eid[valid], weights=dmg[valid], minlength=n_members)
    counts = np.bincount(eid[valid], minlength=n_members)
    nz = counts > 0
    out[nz] = sums[nz] / counts[nz]
    return out
