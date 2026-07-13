"""Directional bombing / explosion damage model for the voxel building viewer.

Physics core, pure numpy, no I/O (except :func:`export_damaged`). Operates on the
voxel arrays produced by :func:`stand_trans.litematic.litematic_to_voxels`, in the
same Minecraft Y-up integer voxel space the front-end renders, so the returned
per-voxel ``damage`` array aligns index-for-index with the viewer's InstancedMesh.

The overpressure law is the Kinney & Graham (1985) spherical free-field fit (ported
from the reference ``blast-model.js``). The novel part this adds on top is what makes
the *drop angle* matter: a ballistic ray-cast finds the struck face, penetration
depth depends on incidence angle + yield + material, and damage is then biased by a
forward fragment cone and attenuated by occluding mass between burst and voxel.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

P0_KPA = 101.325  # standard atmospheric pressure

# Overpressure (kPa) at which a material is fully destroyed. Keyed by the minecraft
# block ids emitted in litematic._BLOCKS / _LITERAL_ROLES. Glass is weakest,
# reinforced concrete / iron strongest. tests/test_blast.py asserts full coverage.
BLOCK_RESISTANCE = {
    "minecraft:light_blue_stained_glass": 7.0,
    "minecraft:white_stained_glass": 7.0,
    "minecraft:dark_oak_planks": 20.0,
    "minecraft:stripped_oak_log": 22.0,
    "minecraft:glowstone": 20.0,
    "minecraft:cut_copper": 25.0,
    "minecraft:cyan_glazed_terracotta": 40.0,
    "minecraft:smooth_sandstone": 45.0,
    "minecraft:cut_sandstone": 50.0,
    "minecraft:smooth_quartz": 60.0,
    "minecraft:quartz_block": 60.0,
    "minecraft:chiseled_quartz_block": 60.0,
    "minecraft:quartz_pillar": 70.0,
    "minecraft:stone_bricks": 90.0,
    "minecraft:smooth_stone": 100.0,
    "minecraft:light_gray_concrete": 150.0,
    "minecraft:cyan_concrete": 170.0,
    "minecraft:white_concrete": 160.0,
    "minecraft:gray_concrete": 180.0,
    "minecraft:iron_block": 200.0,
}
# Override/extend with the engineering material library (block -> blast_kPa), so
# material-accurate thresholds (incl. new blocks like bricks) drive the physics.
try:
    from ..shared.materials import block_resistance as _mat_res
    BLOCK_RESISTANCE.update(_mat_res())
except Exception:
    pass
DEFAULT_RESISTANCE = 100.0

# Damage-ring thresholds (kPa) reused from the reference model, for HUD/report.
RING_THRESHOLDS = [200.0, 100.0, 35.0, 20.0, 7.0, 3.5]

# Common ordnance, TNT-equivalent kg.
PRESETS = [
    {"name": "手榴弹 Grenade", "kg": 0.2},
    {"name": "152mm 榴弹", "kg": 8.0},
    {"name": "Mk-82 航空炸弹", "kg": 87.0},
    {"name": "Mk-84 航空炸弹", "kg": 430.0},
    {"name": "战斧巡航导弹", "kg": 450.0},
    {"name": "车载炸弹 IED", "kg": 5000.0},
    {"name": "云爆弹/温压", "kg": 10000.0},
]

# Tunables for the directional model.
_P_INC = 1.5          # incidence-angle exponent: glancing hits carry less penetration energy
_PEN_SURFACE = 0.5    # surface/contact fuze: detonates on the skin (cells)
_PEN_E = 300.0        # penetrator kinetic-energy budget scale: E0 = _PEN_E * W^(1/3) * cos^P_INC
# (70→300:原值下重弹撞主楼实心结构 15m 就耗尽、到不了地下,penLayers 因穿不到上限而从不触发;
#  300 让钻地弹真能贯穿到地下,penLayers 才能按"穿 N 层引爆"控制起爆深度)
# (raised 16→70 for the 1%-yield regime: at W*0.006 the cube-root energy dropped ~5x and even
#  heavy penetrators only scratched concrete; 70 restores a realistic gradient — heavy bombs
#  breach ~1m plain RC, small bombs stop at the skin, reinforced mats stop heavy bombs.)
_AIR_DRAG = 0.2       # energy spent per air cell while coasting through a void/room (cheap)
_PEN_CAP = 170.0      # absolute max penetrator travel (cells) before forced detonation
# (60→170:屋顶到地下B2约 55m=110格,原 60格=30m 卡在地上;170格=85m 够从主楼顶钻到地下深层)
_CONE_HALF_DEG = 35.0  # forward fragment cone half-angle
_CONE_GAIN = 0.6      # forward-cone overpressure boost
_OCC_MU = 0.55        # occlusion attenuation per intervening solid sample(调强:被实心遮挡的体素超压快速衰减,杜绝隔山打牛)
_OCC_FREE = 0.0       # 自身/爆心 cell 已在 occ 计数时排除,这里不再额外扣减
_OCC_SAMPLES = 24     # ray samples for occlusion(提高采样,减少薄墙漏检)
_K_CRUSH = 2.5        # near-burst pulverization radius scale (m/kg^(1/3)) — raised 0.8->2.5 so each
                      # burst pulverizes a much larger sphere; at the 1% yield the old 0.8 only
                      # crushed ~3 cells, so walls/columns a few metres from a burst survived.
_CRUSH_OCC_MAX = 1.5  # crush 无条件全毁仅作用于视线通畅(遮挡采样 ≤ 此)的近场体素;被结构遮挡的不绕过(防隔山打牛)
_OCC_BLOCK = 3.0      # 视线被 ≥ 此数量的实心采样阻断 → 落入"爆炸阴影"(硬遮挡)
_SHADOW_KPA = 90.0    # raised 30->90 so walls in the "blast shadow" of interior structure still see
                      # enough pressure to be damaged (30 kPa < stone 95 left sheltered walls untouched). #爆炸阴影内的超压上限(kPa):仅绕射/传导残余,杜绝大当量近场穿墙
_MEMBER_FAIL_FRAC = 0.25  # member is "severed"/blast-failed when this fraction of it is destroyed
# Tall thin verticals (columns / load-bearing walls) and beams are "severed" by a CONCENTRATED
# local hit, not by losing a quarter of their whole length. Requiring 25% of a full-height column
# to be destroyed left columns/walls 毫发无损 while broad thin slabs (which lose >>25% under one
# overhead burst) vanished — and with no vertical failing, progressive collapse never fired. A
# lower per-class fraction lets a burst adjacent to a column sever it -> cascade -> real collapse.
_MEMBER_FAIL_FRAC_VERTICAL = 0.08
# Base-severance ("炸掉墙脚 -> 墙倒 / 炸掉支撑 -> 上面掉"): a vertical member (column / load-bearing
# wall) topples when its FOOT is destroyed, regardless of the whole-member fraction. A tall column
# or a broad wall loses only a small fraction from one burst — so the fraction test alone left walls
# 毫发无损 — but if the bottom band of its section is gone it has lost its footing and falls,
# which then unsupports (via the cascade) whatever it carried.
_MEMBER_BASE_BAND = 5        # voxels above a vertical member's lowest voxel counted as its "foot"
_MEMBER_BASE_FAIL_FRAC = 0.5  # topple when this fraction of the foot band is destroyed
_VERTICAL_CLASSES = ("primary_vertical", "primary_horizontal")
# Pressure-impulse (P-I) damage criterion. Enabled 2026-06 together with the 1% yield scale
# (blast_runner._YIELD_SCALE=0.006): at 1% yield the peak-pressure-only `P/res` collapses to
# ~zero damage with no munition differentiation, while P-I (which also credits the positive-
# phase impulse) keeps damage visible and graded. _I_CRIT_REF is calibrated for the 1%/bpm6
# regime — see tools-style calibration in the PR; tune in [0.015, 0.03] if materials over/under-survive.
_USE_PI = True        # pressure-impulse damage criterion (calibrated for the 1%-yield regime)
_I_CRIT_REF = 1.0     # impulse-criterion scale: i_crit = res * _I_CRIT_REF (kPa·ms per kPa)
                      # (swept on RC scenes @1% yield/bpm6: 1.0 gives a clean munition gradient —
                      #  grenade ~0%, 152mm few%, Mk-82 ~16%, Mk-84/GBU-57 high; lower over-destroys.)
# Reinforcement → resistance hardening (配筋率 → 抗力). A member's reinforcement ratio (%) raises
# its effective blast resistance; because `res` feeds BOTH the damage criterion AND the penetrator
# energy cost (res_grid), this makes a thick high-ratio RC mat (地下室顶板/基础底板) genuinely hard
# to breach — surface/shallow fuzes stop at it, only a deep penetrator with enough energy gets through.
_REINF_GAIN = 0.30    # mult = 1 + _REINF_GAIN * reinforcement_ratio_percent
# (0.45->0.30: RC columns/walls were hardened to ~277 kPa vs RC floor ~245 — so a burst adjacent to
#  a column barely dented it and it never reached the sever fraction, leaving columns 毫发无损 and
#  blocking local collapse. Trimming the reinforcement edge lets a close hit destroy enough of a
#  column's section to sever it -> the bay it supports pancakes; distant columns still stand.)
_REINF_MULT_CAP = 4.0  # upper clamp on the reinforcement resistance multiplier
_DESTROY = 0.85       # damage >= this -> block removed
_DAMAGE = 0.30        # damage >= this -> block visibly damaged
_FALL_CAP = 40        # max cells a surviving block collapses downward
_OCC_CHUNK = 8192     # bounded working set for occlusion sampling


# --------------------------------------------------------------------------- #
# Kinney-Graham overpressure (vectorized)
# --------------------------------------------------------------------------- #
def overpressure_ratio(Z):
    """Peak overpressure ratio Pso/P0 for scaled distance Z (m/kg^(1/3))."""
    Z = np.maximum(np.asarray(Z, dtype=float), 1e-6)
    num = 1.0 + (Z / 4.5) ** 2
    d1 = np.sqrt(1.0 + (Z / 0.048) ** 2)
    d2 = np.sqrt(1.0 + (Z / 0.32) ** 2)
    d3 = np.sqrt(1.0 + (Z / 1.35) ** 2)
    return 808.0 * num / (d1 * d2 * d3)


def overpressure_kpa(Z):
    return overpressure_ratio(Z) * P0_KPA


def scaled_distance(R, W_kg):
    return np.asarray(R, dtype=float) / (W_kg ** (1.0 / 3.0))


def scaled_duration(Z):
    """Scaled positive-phase duration t_d/W^(1/3) [ms/kg^(1/3)] — Kinney & Graham (1985).
    Rises with distance (far blasts have longer, gentler pulses)."""
    Z = np.maximum(np.asarray(Z, dtype=float), 1e-6)
    num = 1.0 + (Z / 0.54) ** 10
    d1 = 1.0 + (Z / 0.02) ** 3
    d2 = 1.0 + (Z / 0.74) ** 6
    d3 = np.sqrt(1.0 + (Z / 6.9) ** 2)
    return 980.0 * num / (d1 * d2 * d3)


def scaled_impulse(Z, W_kg):
    """Positive-phase specific impulse i_s [kPa·ms] via a triangular-pulse approximation
    i_s ~= 0.5 * P_so * t_d. Decreases with distance and grows with yield in the mid/far
    field (Z >~ 1). APPROXIMATE: in the very near field (Z < ~1, essentially inside the
    fireball) the triangular approximation is unreliable — use the cascade/crush model
    there, not this impulse estimate."""
    P = overpressure_kpa(Z)
    t_d = scaled_duration(Z) * (W_kg ** (1.0 / 3.0))   # ms
    return 0.5 * P * t_d


def pi_damage(P_kpa, I_kpa_ms, p_crit, i_crit):
    """Pressure-impulse (P-I) damage in [0,1]. A normalized hyperbolic P-I curve: an
    element nears failure when (P/Pcrit)+(I/Icrit) is large, capturing BOTH the impulsive
    regime (small/fast charges: high I, short pulse) and the quasi-static regime (large/
    slow charges: high P). Distinct from peak-pressure-only `P/res`.

    NOTE: i_crit must be calibrated per material/element for production use; see _USE_PI.
    """
    # p_crit / i_crit may be scalars OR per-voxel arrays (res is per-voxel) -> vectorized clamp.
    p = np.asarray(P_kpa, dtype=float) / np.maximum(np.asarray(p_crit, dtype=float), 1e-6)
    i = np.asarray(I_kpa_ms, dtype=float) / np.maximum(np.asarray(i_crit, dtype=float), 1e-6)
    # Hyperbolic interaction: failure surface at p*i + p + i = 1 boundary -> damage 1.
    return np.clip(p * i + p + i, 0.0, 1.0)


def reinforcement_resistance_mult(ratio_percent):
    """Reinforcement ratio (配筋率, %) -> effective resistance multiplier in [1, _REINF_MULT_CAP].

    ρ=0 (masonry / steel / timber / unreinforced) -> 1.0 (no change); heavily reinforced RC
    (ρ~3%, e.g. a basement-roof / foundation mat) -> ~2.3x. Applied to the per-voxel ``res`` so it
    hardens BOTH the overpressure-damage criterion (P/res, i_crit=res*_I_CRIT_REF) AND the
    penetrator energy cost (res_grid = res). Accepts a scalar or an array.
    """
    r = np.maximum(np.asarray(ratio_percent, dtype=float), 0.0)
    return np.clip(1.0 + _REINF_GAIN * r, 1.0, _REINF_MULT_CAP)


def radius_for_overpressure(target_kpa: float, W_kg: float) -> float:
    """Inverse: distance (m) at which peak overpressure equals target_kpa."""
    target_ratio = target_kpa / P0_KPA
    if target_ratio >= float(overpressure_ratio(1e-3)):
        return 0.0
    lo, hi = 1e-3, 1e4
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if float(overpressure_ratio(mid)) > target_ratio:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi) * (W_kg ** (1.0 / 3.0))


# --------------------------------------------------------------------------- #
# Voxel helpers
# --------------------------------------------------------------------------- #
def unpack(voxels: dict):
    """From a litematic_to_voxels payload -> (coords Nx3 int, palette_idx N, palette_ids)."""
    arr = np.asarray(voxels["blocks"], dtype=np.int64).reshape(-1, 4)
    coords = arr[:, :3]
    palette_idx = arr[:, 3]
    palette_ids = voxels.get("palette_ids") or ["minecraft:stone"] * len(voxels["palette"])
    return coords, palette_idx, palette_ids


def velocity_vector(azimuth_deg: float, dive_deg: float) -> np.ndarray:
    """Incoming-bomb unit vector in Y-up voxel space (descending -> y component < 0)."""
    dive = math.radians(dive_deg)
    az = math.radians(azimuth_deg)
    horiz = math.cos(dive)
    v = np.array([horiz * math.cos(az), -math.sin(dive), horiz * math.sin(az)], dtype=float)
    n = np.linalg.norm(v)
    return v / n if n else np.array([0.0, -1.0, 0.0])


def _dense_grid(coords: np.ndarray) -> np.ndarray:
    shape = tuple(int(c) for c in (coords.max(axis=0) + 1))
    grid = np.zeros(shape, dtype=bool)
    grid[coords[:, 0], coords[:, 1], coords[:, 2]] = True
    return grid

def prepare_context(coords: np.ndarray, palette_idx: np.ndarray, palette_ids: list[str], *,
                    member_resistance_mult=None, element_ids=None, element_table=None,
                    support_graph=None) -> dict:
    """Precompute immutable scene arrays reused by multi-strike blast plans."""
    coords_f = np.asarray(coords, dtype=float)
    ci = coords_f.astype(np.int64)
    res_per_palette = np.array(
        [BLOCK_RESISTANCE.get(b, DEFAULT_RESISTANCE) for b in palette_ids], dtype=float)
    res = res_per_palette[np.asarray(palette_idx, dtype=np.int64)]
    if member_resistance_mult is not None:
        mrm = np.asarray(member_resistance_mult, dtype=float)
        if mrm.shape == (len(coords_f),):
            res = res * np.maximum(mrm, 1e-6)

    base_grid = _dense_grid(ci)
    res_grid = np.zeros(base_grid.shape, dtype=float)
    res_grid[ci[:, 0], ci[:, 1], ci[:, 2]] = res

    col_id = ci[:, 0] * int(base_grid.shape[2]) + ci[:, 2]
    col_order = np.argsort(col_id, kind="stable")
    col_unique, col_start, col_count = np.unique(
        col_id[col_order], return_index=True, return_counts=True)

    ctx = {
        "coords": coords_f,
        "ci": ci,
        "res": res,
        "base_grid": base_grid,
        "grid": base_grid.copy(),
        "res_grid": res_grid,
        "diag": float(np.linalg.norm(base_grid.shape)),
        "col_id": col_id,
        "col_order": col_order,
        "col_unique": col_unique,
        "col_start": col_start,
        "col_count": col_count,
    }

    if support_graph is not None and element_ids is not None and element_table is not None:
        try:
            eids = np.asarray(element_ids)
            if len(eids) == len(coords_f):
                n_mem = len(support_graph["ids"])
                node_of_table = np.array(
                    [support_graph["index"].get(t, -1) for t in element_table], dtype=np.int64)
                has_node0 = (eids >= 0) & (eids < len(node_of_table))
                vn_safe = np.where(has_node0, eids, 0)
                voxel_node = np.where(has_node0, node_of_table[vn_safe], -1)
                has_node = voxel_node >= 0
                cnt_all = np.bincount(voxel_node[has_node], minlength=n_mem)
                ctx["structure"] = {
                    "support_graph": support_graph,
                    "voxel_node": voxel_node,
                    "has_node": has_node,
                    "cnt_all": cnt_all,
                    "n_mem": n_mem,
                }
        except Exception:
            pass
    return ctx

def _raycast_first_solid(grid: np.ndarray, origin: np.ndarray, direction: np.ndarray,
                         max_steps: int):
    """3D-DDA (Amanatides-Woo). Returns (impact_cell, surface_normal) or (None, None)."""
    X, Y, Z = grid.shape
    d = direction.astype(float)
    cell = np.floor(origin).astype(int)
    step = np.where(d > 0, 1, -1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_delta = np.abs(1.0 / d)
        next_b = cell + (step > 0).astype(int)
        t_max = np.where(d != 0, (next_b - origin) / d, np.inf)

    def solid(c):
        return 0 <= c[0] < X and 0 <= c[1] < Y and 0 <= c[2] < Z and grid[c[0], c[1], c[2]]

    if solid(cell):
        return cell.copy(), -np.sign(d)  # started inside; coarse normal
    entered = False
    for _ in range(max_steps):
        axis = int(np.argmin(t_max))
        cell[axis] += step[axis]
        t_max[axis] += t_delta[axis]
        inside = 0 <= cell[0] < X and 0 <= cell[1] < Y and 0 <= cell[2] < Z
        if inside:
            entered = True
            if grid[cell[0], cell[1], cell[2]]:
                normal = np.zeros(3)
                normal[axis] = -step[axis]  # entered through face opposing the step
                return cell.copy(), normal
        elif entered:
            break  # left the grid after passing through without a hit
    return None, None


# --------------------------------------------------------------------------- #
# Main blast computation
# --------------------------------------------------------------------------- #
def _penetrate_path(grid, res_grid, impact_cell, v, E0, max_layers=None,
                    cap=_PEN_CAP, air_drag=_AIR_DRAG):
    """Kinetic-energy penetrator march from the impact face along v.

    Models a real bunker-buster: the body spends kinetic energy boring through each solid
    cell (harder/denser material = costlier), coasts almost free through voids (rooms),
    and counts the slabs/walls it punches (void->solid transitions = layers). It detonates
    when energy runs out (blocked by thick/hard structure), when a programmed layer count
    is reached (smart fuze), or when it over-penetrates out the far side.

    Returns (burst_xyz [float], layers, dist_cells, reason, pen_points).
    pen_points: 每穿过一层(void->solid)的穿透点坐标列表,供 compute_blast 沿路径加碎裂(消除穿模无痕)。
    """
    gx, gy, gz = grid.shape
    base = impact_cell.astype(float) + 0.5
    E = float(E0)
    layers = 0
    prev_solid = True            # we begin embedded in the struck skin (the entry layer)
    last = base.copy()
    pen_points = []              # 穿透点(每穿过一层楼板/墙记一点)
    step = 0.5
    t = 0.0
    while t < cap:
        t += step
        p = base + v * t
        c = np.floor(p).astype(int)
        if not (0 <= c[0] < gx and 0 <= c[1] < gy and 0 <= c[2] < gz):
            return last, layers, t, "exit", pen_points          # over-penetrated out the far side
        solid = bool(grid[c[0], c[1], c[2]])
        if solid:
            if not prev_solid:
                layers += 1
                pen_points.append(p.copy())          # 记录穿透点(供路径碎裂)
                if max_layers is not None and layers >= max_layers:
                    return p, layers, t, "layers", pen_points    # smart-fuze layer count reached
            E -= math.sqrt(max(float(res_grid[c[0], c[1], c[2]]), 1.0)) * step
            last = p.copy()
            if E <= 0.0:
                return p, layers, t, "blocked", pen_points       # stopped by thick/hard structure
        else:
            E -= air_drag * step                     # coasting through a void
        prev_solid = solid
    return last, layers, t, "cap", pen_points


def compute_blast(coords: np.ndarray, palette_idx: np.ndarray, palette_ids: list[str], *,
                  yield_kg: float, aim, azimuth_deg: float, dive_deg: float,
                  fuze: str = "surface", blocks_per_meter: float = 6.0,
                  occlusion: bool = True, removed_idx=None, pen_layers=None,
                  element_ids=None, element_table=None, support_graph=None,
                  member_resistance_mult=None, return_sparse: bool = False, context: dict | None = None,
                  removed_mask=None, occlusion_quality: str = "full") -> dict:
    """Compute per-voxel blast damage for a directional bombing run.

    Returns a dict with ``damage`` (list aligned to ``coords`` order), ``burst``,
    ``impact``, ``v``, ``rings_m`` and ``stats``.

    If ``support_graph`` + ``element_ids`` + ``element_table`` are supplied (the
    member-level structural model), per-voxel damage is aggregated onto members and a
    progressive-collapse cascade runs: members that lose their load path bring down
    everything they support. Without them, behaviour is the legacy per-voxel model.
    """
    _t0 = _tlast = time.perf_counter()
    timings = []

    def _mark(stage: str):
        nonlocal _tlast
        now = time.perf_counter()
        timings.append({
            "stage": stage,
            "delta_ms": round((now - _tlast) * 1000.0, 3),
            "elapsed_ms": round((now - _t0) * 1000.0, 3),
        })
        _tlast = now

    if context is not None:
        coords = context["coords"]
        ci0 = context["ci"]
        res = context["res"]
        grid = context.get("grid")
        if grid is None:
            grid = context["base_grid"].copy()
        diag = float(context.get("diag", np.linalg.norm(grid.shape)))
    else:
        coords = np.asarray(coords, dtype=float)
        ci0 = coords.astype(np.int64)
        res_per_palette = np.array(
            [BLOCK_RESISTANCE.get(b, DEFAULT_RESISTANCE) for b in palette_ids], dtype=float)
        res = res_per_palette[palette_idx]
        if member_resistance_mult is not None:
            mrm = np.asarray(member_resistance_mult, dtype=float)
            if mrm.shape == (len(coords),):
                res = res * np.maximum(mrm, 1e-6)
        grid = _dense_grid(ci0)
        diag = float(np.linalg.norm(grid.shape))
    n = len(coords)
    W = max(float(yield_kg), 1e-3)
    bpm = float(blocks_per_meter)
    _mark("context_material_grid")

    # Blocks destroyed by prior strikes are holes: drop them from the occupancy so
    # this strike's ray-cast, occlusion and burst see the existing crater.
    if removed_mask is not None:
        removed_bool = np.asarray(removed_mask, dtype=bool)
        if removed_bool.shape != (n,):
            removed_bool = np.zeros(n, dtype=bool)
    else:
        removed_bool = np.zeros(n, dtype=bool)
        if removed_idx is not None and len(removed_idx):
            ridx = np.asarray(removed_idx, dtype=np.int64)
            ridx = ridx[(ridx >= 0) & (ridx < n)]
            removed_bool[ridx] = True
            if context is None:
                rc = ci0[ridx]
                grid[rc[:, 0], rc[:, 1], rc[:, 2]] = False
    _mark("removed_mask")
    v = velocity_vector(azimuth_deg, dive_deg)
    aim = np.asarray(aim, dtype=float)

    # Trajectory: start outside the building along -v, march in to find impact.
    origin = aim - v * (diag + 4.0)
    max_steps = int(4 * sum(grid.shape) + 4 * diag + 16)
    impact_cell, normal = _raycast_first_solid(grid, origin, v, max_steps)
    if impact_cell is None:
        # No hit along the path: detonate at the solid voxel nearest the aim point.
        nearest = int(np.argmin(np.linalg.norm(coords - aim, axis=1)))
        impact = coords[nearest].copy()
        normal = -v
    else:
        impact = impact_cell.astype(float) + 0.5
    normal = np.asarray(normal, dtype=float)
    nlen = np.linalg.norm(normal)
    normal = normal / nlen if nlen else -v

    cos_inc = float(np.clip(np.dot(-v, normal), 0.0, 1.0))
    res_impact = float(BLOCK_RESISTANCE.get(
        palette_ids[int(palette_idx[int(np.argmin(np.linalg.norm(coords - impact, axis=1)))])],
        DEFAULT_RESISTANCE))
    pen_layers_used = 0
    pen_reason = "surface"
    pen_points: list = []   # 钻地弹侵彻路径的穿透点(surface 引信为空)
    if fuze == "surface":
        # Contact/触发: detonates on the skin it first strikes — blast must then work
        # inward through the structure (shaves the facade/roof, spares the interior).
        burst = impact + v * _PEN_SURFACE
    else:
        # Penetrator/钻地: bore through the structure cell-by-cell on a kinetic-energy
        # budget, coasting through rooms and counting slabs, until energy runs out (stopped
        # by thick/hard structure), a programmed layer count is hit, or it exits the far
        # side. Burst sits where it stops — deep inside, not at the skin.
        if context is not None and "res_grid" in context:
            res_grid = context["res_grid"]
        else:
            res_grid = np.zeros(grid.shape, dtype=float)
            res_grid[ci0[:, 0], ci0[:, 1], ci0[:, 2]] = res
        ic = impact_cell if impact_cell is not None else np.floor(impact).astype(np.int64)
        E0 = _PEN_E * (W ** (1.0 / 3.0)) * (cos_inc ** _P_INC)
        ml = int(pen_layers) if pen_layers else None
        burst_xyz, pen_layers_used, _t, pen_reason, pen_points = _penetrate_path(grid, res_grid, np.asarray(ic), v, E0, ml)
        burst = np.asarray(burst_xyz, dtype=float)
    pen = float(np.linalg.norm(burst - impact))
    _mark("trajectory_penetration")

    # Distance-based Kinney-Graham overpressure. In interactive plan mode, compute
    # the expensive pressure/cone fields only inside the maximum physical effect
    # radius; all outside voxels remain zero-damage and cannot affect sparse output.
    quality = (occlusion_quality or "full").lower()
    crush_cells = _K_CRUSH * (W ** (1.0 / 3.0)) * bpm
    ring_max_cells = radius_for_overpressure(min(RING_THRESHOLDS), W) * bpm
    if quality in ("interactive", "fast"):
        effect_cells = max(ring_max_cells, crush_cells, 2.0) + 1.0
        spatial_mask = ((np.abs(coords[:, 0] - burst[0]) <= effect_cells) &
                        (np.abs(coords[:, 1] - burst[1]) <= effect_cells) &
                        (np.abs(coords[:, 2] - burst[2]) <= effect_cells))
        field_idx = np.flatnonzero(spatial_mask)
        sub = coords[field_idx]
        diff_sub = sub - burst
        dist_sub = np.linalg.norm(diff_sub, axis=1)
        dist_safe_sub = np.maximum(dist_sub, 1e-6)
        Z_sub = scaled_distance(dist_sub / bpm, W)
        P_sub = overpressure_kpa(Z_sub)
        u_sub = diff_sub / dist_safe_sub[:, None]
        cos_fwd_sub = u_sub @ v
        cos_half = math.cos(math.radians(_CONE_HALF_DEG))
        cone_sub = np.clip((cos_fwd_sub - cos_half) / (1.0 - cos_half), 0.0, 1.0)
        P_sub = P_sub * (1.0 + _CONE_GAIN * cone_sub)
        dist_cells = np.full(n, np.inf, dtype=float)
        Z = np.full(n, 1.0e9, dtype=float)
        P = np.zeros(n, dtype=float)
        dist_cells[field_idx] = dist_sub
        Z[field_idx] = Z_sub
        P[field_idx] = P_sub
        field_count = int(field_idx.size)
    else:
        diff = coords - burst
        dist_cells = np.linalg.norm(diff, axis=1)
        dist_safe = np.maximum(dist_cells, 1e-6)
        R_m = dist_cells / bpm
        Z = scaled_distance(R_m, W)
        P = overpressure_kpa(Z)
        u = diff / dist_safe[:, None]
        cos_fwd = u @ v
        cos_half = math.cos(math.radians(_CONE_HALF_DEG))
        cone = np.clip((cos_fwd - cos_half) / (1.0 - cos_half), 0.0, 1.0)
        P = P * (1.0 + _CONE_GAIN * cone)
        field_idx = None
        field_count = int(n)
    _mark("pressure_cone")
    # (b) Occlusion: intervening solid mass between burst and each voxel shelters it.
    # occ_full[i] = how many solid samples the burst->voxel sightline crosses (0 = clear LoS).
    # Drives BOTH the overpressure attenuation AND the crush-zone gate below, so a voxel
    # screened by a wall is never destroyed straight "through" it — the 隔山打牛 fix.
    occ_full = np.zeros(n)
    crush_cells = _K_CRUSH * (W ** (1.0 / 3.0)) * bpm
    occ_stats = {"candidates": 0, "sampled": 0, "quality": occlusion_quality, "field_count": field_count}
    if occlusion:
        ring_max_cells = radius_for_overpressure(min(RING_THRESHOLDS), W) * bpm
        quality = (occlusion_quality or "full").lower()
        if quality == "interactive":
            sample_cap = 12
            min_gate = 0.05
        elif quality == "fast":
            sample_cap = 8
            min_gate = 0.10
        else:
            sample_cap = _OCC_SAMPLES
            min_gate = 0.0

        raw_gate = P / np.maximum(res, 1e-6)
        candidate_mask = dist_cells <= max(ring_max_cells, 1.0)
        if min_gate > 0.0:
            candidate_mask &= ((raw_gate >= min_gate) | (dist_cells <= crush_cells))
        near_idx = np.flatnonzero(candidate_mask)
        occ_stats["candidates"] = int(near_idx.size)

        def _apply_occlusion(sel_idx, sample_count: int):
            if len(sel_idx) == 0:
                return
            gx, gy, gz = grid.shape
            ts = np.linspace(0.0, 1.0, int(sample_count) + 2)[1:-1]
            for start in range(0, len(sel_idx), _OCC_CHUNK):
                chunk_idx = sel_idx[start:start + _OCC_CHUNK]
                sub = coords[chunk_idx]
                ns = len(sub)
                occ = np.zeros(ns, dtype=float)
                air_after = np.zeros(ns, dtype=bool)
                for t in ts[::-1]:
                    pts = burst + (sub - burst) * t
                    ci = np.floor(pts).astype(int)
                    ib = ((ci[:, 0] >= 0) & (ci[:, 0] < gx) & (ci[:, 1] >= 0) &
                          (ci[:, 1] < gy) & (ci[:, 2] >= 0) & (ci[:, 2] < gz))
                    h = np.zeros(ns, dtype=bool)
                    if np.any(ib):
                        cj = ci[ib]
                        h[ib] = grid[cj[:, 0], cj[:, 1], cj[:, 2]]
                    occ += (h & air_after)
                    air_after |= ~h
                occ_full[chunk_idx] = occ
                atten = np.exp(-_OCC_MU * np.maximum(occ - _OCC_FREE, 0.0))
                Pn = P[chunk_idx] * atten
                blocked = occ >= _OCC_BLOCK
                Pn[blocked] = np.minimum(Pn[blocked], _SHADOW_KPA)
                P[chunk_idx] = Pn

        if len(near_idx):
            d_near = dist_cells[near_idx]
            near_cut = max(crush_cells * 2.0, bpm * 4.0)
            mid_cut = max(near_cut, ring_max_cells * 0.35)
            idx24 = near_idx[d_near <= near_cut]
            idx12 = near_idx[(d_near > near_cut) & (d_near <= mid_cut)]
            idx6 = near_idx[d_near > mid_cut]
            s24 = min(sample_cap, 24)
            s12 = min(sample_cap, 12)
            s6 = min(sample_cap, 6)
            _apply_occlusion(idx24, s24)
            _apply_occlusion(idx12, s12)
            _apply_occlusion(idx6, s6)
            occ_stats["sampled"] = int(len(idx24) * s24 + len(idx12) * s12 + len(idx6) * s6)
    _mark("occlusion")
    if field_idx is not None:
        damage = np.zeros(n, dtype=float)
        if len(field_idx):
            if _USE_PI:
                I_sub = scaled_impulse(Z[field_idx], W)
                damage[field_idx] = pi_damage(P[field_idx], I_sub, res[field_idx], res[field_idx] * _I_CRIT_REF)
            else:
                damage[field_idx] = np.clip(P[field_idx] / np.maximum(res[field_idx], 1e-6), 0.0, 1.0)
    elif _USE_PI:
        # Pressure-impulse criterion: distinguishes impulsive (small/fast) vs quasi-static
        # (large/slow) charges that peak-pressure-only cannot. Off by default (the i_crit
        # scaling needs per-material calibration); `P/res` remains the validated default.
        I_field = scaled_impulse(Z, W)
        damage = pi_damage(P, I_field, res, res * _I_CRIT_REF)
    else:
        damage = np.clip(P / res, 0.0, 1.0)    # Near-burst pulverization, GATED BY LINE-OF-SIGHT. A warhead detonating in contact with
    # (or inside) a structural element pulverizes the material immediately around it — but only
    # material the burst can actually "see" (occ_full <= _CRUSH_OCC_MAX). Material behind an
    # intervening wall/slab is NOT crushed through it, so the crush zone no longer punches the
    # 隔山打牛 hole it used to; shielded near voxels fall back to the (occlusion-attenuated)
    # P/res criterion above.
    if crush_cells > 0:
        crush_mask = (dist_cells <= crush_cells) & (occ_full <= _CRUSH_OCC_MAX)
        damage[crush_mask] = 1.0
    # 侵彻路径碎裂:钻地弹穿过的每层穿透点周围加一圈崩裂(小半径 ~1m),消除"穿模无痕"——
    # 现在钻穿沿途每层都留破口,而非只在爆心毁伤。
    for pp in pen_points:
        d_pp = np.linalg.norm(coords - np.asarray(pp, dtype=float), axis=1)
        damage[d_pp <= 2.0] = 1.0
    damage[removed_bool] = 0.0   # already gone from a prior strike
    _mark("damage_crush")

    # ---- Progressive collapse (alternate load path) -------------------------
    # With a member-level support graph, turn independent per-voxel damage into real
    # disproportionate collapse: aggregate damage onto members, run the cascade, and
    # project failures back to voxels — blast/overload failures pulverize (debris),
    # members that lose their load path collapse (pancake down). No graph => unchanged.
    collapse_vox = np.zeros(n, dtype=bool)
    cascade_stats = None
    struct_ctx = context.get("structure") if context is not None else None
    if struct_ctx is not None or (support_graph is not None and element_ids is not None and element_table is not None):
        try:
            from ..shared import structure as _structure
            if struct_ctx is not None:
                support_graph = struct_ctx["support_graph"]
                voxel_node = struct_ctx["voxel_node"]
                has_node = struct_ctx["has_node"]
                cnt_all = struct_ctx["cnt_all"]
                n_mem = int(struct_ctx["n_mem"])
            else:
                eids = np.asarray(element_ids)
                if len(eids) != n:
                    raise ValueError("element_ids length does not match voxel count")
                n_mem = len(support_graph["ids"])
                node_of_table = np.array(
                    [support_graph["index"].get(t, -1) for t in element_table], dtype=np.int64)
                has_node = (eids >= 0) & (eids < len(node_of_table))
                vn_safe = np.where(has_node, eids, 0)
                voxel_node = np.where(has_node, node_of_table[vn_safe], -1)
                has_node = voxel_node >= 0
                cnt_all = np.bincount(voxel_node[has_node], minlength=n_mem)

            member_damage = _structure.aggregate_member_damage(damage, voxel_node, n_mem)
            dead = (damage >= _DESTROY) & has_node
            cnt_dead = np.bincount(voxel_node[dead], minlength=n_mem)
            with np.errstate(divide="ignore", invalid="ignore"):
                dfrac = np.where(cnt_all > 0, cnt_dead / np.maximum(cnt_all, 1), 0.0)
            cls_by_node = support_graph.get("cls") or []
            fail_frac = np.full(n_mem, _MEMBER_FAIL_FRAC, dtype=float)
            vert_node = np.zeros(n_mem, dtype=bool)
            for _mi in range(min(n_mem, len(cls_by_node))):
                if cls_by_node[_mi] in _VERTICAL_CLASSES:
                    fail_frac[_mi] = _MEMBER_FAIL_FRAC_VERTICAL
                    vert_node[_mi] = True
            # base-severance: topple a vertical member if the foot band of its section is destroyed
            base_severed = np.zeros(n_mem, dtype=bool)
            vn0 = np.where(has_node, voxel_node, 0)
            is_vert_vox = has_node & vert_node[vn0]
            if is_vert_vox.any():
                yv = ci0[:, 1].astype(np.int64)
                member_min_y = np.full(n_mem, 1 << 30, dtype=np.int64)
                np.minimum.at(member_min_y, voxel_node[is_vert_vox], yv[is_vert_vox])
                is_base = is_vert_vox & (yv <= member_min_y[vn0] + _MEMBER_BASE_BAND)
                base_all = np.bincount(voxel_node[is_base], minlength=n_mem)
                base_dead = np.bincount(voxel_node[is_base & dead], minlength=n_mem)
                with np.errstate(divide="ignore", invalid="ignore"):
                    base_frac = np.where(base_all > 0, base_dead / np.maximum(base_all, 1), 0.0)
                base_severed = vert_node & (base_all > 0) & (base_frac >= _MEMBER_BASE_FAIL_FRAC)
            blast_failed = set(int(i) for i in np.where((dfrac >= fail_frac) | base_severed)[0])
            if removed_bool.any():
                rm = voxel_node[removed_bool & has_node]
                if len(rm):
                    cnt_rm = np.bincount(rm, minlength=n_mem)
                    with np.errstate(divide="ignore", invalid="ignore"):
                        rfrac = np.where(cnt_all > 0, cnt_rm / np.maximum(cnt_all, 1), 0.0)
                    blast_failed |= set(int(i) for i in np.where(rfrac >= 0.6)[0])
            res_collapse = _structure.run_collapse(support_graph, list(blast_failed), member_damage)
            reason = res_collapse["reason"]
            destroy_node = np.zeros(n_mem, dtype=bool)
            collapse_node = np.zeros(n_mem, dtype=bool)
            for m in res_collapse["failed"]:
                if reason.get(m) in ("blast", "overload"):
                    destroy_node[m] = True
                else:
                    collapse_node[m] = True
            vn = np.where(has_node, voxel_node, 0)
            destroy_vox = has_node & destroy_node[vn] & ~removed_bool
            collapse_vox = has_node & collapse_node[vn] & ~removed_bool & (damage < _DESTROY)
            damage[destroy_vox] = 1.0
            damage[removed_bool] = 0.0
            n_blast = sum(1 for m in res_collapse["failed"] if reason.get(m) == "blast")
            n_over = sum(1 for m in res_collapse["failed"] if reason.get(m) == "overload")
            n_unsup = sum(1 for m in res_collapse["failed"] if reason.get(m) == "unsupported")
            cascade_stats = {
                "members_total": n_mem,
                "members_failed": int(len(res_collapse["failed"])),
                "by_reason": {"blast": n_blast, "overload": n_over, "unsupported": n_unsup},
                "members_collapsed": int(collapse_node.sum()),
                "cascade_iters": res_collapse["iters"],
            }
        except Exception:
            collapse_vox = np.zeros(n, dtype=bool)
            cascade_stats = None
    _mark("progressive_collapse")
    # Gravity collapse: a surviving voxel falls by the number of void cells below
    # it in its own (x,z) column (blown out this strike OR a prior one). Upper
    # floors pancake into the crater; intact lower structure stays put.
    ci = ci0
    destroyed_mask = damage >= _DESTROY
    void_mask = destroyed_mask | removed_bool
    if return_sparse and context is not None and "col_unique" in context:
        fall = np.zeros(n, dtype=np.int16)
        new_void = destroyed_mask & ~removed_bool
        col_id = context["col_id"]
        affected = new_void | collapse_vox
        affected_cols = np.unique(col_id[affected]) if np.any(affected) else np.array([], dtype=col_id.dtype)
        col_unique = context["col_unique"]
        col_order = context["col_order"]
        col_start = context["col_start"]
        col_count = context["col_count"]
        height = int(grid.shape[1])
        for col in affected_cols:
            pos = np.searchsorted(col_unique, col)
            if pos >= len(col_unique) or col_unique[pos] != col:
                continue
            idx = col_order[col_start[pos]:col_start[pos] + col_count[pos]]
            ys = ci[idx, 1].astype(np.int64)
            col_void = np.zeros(height, dtype=bool)
            col_void[ys] = void_mask[idx]
            below = np.zeros(height, dtype=np.int16)
            if height > 1:
                below[1:] = np.cumsum(col_void, dtype=np.int32)[:-1]
            vals = np.where(void_mask[idx], 0, np.minimum(below[ys], _FALL_CAP)).astype(np.int16)
            moved = vals > 0
            if np.any(moved):
                fall[idx[moved]] = vals[moved]
        if collapse_vox.any():
            floor_y = int(ci[:, 1].min())
            cfall = np.minimum(ci[:, 1] - floor_y, _FALL_CAP).astype(np.int16)
            fall = np.where(collapse_vox & ~void_mask, np.maximum(fall, cfall), fall).astype(np.int16)
        gravity_mode = "local_columns"
    else:
        dgrid = np.zeros(grid.shape, dtype=bool)
        dgrid[ci[:, 0], ci[:, 1], ci[:, 2]] = void_mask
        below = np.zeros(grid.shape, dtype=np.int32)
        below[:, 1:, :] = np.cumsum(dgrid, axis=1)[:, :-1, :]
        fall_at = below[ci[:, 0], ci[:, 1], ci[:, 2]]
        fall = np.where(void_mask, 0, np.minimum(fall_at, _FALL_CAP)).astype(int)
        if collapse_vox.any():
            floor_y = int(ci[:, 1].min())
            cfall = np.minimum(ci[:, 1] - floor_y, _FALL_CAP)
            fall = np.where(collapse_vox & ~void_mask, np.maximum(fall, cfall), fall).astype(int)
        gravity_mode = "full_grid"
    _mark("gravity_fall")
    destroyed = int(np.count_nonzero(destroyed_mask))
    damaged = int(np.count_nonzero((damage >= _DAMAGE) & (damage < _DESTROY)))

    rings_m = [{"kPa": k, "r": round(radius_for_overpressure(k, W), 2)} for k in RING_THRESHOLDS]

    # Damage by vertical band (approx storeys), in building metres.
    y = coords[:, 1]
    y0, y1 = float(y.min()), float(y.max())
    bands = []
    nb = 4
    for b in range(nb):
        lo = y0 + (y1 - y0) * b / nb
        hi = y0 + (y1 - y0) * (b + 1) / nb
        m = (y >= lo) & (y <= hi if b == nb - 1 else y < hi)
        tot = int(m.sum())
        bands.append({
            "z_lo_m": round(lo / bpm, 2), "z_hi_m": round(hi / bpm, 2),
            "total": tot,
            "destroyed": int(np.count_nonzero(m & (damage >= _DESTROY))),
        })
    _mark("stats_bands")

    if return_sparse:
        destroyed_idx = np.flatnonzero(damage >= _DESTROY).astype(np.int64)
        damaged_idx = np.flatnonzero((damage >= _DAMAGE) & (damage < _DESTROY)).astype(np.int64)
        fall_idx = np.flatnonzero((fall > 0) & (damage < _DESTROY)).astype(np.int64)
        sparse_out = {
            "destroyed_idx": destroyed_idx.tolist(),
            "damaged_idx": damaged_idx.tolist(),
            "fall_idx": fall_idx.tolist(),
            "fall_values": fall[fall_idx].astype(np.int16).tolist(),
        }
        damage_out = None
        fall_out = None
    else:
        sparse_out = None
        damage_out = [round(float(x), 4) for x in damage]
        fall_out = fall.tolist()
    _mark("materialize_output_lists")

    out = {
        "burst": [round(float(x), 3) for x in burst],
        "impact": [round(float(x), 3) for x in impact],
        "v": [round(float(x), 4) for x in v],
        "rings_m": rings_m,
        "timings_ms": timings,
        "sparse": sparse_out,
        "stats": {
            "total": n,
            "destroyed": destroyed,
            "damaged": damaged,
            "destroyed_pct": round(100.0 * destroyed / n, 2) if n else 0.0,
            "incidence_deg": round(math.degrees(math.acos(cos_inc)), 1),
            "penetration_cells": round(pen, 2),
            "penetration_layers": pen_layers_used,
            "penetration_stop": pen_reason,
            "max_overpressure_kPa": round(float(P.max()), 1) if n else 0.0,
            "by_height_band": bands,
            "yield_kg": W,
            "azimuth_deg": azimuth_deg,
            "dive_deg": dive_deg,
            "fuze": fuze,
            "cascade": cascade_stats,
            "occlusion": occ_stats,
            "gravity_mode": gravity_mode,
            "timing_ms_total": timings[-1]["elapsed_ms"] if timings else 0.0,
        },
    }
    if return_sparse:
        out["sparse"] = sparse_out
    else:
        out["damage"] = damage_out
        out["fall"] = fall_out
    return out


# --------------------------------------------------------------------------- #
# Export: bombed litematic + damage report
# --------------------------------------------------------------------------- #
def export_damaged(litematic_path, damage, out_litematic, *, destroy_threshold: float = _DESTROY,
                   name: str | None = None) -> int:
    """Rewrite the litematic with destroyed voxels removed. Returns removed count.

    ``damage`` must be aligned to litematic_to_voxels' non-air block order.
    """
    from litemapy import BlockState, Schematic

    schem = Schematic.load(str(litematic_path))
    region = next(iter(schem.regions.values()))
    removed = 0
    try:
        # Fast path: clear destroyed cells directly in the palette-index array.
        blk = region._Region__blocks
        pal = region._Region__palette
        air_ids = [i for i, b in enumerate(pal) if b.id == "minecraft:air"]
        air_idx = air_ids[0] if air_ids else len(pal)
        if not air_ids:
            pal.append(BlockState("minecraft:air"))
        mask = ~np.isin(blk, np.array(air_ids, dtype=blk.dtype)) if air_ids else np.ones(blk.shape, bool)
        coords = np.argwhere(mask)  # same x->y->z order as litematic_to_voxels / damage
        dmg = np.asarray(damage, dtype=float)
        n = min(len(coords), len(dmg))
        sel = coords[:n][dmg[:n] >= destroy_threshold]
        if len(sel):
            blk[sel[:, 0], sel[:, 1], sel[:, 2]] = air_idx
            removed = int(len(sel))
    except Exception:
        air = BlockState("minecraft:air")
        dmg = list(damage)
        i = 0
        for x, y, z in region.block_positions():
            if region[x, y, z].id == "minecraft:air":
                continue
            if i < len(dmg) and dmg[i] >= destroy_threshold:
                region[x, y, z] = air
                removed += 1
            i += 1
    out = Path(out_litematic)
    out.parent.mkdir(parents=True, exist_ok=True)
    Schematic(name=name or schem.name, author="stand_trans-blast",
              description="post-blast remnant", regions={"main": region}).save(str(out))
    return removed


def write_report(report_path, params: dict, result: dict) -> str:
    out = Path(report_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "params": params,
        "burst": result["burst"],
        "impact": result["impact"],
        "rings_m": result["rings_m"],
        "stats": result["stats"],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)
