"""Aircraft-carrier / ship geometry for the visual GLB + voxel pipeline.

A ship is a *param-only feature* (like domes/gardens): read straight from
``param["ships"]`` by ``collect_meshes`` and emitted as parallel (meshes, kinds)
lists. The hull is a LOFTED surface — the first non-prismatic primitive in the
generator — built as a **fixed-topology station loft** so it is watertight /
manifold and voxel-fills solid via ``mesh.voxelized(pitch).fill()``. The flight
deck, island, internal decks and the (GLB-only) sea are axis-aligned boxes that
reuse ``primitives.box``.

Local authoring frame (Z-up, like every other primitive; the exporter rotates the
whole scene to Y-up):
    x = stern(-L/2) -> bow(+L/2)   y = transverse (starboard = -y)   z: waterline = 0
Everything is built around the ship's own origin, then a single transform applies
``heading`` (rotation about Z) and moves it to the world ``origin`` at the
waterline elevation of the ship's level.

Colours are baked as explicit face colours (both the GLB and the voxel viewer read
the mesh face colour), so the warship look does not depend on the building style
palette. Every mesh is tagged material ``"steel"`` (a real material: iron_block,
blast 240 kPa) so the voxel/blast pipeline gets a block + resistance for free. The
sea plane is tagged class ``"glb_only"`` — a sentinel the litematic exporter drops
before voxelization so the ocean is never turned into blocks.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh

from . import primitives as prim

# ── warship palette (RGBA 0-255) ──────────────────────────────────────────────
_HULL_RGB = (92, 99, 107, 255)     # haze grey topsides
_DECK_RGB = (58, 60, 64, 255)      # dark non-skid flight deck
_ISLAND_RGB = (104, 110, 118, 255)
_MAST_RGB = (72, 76, 82, 255)
_WATER_RGB = (40, 96, 140, 170)    # translucent sea (GLB only)

_MAT = "steel"                     # every ship part: real material for block + blast_kPa
_HULL_KIND_CLASS = "primary_vertical"
_DECK_KIND_CLASS = "floor"
_GLB_ONLY = "glb_only"             # sentinel class: litematic drops these before voxelizing


def _rz4(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    m = np.eye(4)
    m[0, 0] = c; m[0, 1] = -s
    m[1, 0] = s; m[1, 1] = c
    return m


def _f_beam(u: float, bow_taper: float, stern: str, transom_ratio: float) -> float:
    """Longitudinal half-beam fullness in (0, 1]. u=0 stern .. u=1 bow.

    Midbody is full (1.0); the forebody fines down to a small (never zero) stem so
    the loft stays manifold; the stern is a wide transom or a tapering cruiser stern.
    """
    bow_start = 1.0 - bow_taper
    if u >= bow_start:                                   # fine bow entrance
        s = (u - bow_start) / max(bow_taper, 1e-6)
        f_stem = 0.06                                    # >0: avoid a degenerate knife edge
        return max(f_stem, 1.0 - (1.0 - f_stem) * (s ** 1.6))
    if stern == "cruiser":
        if u < 0.12:
            s = (0.12 - u) / 0.12
            return max(0.12, 1.0 - 0.8 * (s ** 1.5))
        return 1.0
    # transom (default): hold a wide flat stern, blend to full by u~0.14
    if u < 0.14:
        return transom_ratio + (1.0 - transom_ratio) * (u / 0.14)
    return 1.0


def _hull_mesh(h: dict):
    """Build the lofted hull as a single Trimesh in local coords. Returns (mesh, z_deck)
    where z_deck is the main-deck top (local z) the flight deck / island sit on.

    fill=="solid" (default): a fully closed watertight manifold -> voxel fill() solidifies.
    fill=="decks": the long top chord band is left open -> per-mesh fill() cannot seal it
    -> hull voxelizes as a hollow plating shell (far fewer blocks); the flight deck +
    internal deck slabs supply the strike/penetration layers.
    """
    L = float(h["length_m"]); B = float(h["beam_m"])
    depth = float(h["depth_m"]); draft = float(h.get("draft_m", depth * 0.4))
    bow_taper = float(h.get("bow_taper", 0.30))
    stem_flare = float(h.get("stem_flare", 1.0))
    bottom_ratio = float(h.get("bottom_ratio", 0.5))
    bilge_frac = float(h.get("bilge_frac", 0.30))
    stern = str(h.get("stern", "transom"))
    transom_ratio = float(h.get("transom_ratio", 0.82))
    sheer = float(h.get("sheer_m", depth * 0.10))
    solid = str(h.get("fill", "solid")) == "solid"
    M = int(h.get("stations", max(24, round(L / 7.0))))
    P = int(h.get("section_points", 10))

    z_keel = -draft
    z_deck0 = depth - draft                              # main deck above the waterline
    half_beam = B / 2.0

    rings: list[list[tuple]] = []
    for i in range(M + 1):
        u = i / M
        x = -L / 2.0 + u * L
        fb = _f_beam(u, bow_taper, stern, transom_ratio)
        b_deck = half_beam * fb * stem_flare             # topside half-breadth
        b_bot = half_beam * fb * bottom_ratio            # flat-bottom half-breadth
        z_deck = z_deck0 + sheer * (max(0.0, u - 0.6) / 0.4) ** 2   # raise the bow deck (sheer)
        star = []
        for p in range(P):                               # deck edge (f=0) -> keel centre (f=1)
            f = p / (P - 1)
            z = z_deck - (z_deck - z_keel) * f
            if f < 1.0 - bilge_frac:
                y = b_deck
            else:
                t = (1.0 - f) / bilge_frac                # 1 at bilge start .. 0 at keel
                y = b_bot + (b_deck - b_bot) * t
            if p == P - 1:
                y = 0.0                                   # keel exactly on centreline
            star.append((x, y, z))
        ring = list(star)                                 # starboard incl. keel
        for p in range(P - 2, -1, -1):                    # port, mirrored, excl. keel
            xx, yy, zz = star[p]
            ring.append((xx, -yy, zz))
        rings.append(ring)

    N = len(rings[0])                                     # 2P - 1
    verts: list = []
    for ring in rings:
        verts.extend(ring)

    def vid(i, k):
        return i * N + k

    faces: list = []
    kmax = N if solid else N - 1                          # include the top deck-chord band only when solid
    for i in range(M):
        for k in range(kmax):
            k2 = (k + 1) % N
            a, b, c, d = vid(i, k), vid(i, k2), vid(i + 1, k2), vid(i + 1, k)
            faces.append([a, b, c]); faces.append([a, c, d])
    # end caps: centroid fans over the FULL ring (incl. deck chord) so both ends are closed
    c0 = np.mean(np.asarray(rings[0]), axis=0)
    cM = np.mean(np.asarray(rings[-1]), axis=0)
    c0i, cMi = len(verts), len(verts) + 1
    verts.append(tuple(c0)); verts.append(tuple(cM))
    for k in range(N):
        k2 = (k + 1) % N
        faces.append([c0i, vid(0, k2), vid(0, k)])        # stern cap
        faces.append([cMi, vid(M, k), vid(M, k2)])        # bow cap

    mesh = trimesh.Trimesh(vertices=np.asarray(verts, dtype=float),
                           faces=np.asarray(faces, dtype=np.int64), process=False)
    mesh.fix_normals()                                    # consistent winding
    # orient outward (robust for the open-top shell too): flip if normals mostly face inward
    if np.einsum("ij,ij->i", mesh.face_normals, mesh.triangles_center - mesh.centroid).sum() < 0:
        mesh.invert()
    prim.color_mesh(mesh, _HULL_RGB)
    return mesh, z_deck0


def build_ship(s: dict, levels: dict) -> tuple[list, list]:
    """Expand one ``param["ships"]`` entry into (meshes, kinds) in world coords.

    ``levels`` = {name: level dict} from the BIM; the ship's ``level`` fixes the
    waterline elevation. Each returned mesh has an explicit warship face colour and a
    parallel ``kinds`` tuple (class, material, element_id) for the voxel/blast path.
    """
    sid = s.get("id", "ship")
    hull = s.get("hull", {})
    L = float(hull.get("length_m", 300.0))
    B = float(hull.get("beam_m", 40.0))
    solid = str(hull.get("fill", "solid")) == "solid"

    lv = levels.get(s.get("level"))
    z_wl = float(lv["elevation_m"]) if lv else 0.0
    ox, oy = (s.get("origin") or [0.0, 0.0])[:2]
    heading = math.radians(float(s.get("heading_deg", 0.0)))

    meshes: list = []
    kinds: list = []

    def add(mesh, cls, elem):
        meshes.append(mesh)
        kinds.append((cls, _MAT, f"ship:{sid}:{elem}"))

    # ── hull ─────────────────────────────────────────────────────────────────
    hull_mesh, z_deck = _hull_mesh(hull)
    add(hull_mesh, _HULL_KIND_CLASS, "hull")

    # ── flight deck (wide, cantilevered) + optional angled deck ────────────────
    fd = s.get("flight_deck", {})
    fd_len = float(fd.get("length_m", L))
    fd_w = float(fd.get("width_m", B * 1.9))
    fd_t = float(fd.get("thickness_m", 0.6))
    add(prim.box([0.0, 0.0, z_deck + fd_t / 2.0], [fd_len, fd_w, fd_t], _DECK_RGB, 0.0),
        _DECK_KIND_CLASS, "deck")
    ang = float(fd.get("angled_deck_deg", 9.0))
    if ang > 0.1:
        a_len = float(fd.get("angled_length_m", fd_len * 0.72))
        a_w = float(fd.get("angled_width_m", fd_w * 0.42))
        # canted strip: offset forward + to port, rotated by the cant angle about Z
        cx, cy = L * 0.08, fd_w * 0.20
        add(prim.box([cx, cy, z_deck + fd_t / 2.0], [a_len, a_w, fd_t], _DECK_RGB,
                     math.radians(ang)), _DECK_KIND_CLASS, "angled_deck")

    # ── internal decks (hollow hull only) → strike/penetration layers ──────────
    if not solid:
        dk = s.get("decks", {})
        n_dk = int(dk.get("count", 3))
        draft = float(hull.get("draft_m", float(hull.get("depth_m", 20)) * 0.4))
        for n in range(n_dk):
            f = (n + 1) / (n_dk + 1)
            zc = (-draft * 0.3) + f * (z_deck - (-draft * 0.3))
            add(prim.box([0.0, 0.0, zc], [L * 0.9, B * 0.86, 0.35], _ISLAND_RGB, 0.0),
                _DECK_KIND_CLASS, f"innerdeck{n}")

    # ── island superstructure (starboard, aft of midships) + bridge + mast ─────
    isl = s.get("island", {})
    if isl.get("enabled", True):
        il = float(isl.get("length_m", L * 0.10))
        iw = float(isl.get("width_m", B * 0.20))
        ih = float(isl.get("height_m", 14.0))
        i_x = float(isl.get("offset_fwd_m", -L * 0.06))          # aft of midships (−x)
        i_y = -abs(float(isl.get("offset_stbd_m", fd_w * 0.5 - iw)))   # starboard (−y)
        base = z_deck + fd_t
        add(prim.box([i_x, i_y, base + ih / 2.0], [il, iw, ih], _ISLAND_RGB, 0.0),
            _HULL_KIND_CLASS, "island")
        br = isl.get("bridge", {})
        bl = float(br.get("length_m", il * 0.5)); bw = float(br.get("width_m", iw * 1.15))
        bh = float(br.get("height_m", 4.0))
        add(prim.box([i_x + il * 0.1, i_y, base + ih + bh / 2.0], [bl, bw, bh], _ISLAND_RGB, 0.0),
            _HULL_KIND_CLASS, "bridge")
        mast_h = float(isl.get("mast_height_m", ih * 1.3))
        if mast_h > 0.1:
            add(prim.box([i_x - il * 0.15, i_y, base + ih + mast_h / 2.0],
                         [1.2, 1.2, mast_h], _MAST_RGB, 0.0), _HULL_KIND_CLASS, "mast")

    # ── sea plane (GLB only; excluded from voxels via the glb_only sentinel) ────
    water = s.get("water", {})
    if water.get("enabled", True):
        sf = float(water.get("size_factor", 3.0))
        rgb = tuple(water.get("rgb", _WATER_RGB))
        if len(rgb) == 3:
            rgb = rgb + (170,)
        add(prim.box([0.0, 0.0, -0.05], [L * sf, B * 2.0 * sf, 0.2], rgb, 0.0), _GLB_ONLY, "water")

    # ── place the whole ship: heading about Z, then move to origin at waterline ──
    T = np.eye(4)
    T[0, 3], T[1, 3], T[2, 3] = float(ox), float(oy), z_wl
    T = T @ _rz4(heading)
    for m in meshes:
        m.apply_transform(T)
    return meshes, kinds
