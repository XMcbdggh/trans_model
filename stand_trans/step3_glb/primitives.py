"""Mesh primitives for direct visual GLB export.

Coordinates are authored in Z-up building space. The final exporter rotates the
scene for Three.js-compatible Y-up display.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh


def color_mesh(mesh: trimesh.Trimesh, color) -> trimesh.Trimesh:
    mesh.visual.face_colors = np.array(color, dtype=np.uint8)
    return mesh


def box(center, size, color, angle=0.0) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=[max(size[0], 0.01), max(size[1], 0.01), max(size[2], 0.01)])
    mesh.apply_transform(_rot_z(angle))
    mesh.apply_translation(center)
    return color_mesh(mesh, color)


def oriented_box_from_start(start, length, depth, height, z, angle, color) -> trimesh.Trimesh:
    dx = math.cos(angle) * length / 2.0
    dy = math.sin(angle) * length / 2.0
    nx = -math.sin(angle) * depth / 2.0
    ny = math.cos(angle) * depth / 2.0
    center = [start[0] + dx + nx, start[1] + dy + ny, z + height / 2.0]
    return box(center, [length, depth, height], color, angle)


def cylinder(center, radius, height, color, sections=32) -> trimesh.Trimesh:
    mesh = trimesh.creation.cylinder(radius=max(radius, 0.01), height=max(height, 0.01), sections=sections)
    mesh.apply_translation([center[0], center[1], center[2] + height / 2.0])
    return color_mesh(mesh, color)


def ring_column(center, radius, height, color, accent, sections=32) -> list[trimesh.Trimesh]:
    meshes = [cylinder(center, radius, height, color, sections)]
    meshes.append(cylinder([center[0], center[1], center[2]], radius * 1.22, height * 0.08, accent, sections))
    meshes.append(cylinder([center[0], center[1], center[2] + height * 0.92], radius * 1.22, height * 0.08, accent, sections))
    return meshes


# ── 非建筑场景元素(树/车):返回的网格在体素 pitch 下可分辨(部件 ≥ ~1 体素)。
def palm_tree(base, height, trunk_r, canopy_r, trunk_color, foliage_color, fronds=7):
    """棕榈:细高树干(timber) + 顶冠放射状叶簇(foliage)。返回 (trunk_meshes, foliage_meshes)。"""
    cx, cy, z = base
    trunk_h = max(height * 0.82, 1.0)
    trunk = [cylinder([cx, cy, z], max(trunk_r, 0.18), trunk_h, trunk_color, sections=8)]
    crown_z = z + trunk_h
    fw = max(canopy_r * 0.45, 0.45)   # frond 截面(宽×厚),保证 ≥1 体素
    fol = [box([cx, cy, crown_z], [fw, fw, fw], foliage_color)]   # 顶冠核心
    for i in range(fronds):
        a = i * math.tau / fronds
        fx = cx + math.cos(a) * canopy_r * 0.5
        fy = cy + math.sin(a) * canopy_r * 0.5
        fol.append(box([fx, fy, crown_z - canopy_r * 0.12],
                       [max(canopy_r, 1.0), fw, fw], foliage_color, a))
    return trunk, fol


def cypress_tree(base, height, trunk_r, canopy_r, trunk_color, foliage_color, tiers=4):
    """柏树/塔松:短树干 + 锥形叶体(逐层收窄)。返回 (trunk_meshes, foliage_meshes)。"""
    cx, cy, z = base
    trunk_h = max(height * 0.18, 0.6)
    trunk = [cylinder([cx, cy, z], max(trunk_r, 0.15), trunk_h, trunk_color, sections=8)]
    fol = []
    fz = z + trunk_h
    foliage_h = max(height - trunk_h, 1.0)
    seg_h = foliage_h / tiers
    for i in range(tiers):
        r = max(canopy_r * (1.0 - i / tiers) * 0.9, 0.4)
        fol.append(cylinder([cx, cy, fz + i * seg_h], r, seg_h * 1.06, foliage_color, sections=10))
    return trunk, fol


def vehicle(base, length, width, height, color, angle=0.0, kind="car"):
    """车辆(单色 vehicle_body)。kind: car/truck=车体+驾驶舱;tank/afv/ifv=装甲战车
    (低矮车体 + 炮塔 + 沿 heading 的炮管);equipment=低矮设备/物资箱。返回 mesh 列表。"""
    cx, cy, z = base
    if kind in ("tank", "afv", "ifv"):
        hull_h = max(height * 0.5, 0.5)
        meshes = [box([cx, cy, z + hull_h / 2.0], [length, width, hull_h], color, angle)]
        tur_h = max(height - hull_h, 0.5)
        meshes.append(cylinder([cx, cy, z + hull_h], max(width * 0.34, 0.5), tur_h, color, sections=10))
        bl = length * 0.6   # 炮管沿 heading 伸出
        bx = cx + math.cos(angle) * (length / 2.0 + bl / 2.0) * 0.7
        by = cy + math.sin(angle) * (length / 2.0 + bl / 2.0) * 0.7
        meshes.append(box([bx, by, z + hull_h + tur_h * 0.4],
                          [bl, max(width * 0.16, 0.45), max(width * 0.16, 0.45)], color, angle))
        return meshes
    if kind == "equipment":
        return [box([cx, cy, z + max(height * 0.6, 0.4) / 2.0],
                    [length, width, max(height * 0.6, 0.4)], color, angle)]
    body_h = max(height * 0.55, 0.4)
    cabin_h = max(height - body_h, 0.4)
    chassis = box([cx, cy, z + body_h / 2.0], [length, width, body_h], color, angle)
    cabin = box([cx, cy, z + body_h + cabin_h / 2.0], [length * 0.5, width * 0.92, cabin_h], color, angle)
    return [chassis, cabin]


def frame(center, width, height, depth, thickness, color, angle=0.0, sill=0.0) -> list[trimesh.Trimesh]:
    x, y, z = center[0], center[1], center[2] + sill
    pieces = [
        _local_box(center, [-width / 2 + thickness / 2, 0, sill + height / 2], thickness, depth, height, angle, color),
        _local_box(center, [width / 2 - thickness / 2, 0, sill + height / 2], thickness, depth, height, angle, color),
        _local_box(center, [0, 0, sill + thickness / 2], width, depth, thickness, angle, color),
        _local_box(center, [0, 0, sill + height - thickness / 2], width, depth, thickness, angle, color),
    ]
    return pieces


def grid_frame(center, width, height, depth, thickness, color, angle=0.0, verticals=2, horizontals=2) -> list[trimesh.Trimesh]:
    meshes = frame(center, width, height, depth, thickness, color, angle)
    x, y, z = center
    for i in range(1, verticals + 1):
        off = -width / 2 + width * i / (verticals + 1)
        meshes.append(_local_box(center, [off, 0, height / 2], thickness * 0.65, depth, height, angle, color))
    for i in range(1, horizontals + 1):
        off = height * i / (horizontals + 1)
        meshes.append(_local_box(center, [0, 0, off], width, depth, thickness * 0.65, angle, color))
    return meshes


def pointed_arch_panel(center, width, height, depth, color, angle=0.0, base_height_ratio=0.58) -> trimesh.Trimesh:
    pts = _pointed_arch_points(width, height, base_height_ratio)
    return extrude_local_polygon(pts, center, depth, angle, color)


def horseshoe_arch_panel(center, width, height, depth, color, angle=0.0) -> trimesh.Trimesh:
    pts = []
    spring = height * 0.55
    pts.extend([[-width / 2, 0], [width / 2, 0], [width / 2, spring]])
    radius = width * 0.56
    cx, cz = 0.0, spring
    for i in range(18):
        theta = math.radians(15 + 150 * i / 17)
        pts.append([math.cos(theta) * radius + cx, math.sin(theta) * radius + cz])
    pts.append([-width / 2, spring])
    return extrude_local_polygon(pts, center, depth, angle, color)


def round_arch_panel(center, width, height, depth, color, angle=0.0) -> trimesh.Trimesh:
    """Semicircular (round / Roman) arch: rectangular base + half-circle top (radius = width/2)."""
    radius = width / 2.0
    spring = max(0.0, height - radius)          # height of the straight jambs; semicircle sits on top
    pts = [[-width / 2, 0.0], [width / 2, 0.0]]
    segs = 20
    for i in range(segs + 1):
        theta = math.pi * i / segs              # 0 (right jamb) -> pi (left jamb), apex at pi/2
        pts.append([math.cos(theta) * radius, spring + math.sin(theta) * radius])
    return extrude_local_polygon(pts, center, depth, angle, color)


def dome(center, radius, height, color, segments=32, rings=10) -> trimesh.Trimesh:
    vertices = []
    faces = []
    for r in range(rings + 1):
        v = r / rings
        phi = v * math.pi / 2
        rr = math.cos(phi) * radius
        z = math.sin(phi) * height
        for s in range(segments):
            theta = s * math.tau / segments
            vertices.append([center[0] + math.cos(theta) * rr, center[1] + math.sin(theta) * rr, center[2] + z])
    for r in range(rings):
        for s in range(segments):
            a = r * segments + s
            b = r * segments + (s + 1) % segments
            c = (r + 1) * segments + (s + 1) % segments
            d = (r + 1) * segments + s
            faces.append([a, b, c])
            faces.append([a, c, d])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    return color_mesh(mesh, color)


def barrel_vault(center, length, radius, depth, color, angle=0.0, segments=18) -> trimesh.Trimesh:
    pts = [[-length / 2, 0], [length / 2, 0]]
    for i in range(segments + 1):
        theta = math.pi * i / segments
        pts.append([length / 2 - length * i / segments, math.sin(theta) * radius])
    return extrude_local_polygon(pts, center, depth, angle, color)


def extrude_local_polygon(points_xz, center, depth, angle, color) -> trimesh.Trimesh:
    verts = []
    for y in (-depth / 2, depth / 2):
        for x, z in points_xz:
            verts.append(_transform_local([x, y, z], center, angle))
    n = len(points_xz)
    faces = []
    for i in range(1, n - 1):
        faces.append([0, i, i + 1])
        faces.append([n, n + i + 1, n + i])
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j])
        faces.append([i, n + j, n + i])
    return color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color)


def _pointed_arch_points(width, height, base_ratio):
    spring = height * base_ratio
    pts = [[-width / 2, 0], [width / 2, 0], [width / 2, spring]]
    for i in range(1, 12):
        t = i / 12
        x = width / 2 * (1 - t)
        z = spring + (height - spring) * math.sin(t * math.pi / 2)
        pts.append([x, z])
    for i in range(1, 12):
        t = i / 12
        x = -width / 2 * t
        z = height - (height - spring) * (1 - math.cos(t * math.pi / 2))
        pts.append([x, z])
    pts.append([-width / 2, spring])
    return pts


def _local_box(center, local_center, sx, sy, sz, angle, color):
    return box(_transform_local(local_center, center, angle), [sx, sy, sz], color, angle)


def _transform_local(p, center, angle):
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = p
    return [center[0] + c * x - s * y, center[1] + s * x + c * y, center[2] + z]


def _rot_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    m = np.eye(4)
    m[0, 0] = c
    m[0, 1] = -s
    m[1, 0] = s
    m[1, 1] = c
    return m


def gable_roof(x0, y0, x1, y1, eave_z, ridge_z, ridge_along_x, color, overhang=0.3) -> list[trimesh.Trimesh]:
    """Gable roof with ridge running along X (ridge_along_x=True) or Y."""
    ox0, oy0, ox1, oy1 = x0 - overhang, y0 - overhang, x1 + overhang, y1 + overhang
    meshes = []
    if ridge_along_x:
        ridge_y = (oy0 + oy1) / 2.0
        verts = np.array([
            [ox0, oy0, eave_z], [ox1, oy0, eave_z],
            [ox1, ridge_y, ridge_z], [ox0, ridge_y, ridge_z],
            [ox0, ridge_y, ridge_z], [ox1, ridge_y, ridge_z],
            [ox1, oy1, eave_z], [ox0, oy1, eave_z],
        ])
        faces = [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]
        roof = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        meshes.append(color_mesh(roof, color))
        # gable end walls (triangles)
        for yy in (oy0, oy1):
            v = np.array([[ox0, yy, eave_z], [ox1, yy, eave_z], [(ox0 + ox1) / 2, yy, ridge_z]])
            f = [[0, 1, 2]] if yy == oy0 else [[2, 1, 0]]
            meshes.append(color_mesh(trimesh.Trimesh(vertices=v, faces=f, process=False), color))
    else:
        ridge_x = (ox0 + ox1) / 2.0
        verts = np.array([
            [ox0, oy0, eave_z], [ridge_x, oy0, ridge_z],
            [ridge_x, oy1, ridge_z], [ox0, oy1, eave_z],
            [ridge_x, oy0, ridge_z], [ox1, oy0, eave_z],
            [ox1, oy1, eave_z], [ridge_x, oy1, ridge_z],
        ])
        faces = [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]
        meshes.append(color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color))
        for xx in (ox0, ox1):
            v = np.array([[xx, oy0, eave_z], [xx, oy1, eave_z], [xx, (oy0 + oy1) / 2, ridge_z]])
            f = [[0, 1, 2]] if xx == ox0 else [[2, 1, 0]]
            meshes.append(color_mesh(trimesh.Trimesh(vertices=v, faces=f, process=False), color))
    return meshes


def hip_roof(x0, y0, x1, y1, eave_z, ridge_z, color, overhang=0.3) -> list[trimesh.Trimesh]:
    """4-sloped hip roof with ridge along the longer axis."""
    ox0, oy0, ox1, oy1 = x0 - overhang, y0 - overhang, x1 + overhang, y1 + overhang
    w, d = ox1 - ox0, oy1 - oy0
    inset = min(w, d) / 2.0
    if w >= d:
        r0 = np.array([ox0 + inset, (oy0 + oy1) / 2, ridge_z])
        r1 = np.array([ox1 - inset, (oy0 + oy1) / 2, ridge_z])
    else:
        r0 = np.array([(ox0 + ox1) / 2, oy0 + inset, ridge_z])
        r1 = np.array([(ox0 + ox1) / 2, oy1 - inset, ridge_z])
    c0 = np.array([ox0, oy0, eave_z])
    c1 = np.array([ox1, oy0, eave_z])
    c2 = np.array([ox1, oy1, eave_z])
    c3 = np.array([ox0, oy1, eave_z])
    verts = np.array([c0, c1, c2, c3, r0, r1])
    if w >= d:
        faces = [[0, 1, 5], [0, 5, 4], [1, 2, 5], [2, 3, 4], [2, 4, 5], [3, 0, 4]]
    else:
        faces = [[0, 1, 4], [1, 5, 4], [1, 2, 5], [2, 3, 5], [3, 4, 5], [3, 0, 4]]
    return [color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color)]


def pyramidal_roof(x0, y0, x1, y1, eave_z, apex_z, color, overhang=0.3) -> list[trimesh.Trimesh]:
    ox0, oy0, ox1, oy1 = x0 - overhang, y0 - overhang, x1 + overhang, y1 + overhang
    apex = np.array([(ox0 + ox1) / 2.0, (oy0 + oy1) / 2.0, apex_z])
    verts = np.array([[ox0, oy0, eave_z], [ox1, oy0, eave_z], [ox1, oy1, eave_z], [ox0, oy1, eave_z], apex])
    faces = [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]]
    return [color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color)]


def tent_dome(center, radius, height, color, sides=12) -> trimesh.Trimesh:
    """Polygonal pyramidal cap with N sides (Persian tent / pavilion dome)."""
    verts = []
    apex = [center[0], center[1], center[2] + height]
    for i in range(sides):
        a = i * math.tau / sides
        verts.append([center[0] + math.cos(a) * radius, center[1] + math.sin(a) * radius, center[2]])
    verts.append(apex)
    faces = []
    apex_i = len(verts) - 1
    for i in range(sides):
        j = (i + 1) % sides
        faces.append([i, j, apex_i])
    return color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color)


def onion_dome(center, base_radius, height, color, segments=32, rings=16, neck_ratio=0.85, bulge_ratio=1.18) -> trimesh.Trimesh:
    """Persian onion dome profile: cylindrical neck + bulging belly + pointed top."""
    profile = []
    for i in range(rings + 1):
        t = i / rings
        if t < 0.18:
            r_factor = neck_ratio
            z_factor = t / 0.18 * 0.12
        elif t < 0.62:
            local = (t - 0.18) / 0.44
            r_factor = neck_ratio + (bulge_ratio - neck_ratio) * math.sin(local * math.pi)
            z_factor = 0.12 + local * 0.45
        elif t < 0.88:
            local = (t - 0.62) / 0.26
            r_factor = bulge_ratio * (1 - local) + 0.18 * local
            z_factor = 0.57 + local * 0.32
        else:
            local = (t - 0.88) / 0.12
            r_factor = 0.18 * (1 - local)
            z_factor = 0.89 + local * 0.11
        profile.append((r_factor * base_radius, z_factor * height))
    return _revolve_profile(profile, center, color, segments)


def _revolve_profile(profile_rz, center, color, segments=32) -> trimesh.Trimesh:
    verts = []
    for r, z in profile_rz:
        for s in range(segments):
            a = s * math.tau / segments
            verts.append([center[0] + math.cos(a) * r, center[1] + math.sin(a) * r, center[2] + z])
    faces = []
    n = len(profile_rz)
    for ring in range(n - 1):
        for s in range(segments):
            a = ring * segments + s
            b = ring * segments + (s + 1) % segments
            c = (ring + 1) * segments + (s + 1) % segments
            d = (ring + 1) * segments + s
            faces.append([a, b, c])
            faces.append([a, c, d])
    return color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color)


def revolve_profile(profile_rz, center, color, segments=32):
    """Public wrapper for revolution-of-radius profile (list of (r, z) tuples)."""
    return _revolve_profile(profile_rz, center, color, segments)


def apply_cylindrical_texture(mesh, image, center_xy, repeat_u=1.0, repeat_v=1.0):
    """Wrap `mesh` with `image` via cylindrical UV projection around `center_xy`."""
    verts = mesh.vertices
    if len(verts) == 0:
        return mesh
    zmin, zmax = verts[:, 2].min(), verts[:, 2].max()
    zh = max(zmax - zmin, 1e-6)
    cx, cy = center_xy
    uvs = np.zeros((len(verts), 2))
    for i, v in enumerate(verts):
        theta = math.atan2(v[1] - cy, v[0] - cx)
        uvs[i, 0] = ((theta + math.pi) / (2.0 * math.pi)) * repeat_u
        uvs[i, 1] = ((v[2] - zmin) / zh) * repeat_v
    mesh.visual = trimesh.visual.TextureVisuals(uv=uvs, image=image)
    return mesh


def apply_planar_texture(mesh, image, axis="xz", repeat_u=1.0, repeat_v=1.0):
    """Project `image` onto `mesh` using axis-aligned planar UV projection."""
    verts = mesh.vertices
    if len(verts) == 0:
        return mesh
    if axis == "xz":
        u_min, u_max = verts[:, 0].min(), verts[:, 0].max()
        v_min, v_max = verts[:, 2].min(), verts[:, 2].max()
        u_co, v_co = 0, 2
    elif axis == "yz":
        u_min, u_max = verts[:, 1].min(), verts[:, 1].max()
        v_min, v_max = verts[:, 2].min(), verts[:, 2].max()
        u_co, v_co = 1, 2
    else:
        u_min, u_max = verts[:, 0].min(), verts[:, 0].max()
        v_min, v_max = verts[:, 1].min(), verts[:, 1].max()
        u_co, v_co = 0, 1
    uw = max(u_max - u_min, 1e-6)
    vh = max(v_max - v_min, 1e-6)
    uvs = np.zeros((len(verts), 2))
    for i, v in enumerate(verts):
        uvs[i, 0] = (v[u_co] - u_min) / uw * repeat_u
        uvs[i, 1] = (v[v_co] - v_min) / vh * repeat_v
    mesh.visual = trimesh.visual.TextureVisuals(uv=uvs, image=image)
    return mesh


def fluted_column(center, radius, height, color, accent, flute_count=20,
                  capital_style="bell") -> list[trimesh.Trimesh]:
    """Persepolis / apadana style: stepped base, fluted tapering shaft, bell capital."""
    meshes = []
    cx, cy, cz = center
    base_h = max(height * 0.045, 0.18)
    plinth_h = base_h * 0.55
    meshes.append(box([cx, cy, cz + plinth_h / 2], [radius * 2.7, radius * 2.7, plinth_h], accent))
    torus1_h = base_h * 0.28
    meshes.append(cylinder([cx, cy, cz + plinth_h], radius * 1.35, torus1_h, accent, sections=24))
    torus2_h = base_h * 0.18
    meshes.append(cylinder([cx, cy, cz + plinth_h + torus1_h], radius * 1.18, torus2_h, accent, sections=24))

    shaft_z0 = cz + plinth_h + torus1_h + torus2_h
    capital_h = max(height * 0.13, 0.35)
    neck_h = max(height * 0.02, 0.06)
    shaft_h = height - (shaft_z0 - cz) - capital_h - neck_h
    if shaft_h <= 0:
        return meshes
    meshes.append(_fluted_shaft(center, shaft_z0, shaft_h, radius, flute_count, color))

    neck_z = shaft_z0 + shaft_h
    meshes.append(cylinder([cx, cy, neck_z], radius * 0.96, neck_h, accent, sections=24))

    capital_z = neck_z + neck_h
    if capital_style == "bull_protome":
        meshes.extend(_bull_protome_capital(center, capital_z, capital_h, radius, color, accent))
    elif capital_style == "lotus":
        meshes.extend(_lotus_capital(center, capital_z, capital_h, radius, color, accent))
    else:
        meshes.extend(_bell_capital(center, capital_z, capital_h, radius, color, accent))
    return meshes


def _fluted_shaft(center, z0, height, base_radius, flute_count, color,
                  rings=10, segs_per_flute=4):
    segments = max(flute_count * segs_per_flute, 16)
    flute_depth_ratio = 0.08
    verts = []
    for r in range(rings + 1):
        v = r / rings
        taper = 1.0 - 0.10 * v
        z = z0 + v * height
        for s in range(segments):
            theta = s * math.tau / segments
            r_mod = 1.0 - flute_depth_ratio * (1.0 - math.cos(flute_count * theta)) / 2.0
            r_local = base_radius * taper * r_mod
            verts.append([center[0] + math.cos(theta) * r_local,
                          center[1] + math.sin(theta) * r_local, z])
    faces = []
    for r in range(rings):
        for s in range(segments):
            a = r * segments + s
            b = r * segments + (s + 1) % segments
            c = (r + 1) * segments + (s + 1) % segments
            d = (r + 1) * segments + s
            faces.append([a, b, c])
            faces.append([a, c, d])
    return color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color)


def _bell_capital(center, z0, height, radius, color, accent):
    profile = []
    for i in range(18):
        t = i / 17
        if t < 0.35:
            r = radius * (0.90 + t * 0.95)
            z = t / 0.35 * 0.35 * height
        elif t < 0.78:
            local = (t - 0.35) / 0.43
            r = radius * (1.23 + 0.55 * math.sin(local * math.pi))
            z = (0.35 + local * 0.35) * height
        else:
            local = (t - 0.78) / 0.22
            r = radius * (1.78 - 0.42 * local)
            z = (0.70 + local * 0.20) * height
        profile.append((r, z))
    bell = _revolve_profile(profile, [center[0], center[1], z0], color, segments=32)
    abacus_h = max(height * 0.18, 0.08)
    abacus = box([center[0], center[1], z0 + height - abacus_h / 2],
                 [radius * 3.1, radius * 3.1, abacus_h], accent)
    return [bell, abacus]


def _lotus_capital(center, z0, height, radius, color, accent):
    profile = []
    for i in range(14):
        t = i / 13
        r = radius * (1.05 + 0.85 * math.sin(t * math.pi * 0.78))
        z = t * height * 0.85
        profile.append((r, z))
    lotus = _revolve_profile(profile, [center[0], center[1], z0], color, segments=32)
    abacus_h = max(height * 0.15, 0.06)
    abacus = box([center[0], center[1], z0 + height - abacus_h / 2],
                 [radius * 2.6, radius * 2.6, abacus_h], accent)
    return [lotus, abacus]


def muqarnas_portal(center, width, height, depth, color, accent,
                    tiers=4, cells_base=10, half=True) -> list[trimesh.Trimesh]:
    """Simplified muqarnas: stalactite-cell tiers stepping inward and upward.
    `half=True` arranges cells on a half-circle (suitable for an iwan or niche soffit)."""
    cx, cy, cz = center
    meshes = []
    tier_h = height / tiers
    angular_span = math.pi if half else math.tau
    radius_top = width / 2.0
    for i in range(tiers):
        progress = i / max(tiers - 1, 1)
        r = radius_top * (1.0 - progress * 0.55)
        z = cz + i * tier_h
        n_cells = cells_base + i * 3
        cell_w = (angular_span * r) / max(n_cells, 1)
        # Shelf at this tier (ring segment)
        shelf_segments = max(n_cells * 2, 12)
        ring = _ring_arc(cx, cy, z, r, depth * 0.55, tier_h * 0.18,
                         -angular_span / 2.0, angular_span / 2.0, shelf_segments, accent)
        if ring is not None:
            meshes.append(ring)
        # Cells around this tier
        for j in range(n_cells):
            stagger = (i % 2) * 0.5
            t = (j + 0.5 + stagger) / n_cells
            theta = -angular_span / 2.0 + angular_span * t
            cxe = cx + math.cos(theta) * r
            cye = cy + math.sin(theta) * r
            cze = z + tier_h * 0.65
            niche_radius = min(cell_w * 0.55, tier_h * 0.55)
            niche = trimesh.creation.icosphere(radius=max(niche_radius, 0.05), subdivisions=1)
            niche.apply_translation([cxe, cye, cze])
            meshes.append(color_mesh(niche, color))
            # Small drop point under the niche (stalactite tip pointing down).
            # Flip cone in its LOCAL frame first, then translate, so the apex
            # ends up below the niche centre rather than below the world origin.
            tip_h = tier_h * 0.55
            tip = trimesh.creation.cone(radius=niche_radius * 0.5, height=tip_h, sections=10)
            flip = np.eye(4)
            flip[2, 2] = -1
            tip.apply_transform(flip)              # apex at z = -tip_h, base at z = 0
            tip.apply_translation([cxe, cye, cze])  # base at niche centre, apex hangs below
            meshes.append(color_mesh(tip, color))
    # Top cap (small inverted cone "lantern" at the muqarnas apex)
    cap_h = tier_h * 0.9
    cap = trimesh.creation.cone(radius=radius_top * 0.42, height=cap_h, sections=24)
    flip = np.eye(4)
    flip[2, 2] = -1
    cap.apply_transform(flip)                 # apex below origin
    cap.apply_translation([cx, cy, cz + height])  # base at muqarnas apex, point hangs below
    meshes.append(color_mesh(cap, accent))
    return meshes


def _ring_arc(cx, cy, z, radius, depth, height, theta0, theta1, segments, color):
    """Extruded ring-arc (a partial torus approximated as a band of trapezoids)."""
    inner = max(radius - depth, 0.02)
    outer = radius + depth * 0.1
    verts = []
    for s in range(segments + 1):
        theta = theta0 + (theta1 - theta0) * s / segments
        cs, sn = math.cos(theta), math.sin(theta)
        verts.append([cx + cs * outer, cy + sn * outer, z])
        verts.append([cx + cs * inner, cy + sn * inner, z])
        verts.append([cx + cs * outer, cy + sn * outer, z + height])
        verts.append([cx + cs * inner, cy + sn * inner, z + height])
    faces = []
    for s in range(segments):
        a = s * 4
        faces.extend([
            [a, a + 4, a + 5], [a, a + 5, a + 1],
            [a + 2, a + 3, a + 7], [a + 2, a + 7, a + 6],
            [a, a + 2, a + 6], [a, a + 6, a + 4],
            [a + 1, a + 5, a + 7], [a + 1, a + 7, a + 3],
        ])
    if not faces:
        return None
    return color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color)


def dome_pendentives(center, square_size, top_z, top_radius, color, segments=10) -> list[trimesh.Trimesh]:
    """Generate 4 spherical-triangle pendentives bridging a square base to an inscribed
    circle at top_z. `center` (cx, cy, base_z) is the square centroid at base."""
    cx, cy, base_z = center
    s = square_size / 2.0
    corners = [
        (cx - s, cy - s, base_z),
        (cx + s, cy - s, base_z),
        (cx + s, cy + s, base_z),
        (cx - s, cy + s, base_z),
    ]
    meshes = []
    for i in range(4):
        a = corners[i]
        b = corners[(i + 1) % 4]
        mid_x = (a[0] + b[0]) / 2.0
        mid_y = (a[1] + b[1]) / 2.0
        dir_x = mid_x - cx
        dir_y = mid_y - cy
        norm = math.hypot(dir_x, dir_y) or 1.0
        base_angle = math.atan2(dir_y, dir_x)
        verts = []
        for u in range(segments + 1):
            tu = u / segments
            z = base_z + tu * (top_z - base_z)
            bulge = math.sin(tu * math.pi) * 0.06 * top_radius
            for v in range(segments + 1):
                tv = v / segments
                arc_angle = base_angle - math.pi / 4 + tv * math.pi / 2
                tx = cx + math.cos(arc_angle) * top_radius
                ty = cy + math.sin(arc_angle) * top_radius
                bx = a[0] * (1.0 - tv) + b[0] * tv
                by = a[1] * (1.0 - tv) + b[1] * tv
                x = bx * (1.0 - tu) + tx * tu - (dir_x / norm) * bulge
                y = by * (1.0 - tu) + ty * tu - (dir_y / norm) * bulge
                verts.append([x, y, z])
        faces = []
        n = segments + 1
        for u in range(segments):
            for v in range(segments):
                ai = u * n + v
                bi = u * n + v + 1
                ci = (u + 1) * n + v + 1
                di = (u + 1) * n + v
                faces.append([ai, bi, ci])
                faces.append([ai, ci, di])
        meshes.append(color_mesh(trimesh.Trimesh(vertices=verts, faces=faces, process=False), color))
    return meshes


def dome_drum(center, radius, height, color, sides=None) -> trimesh.Trimesh:
    """Cylindrical drum supporting the dome shell."""
    return cylinder(center, radius, height, color, sections=sides or 32)


def dome_finial(center, height, base_radius, color_outer, color_accent) -> list[trimesh.Trimesh]:
    """Stepped finial: short cylindrical neck + bulb + tall spire + tiny ball at tip."""
    cx, cy, cz = center
    meshes = []
    neck_h = height * 0.18
    meshes.append(cylinder([cx, cy, cz], base_radius * 1.05, neck_h, color_outer, sections=24))
    bulb_h = height * 0.24
    bulb_center = [cx, cy, cz + neck_h + bulb_h / 2]
    meshes.append(dome(bulb_center, base_radius * 1.35, bulb_h * 1.4, color_accent, segments=24, rings=10))
    spire_h = height * 0.45
    spire_z = cz + neck_h + bulb_h
    spire = trimesh.creation.cone(radius=base_radius * 0.75, height=spire_h, sections=18)
    spire.apply_translation([cx, cy, spire_z])
    meshes.append(color_mesh(spire, color_outer))
    tip_z = spire_z + spire_h
    tip = trimesh.creation.icosphere(radius=base_radius * 0.45, subdivisions=2)
    tip.apply_translation([cx, cy, tip_z + base_radius * 0.45])
    meshes.append(color_mesh(tip, color_accent))
    final_h = height * 0.13
    crescent = trimesh.creation.cone(radius=base_radius * 0.18, height=final_h, sections=12)
    crescent.apply_translation([cx, cy, tip_z + base_radius * 0.9])
    meshes.append(color_mesh(crescent, color_accent))
    return meshes


def _bull_protome_capital(center, z0, height, radius, color, accent):
    """Abstract Persepolis bull-protome silhouette: two stylised heads back-to-back."""
    meshes = _bell_capital(center, z0, height * 0.55, radius, color, accent)
    head_z = z0 + height * 0.55
    head_h = height * 0.45
    head_l = radius * 3.2
    head_w = radius * 1.05
    head_d = radius * 1.1
    for sign in (-1.0, 1.0):
        cx = center[0] + sign * head_l * 0.32
        head_box = box([cx, center[1], head_z + head_h * 0.55],
                       [head_l * 0.5, head_w, head_h * 0.7], color)
        snout = box([cx + sign * head_l * 0.32, center[1], head_z + head_h * 0.35],
                    [head_l * 0.22, head_w * 0.72, head_h * 0.4], color)
        horn = box([cx, center[1], head_z + head_h * 0.95],
                   [head_l * 0.18, head_w * 0.35, head_h * 0.5], accent)
        meshes.extend([head_box, snout, horn])
    return meshes
