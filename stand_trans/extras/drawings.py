"""2D drawing extraction: floor plans + cardinal elevations as SVG.

Coordinates in the BIM model use mathematical (Y up). SVG uses screen Y (down).
We flip via svg_y = -building_y on emission so downstream tools render correctly
without depending on transform groups.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable


SVG_STYLE = """
.bg          { fill: #ffffff; }
.wall        { fill: #1a1a1a; stroke: none; }
.column      { fill: #1a1a1a; stroke: none; }
.opening     { fill: #ffffff; stroke: #1a1a1a; stroke-width: 0.04; }
.opening-line{ stroke: #1a1a1a; stroke-width: 0.04; fill: none; }
.window-line { stroke: #1a1a1a; stroke-width: 0.03; fill: none; }
.swing       { stroke: #555555; stroke-width: 0.03; fill: none; stroke-dasharray: 0.2 0.1; }
.stair       { stroke: #1a1a1a; stroke-width: 0.05; fill: none; }
.space-poly  { fill: #f4f0e6; stroke: #c8c0a8; stroke-width: 0.03; }
.label       { font-family: sans-serif; font-size: 0.50px; fill: #333333; text-anchor: middle; }
.label-area  { font-family: sans-serif; font-size: 0.36px; fill: #777777; text-anchor: middle; }
.title       { font-family: sans-serif; font-size: 1.40px; fill: #111111; font-weight: bold; }
.subtitle    { font-family: sans-serif; font-size: 0.80px; fill: #444444; }
.level-line  { stroke: #888888; stroke-width: 0.04; fill: none; stroke-dasharray: 0.4 0.2; }
.level-label { font-family: sans-serif; font-size: 0.40px; fill: #555555; }
.scale-bar   { stroke: #111111; stroke-width: 0.10; fill: none; }
.scale-text  { font-family: sans-serif; font-size: 0.35px; fill: #111111; }
.outline     { stroke: #1a1a1a; stroke-width: 0.06; fill: none; }
.facade-fill { fill: #f6f1e2; stroke: none; }
.roof-fill   { fill: #b9774d; stroke: #6a3e1f; stroke-width: 0.04; }
"""


def render_plan(bim: dict, level_name: str, out_path: str | Path, project_name: str = "") -> None:
    level = next((lv for lv in bim["levels"] if lv["name"] == level_name), None)
    if level is None:
        return
    walls = [e for e in bim["elements"]
             if e["type"] == "Wall" and e["level"] == level_name]
    columns = [e for e in bim["elements"]
               if e["type"] == "Column" and e["level"] == level_name]
    doors = [e for e in bim["elements"]
             if e["type"] == "Door" and e["level"] == level_name]
    windows = [e for e in bim["elements"]
               if e["type"] == "Window" and e["level"] == level_name]
    stairs = [e for e in bim["elements"]
              if e["type"] == "Stair" and e["level"] == level_name]
    spaces = [e for e in bim["elements"]
              if e["type"] == "Space" and e["level"] == level_name]

    pts = _collect_plan_points(walls, columns, spaces)
    if not pts:
        return
    x_min, y_min, x_max, y_max = _bounds(pts, margin=2.5)
    width = x_max - x_min
    height = y_max - y_min + 6.0  # extra room for title block

    body: list[str] = []

    # Space polygons (drawn first as background fill)
    for sp in spaces:
        poly = sp["geometry"]["profile"]
        body.append(_polygon(poly, "space-poly"))

    # Walls
    for w in walls:
        body.append(_polygon(_wall_corners(w), "wall"))

    # Columns
    for c in columns:
        ctr = c["geometry"]["center"]
        if c["geometry"].get("shape") == "circle":
            r = float(c["geometry"]["radius"])
            body.append(f'<circle class="column" cx="{ctr[0]:.3f}" cy="{-ctr[1]:.3f}" r="{r:.3f}"/>')
        else:
            sz = c["geometry"].get("size", [0.4, 0.4])
            x0 = ctr[0] - sz[0] / 2.0
            y0 = ctr[1] - sz[1] / 2.0
            body.append(f'<rect class="column" x="{x0:.3f}" y="{-y0 - sz[1]:.3f}" '
                        f'width="{sz[0]:.3f}" height="{sz[1]:.3f}"/>')

    # Openings (cut through wall)
    walls_by_id = {w["id"]: w for w in walls}
    for d in doors:
        body.extend(_door_plan(d, walls_by_id))
    for win in windows:
        body.extend(_window_plan(win, walls_by_id))

    # Stairs
    for s in stairs:
        body.append(_stair_plan(s))

    # Space labels
    for sp in spaces:
        poly = sp["geometry"]["profile"]
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        name = sp["geometry"].get("name", sp["id"])
        area = _polygon_area(poly)
        body.append(f'<text class="label" x="{cx:.2f}" y="{-cy:.2f}">{_escape(name)}</text>')
        body.append(f'<text class="label-area" x="{cx:.2f}" y="{-cy + 0.6:.2f}">{area:.1f} m²</text>')

    # Title block (in plan coordinates, below the drawing)
    title_y = -y_max - 1.5
    body.append(f'<text class="title" x="{x_min:.2f}" y="{title_y:.2f}">'
                f'{_escape(project_name or bim["project"]["name"])} — Plan {_escape(level_name)}</text>')
    body.append(f'<text class="subtitle" x="{x_min:.2f}" y="{title_y + 1.0:.2f}">'
                f'Elevation: {level["elevation_m"]:.2f} m   Height: {level["height_m"]:.2f} m</text>')
    body.append(_scale_bar(x_min + 0.5, -y_min - 1.5, length=5.0))
    body.append(_north_arrow(x_max - 1.5, -y_max - 1.5))

    svg = _svg_wrap(body, x_min, -y_max - 3.5, width, height)
    Path(out_path).write_text(svg, encoding="utf-8")


def render_elevation(bim: dict, direction: str, out_path: str | Path,
                     project_name: str = "") -> None:
    """direction ∈ {'N','E','S','W'}: which cardinal face we are looking AT."""
    assert direction in ("N", "E", "S", "W")
    walls = [e for e in bim["elements"] if e["type"] == "Wall"]
    doors = [e for e in bim["elements"] if e["type"] == "Door"]
    windows = [e for e in bim["elements"] if e["type"] == "Window"]
    roofs = [e for e in bim["elements"] if e["type"] == "Roof"]
    levels = sorted(bim["levels"], key=lambda lv: float(lv["elevation_m"]))

    extents = _building_extents(bim)
    if not extents:
        return
    x_min, y_min, x_max, y_max = extents
    z_min = min(float(lv["elevation_m"]) for lv in levels) - 1.0
    z_max = max(float(lv["elevation_m"]) + float(lv["height_m"]) for lv in levels) + 4.0

    # Decide the horizontal axis of the elevation
    if direction in ("N", "S"):
        u_min, u_max = x_min, x_max
    else:
        u_min, u_max = y_min, y_max
    u_min -= 2.0
    u_max += 2.0
    margin = 2.0
    width = (u_max - u_min) + 2 * margin
    height = (z_max - z_min) + 6.0
    body: list[str] = []

    # Ground line and facade fill
    body.append(f'<rect class="facade-fill" x="{u_min:.2f}" y="{-z_max:.2f}" '
                f'width="{u_max - u_min:.2f}" height="{z_max - 0:.2f}"/>')

    # Walls facing this direction
    for w in walls:
        if not _wall_faces(w, direction):
            continue
        a, b = w["geometry"]["start"], w["geometry"]["end"]
        u0, u1 = _project_axis(a, direction), _project_axis(b, direction)
        u_lo, u_hi = min(u0, u1), max(u0, u1)
        lv = next((l for l in levels if l["name"] == w["level"]), None)
        if lv is None:
            continue
        z0 = float(lv["elevation_m"])
        z1 = z0 + float(w["geometry"].get("height", lv["height_m"]))
        body.append(f'<rect class="outline" x="{u_lo:.2f}" y="{-z1:.2f}" '
                    f'width="{u_hi - u_lo:.2f}" height="{z1 - z0:.2f}"/>')

    walls_by_id = {w["id"]: w for w in walls}
    # Openings
    for op_list, cls in ((doors, "opening"), (windows, "opening")):
        for op in op_list:
            host = walls_by_id.get(op["geometry"].get("host_id"))
            if host is None or not _wall_faces(host, direction):
                continue
            u_c = _project_axis(op["geometry"]["center"], direction)
            wlv = next((l for l in levels if l["name"] == op["level"]), None)
            if wlv is None:
                continue
            sill = float(op["geometry"].get("sill_height", 0.0))
            z0 = float(wlv["elevation_m"]) + sill
            h = float(op["geometry"]["height"])
            ww = float(op["geometry"]["width"])
            body.append(f'<rect class="opening" x="{u_c - ww / 2:.2f}" y="{-(z0 + h):.2f}" '
                        f'width="{ww:.2f}" height="{h:.2f}"/>')
            # window mullions
            if op in windows:
                u0 = u_c - ww / 2
                u1 = u_c + ww / 2
                z_mid = z0 + h / 2.0
                body.append(f'<line class="window-line" x1="{u0:.2f}" y1="{-z_mid:.2f}" '
                            f'x2="{u1:.2f}" y2="{-z_mid:.2f}"/>')
                body.append(f'<line class="window-line" x1="{u_c:.2f}" y1="{-(z0 + h):.2f}" '
                            f'x2="{u_c:.2f}" y2="{-z0:.2f}"/>')

    # Roof outline (approximate as fill rectangle at top with overhang)
    for r in roofs:
        prof = r["geometry"].get("profile") or []
        if not prof:
            continue
        u_lo = min(_project_axis_pt(p, direction) for p in prof) - 0.3
        u_hi = max(_project_axis_pt(p, direction) for p in prof) + 0.3
        z_top = max(float(lv["elevation_m"]) + float(lv["height_m"]) for lv in levels)
        z_thick = float(r["geometry"].get("height", 0.2))
        body.append(f'<rect class="roof-fill" x="{u_lo:.2f}" y="{-(z_top + z_thick):.2f}" '
                    f'width="{u_hi - u_lo:.2f}" height="{z_thick:.2f}"/>')

    # Level reference lines on the right edge
    for lv in levels:
        z = float(lv["elevation_m"])
        body.append(f'<line class="level-line" x1="{u_min:.2f}" y1="{-z:.2f}" '
                    f'x2="{u_max:.2f}" y2="{-z:.2f}"/>')
        body.append(f'<text class="level-label" x="{u_max + 0.2:.2f}" y="{-z - 0.1:.2f}">'
                    f'{lv["name"]} (+{z:.2f}m)</text>')

    # Title block
    title_y = -z_min + 1.5
    body.append(f'<text class="title" x="{u_min:.2f}" y="{title_y:.2f}">'
                f'{_escape(project_name or bim["project"]["name"])} — Elevation {direction}</text>')
    body.append(_scale_bar(u_min + 0.5, -z_min + 3.0, length=5.0))

    svg = _svg_wrap(body, u_min, -z_max - 1.0, width, height)
    Path(out_path).write_text(svg, encoding="utf-8")


# ---- helpers ----------------------------------------------------------------

def _svg_wrap(body: list[str], x: float, y: float, w: float, h: float) -> str:
    body_str = "\n".join(body)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'viewBox="{x:.2f} {y:.2f} {w:.2f} {h:.2f}" '
            f'preserveAspectRatio="xMidYMid meet">\n'
            f'<defs><style>{SVG_STYLE}</style></defs>\n'
            f'<rect class="bg" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"/>\n'
            f'{body_str}\n</svg>\n')


def _collect_plan_points(walls, columns, spaces) -> list[list[float]]:
    pts: list[list[float]] = []
    for w in walls:
        pts.extend([w["geometry"]["start"], w["geometry"]["end"]])
    for c in columns:
        pts.append(c["geometry"]["center"])
    for sp in spaces:
        pts.extend(sp["geometry"]["profile"])
    return pts


def _bounds(pts: Iterable[list[float]], margin: float = 0.0):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin


def _wall_corners(w: dict):
    a = w["geometry"]["start"]
    b = w["geometry"]["end"]
    t = float(w["geometry"]["thickness"])
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    return [
        (a[0], a[1]),
        (b[0], b[1]),
        (b[0] + nx * t, b[1] + ny * t),
        (a[0] + nx * t, a[1] + ny * t),
    ]


def _polygon(pts, cls: str) -> str:
    flipped = " ".join(f"{p[0]:.3f},{-p[1]:.3f}" for p in pts)
    return f'<polygon class="{cls}" points="{flipped}"/>'


def _door_plan(d: dict, walls_by_id: dict) -> list[str]:
    gd = d["geometry"]
    host = walls_by_id.get(gd.get("host_id"))
    t = float(host["geometry"]["thickness"]) if host else 0.2
    c = gd["center"]
    w = float(gd["width"])
    angle = float(gd.get("angle_rad", 0.0))
    ca, sa = math.cos(angle), math.sin(angle)

    def rot(lx, ly):
        return (c[0] + ca * lx - sa * ly, c[1] + sa * lx + ca * ly)

    rect = [rot(-w / 2, -t / 2), rot(w / 2, -t / 2),
            rot(w / 2, t / 2), rot(-w / 2, t / 2)]
    out = [_polygon(rect, "opening")]
    # Swing arc from one hinge
    hinge = rot(-w / 2, 0)
    target = rot(-w / 2 + w, 0)
    # Quarter-circle from target to top using SVG arc
    arc_end = rot(-w / 2, w)
    out.append(
        f'<path class="swing" d="M {target[0]:.3f},{-target[1]:.3f} '
        f'A {w:.3f},{w:.3f} 0 0 1 {arc_end[0]:.3f},{-arc_end[1]:.3f}"/>'
    )
    out.append(
        f'<line class="opening-line" x1="{hinge[0]:.3f}" y1="{-hinge[1]:.3f}" '
        f'x2="{target[0]:.3f}" y2="{-target[1]:.3f}"/>'
    )
    return out


def _window_plan(win: dict, walls_by_id: dict) -> list[str]:
    gd = win["geometry"]
    host = walls_by_id.get(gd.get("host_id"))
    t = float(host["geometry"]["thickness"]) if host else 0.2
    c = gd["center"]
    w = float(gd["width"])
    angle = float(gd.get("angle_rad", 0.0))
    ca, sa = math.cos(angle), math.sin(angle)

    def rot(lx, ly):
        return (c[0] + ca * lx - sa * ly, c[1] + sa * lx + ca * ly)

    rect = [rot(-w / 2, -t / 2), rot(w / 2, -t / 2),
            rot(w / 2, t / 2), rot(-w / 2, t / 2)]
    out = [_polygon(rect, "opening")]
    # Three parallel lines along axis (window glazing)
    for offset in (-t / 4, 0.0, t / 4):
        e1 = rot(-w / 2, offset)
        e2 = rot(w / 2, offset)
        out.append(f'<line class="window-line" x1="{e1[0]:.3f}" y1="{-e1[1]:.3f}" '
                   f'x2="{e2[0]:.3f}" y2="{-e2[1]:.3f}"/>')
    return out


def _stair_plan(s: dict) -> str:
    bbox = s["geometry"]["bbox"]
    x0, y0, x1, y1 = bbox
    steps = int(s["geometry"].get("riser_count") or 12)
    lines = []
    dy = (y1 - y0) / steps
    for i in range(1, steps):
        y = y0 + i * dy
        lines.append(f'<line class="stair" x1="{x0:.3f}" y1="{-y:.3f}" '
                     f'x2="{x1:.3f}" y2="{-y:.3f}"/>')
    lines.append(
        f'<rect class="opening-line" x="{x0:.3f}" y="{-y1:.3f}" '
        f'width="{x1 - x0:.3f}" height="{y1 - y0:.3f}" fill="none"/>')
    return "\n".join(lines)


def _polygon_area(poly):
    a = 0.0
    for i in range(len(poly)):
        b = poly[(i + 1) % len(poly)]
        a += poly[i][0] * b[1] - b[0] * poly[i][1]
    return abs(a) * 0.5


def _scale_bar(x: float, y: float, length: float = 5.0) -> str:
    return (f'<g><line class="scale-bar" x1="{x:.2f}" y1="{y:.2f}" '
            f'x2="{x + length:.2f}" y2="{y:.2f}"/>'
            f'<text class="scale-text" x="{x:.2f}" y="{y - 0.3:.2f}">0</text>'
            f'<text class="scale-text" x="{x + length:.2f}" y="{y - 0.3:.2f}">'
            f'{length:.0f}m</text></g>')


def _north_arrow(x: float, y: float) -> str:
    return (f'<g><polygon points="{x:.2f},{y - 1.0:.2f} {x - 0.4:.2f},{y:.2f} '
            f'{x + 0.4:.2f},{y:.2f}" fill="#1a1a1a"/>'
            f'<text class="scale-text" x="{x:.2f}" y="{y + 0.5:.2f}" '
            f'text-anchor="middle">N</text></g>')


def _wall_faces(w: dict, direction: str) -> bool:
    a = w["geometry"]["start"]
    b = w["geometry"]["end"]
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return False
    nx, ny = -dy / length, dx / length
    if direction == "N":
        return ny > 0.3
    if direction == "S":
        return ny < -0.3
    if direction == "E":
        return nx > 0.3
    if direction == "W":
        return nx < -0.3
    return False


def _project_axis(p, direction: str) -> float:
    return p[0] if direction in ("N", "S") else p[1]


def _project_axis_pt(p, direction: str) -> float:
    return p[0] if direction in ("N", "S") else p[1]


def _building_extents(bim: dict):
    pts: list[list[float]] = []
    for e in bim["elements"]:
        g = e.get("geometry", {})
        if "centerline" in g:
            pts.extend(g["centerline"])
        if "profile" in g:
            pts.extend(g["profile"])
        if "center" in g and isinstance(g["center"], list):
            pts.append(g["center"])
    if not pts:
        return None
    return _bounds(pts, margin=1.0)


def _escape(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
