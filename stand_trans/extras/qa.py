"""Geometric / topological QA checks beyond IFC syntax.

Runs over the BIM JSON and parametric input. Findings include wall-junction gaps,
narrow piers between openings, vertical wall alignment between storeys, room
access, stair connectivity, and missing host references for iwans/pishtaqs.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path


def qa_report(bim: dict, param: dict, out_path: str | Path | None = None) -> dict:
    issues: list[dict] = []

    def add(severity: str, code: str, msg: str, ref=None):
        issues.append({"severity": severity, "code": code, "message": msg, "ref": ref})

    walls = [e for e in bim["elements"] if e["type"] == "Wall"]
    columns = [e for e in bim["elements"] if e["type"] == "Column"]
    doors = [e for e in bim["elements"] if e["type"] == "Door"]
    windows = [e for e in bim["elements"] if e["type"] == "Window"]
    spaces = [e for e in bim["elements"] if e["type"] == "Space"]
    stairs = [e for e in bim["elements"] if e["type"] == "Stair"]

    # 1. Wall-junction gaps (endpoints close but not touching)
    walls_by_level: dict[str, list[dict]] = {}
    for w in walls:
        walls_by_level.setdefault(w["level"], []).append(w)
    junction_tol = 0.30
    for level, lvl_walls in walls_by_level.items():
        for i, w1 in enumerate(lvl_walls):
            for w2 in lvl_walls[i + 1:]:
                for p1, n1 in ((w1["geometry"]["start"], "start"), (w1["geometry"]["end"], "end")):
                    for p2, n2 in ((w2["geometry"]["start"], "start"), (w2["geometry"]["end"], "end")):
                        d = math.dist(p1, p2)
                        if 0.005 < d < junction_tol:
                            add("warning", "WALL_JUNCTION_GAP",
                                f"{w1['id']} {n1} is {d * 1000:.0f}mm from {w2['id']} {n2}",
                                {"wall1": w1["id"], "wall2": w2["id"],
                                 "gap_mm": round(d * 1000)})

    # 2. Opening clearance — narrow pier between adjacent openings on same wall
    openings_by_wall: dict[str, list[dict]] = {}
    for op in doors + windows:
        host_id = op["geometry"].get("host_id")
        if host_id:
            openings_by_wall.setdefault(host_id, []).append(op)
    pier_tol = 0.30
    for host_id, ops in openings_by_wall.items():
        wall = next((w for w in walls if w["id"] == host_id), None)
        if not wall:
            continue
        a, b = wall["geometry"]["centerline"]
        wall_length = math.dist(a, b)
        sorted_ops = sorted(ops, key=lambda o: float(o["geometry"].get("host_param", 0.0)))
        for i in range(len(sorted_ops) - 1):
            o1, o2 = sorted_ops[i], sorted_ops[i + 1]
            p1 = float(o1["geometry"].get("host_param", 0.0)) * wall_length
            p2 = float(o2["geometry"].get("host_param", 0.0)) * wall_length
            pier = (p2 - float(o2["geometry"]["width"]) / 2.0) - (p1 + float(o1["geometry"]["width"]) / 2.0)
            if pier < pier_tol:
                add("warning", "OPENING_PIER_NARROW",
                    f"Pier between {o1['id']} and {o2['id']} on {host_id} is {pier * 1000:.0f}mm "
                    f"(< {pier_tol * 1000:.0f}mm)",
                    {"op1": o1["id"], "op2": o2["id"], "pier_mm": round(pier * 1000)})

    # 3. Vertical wall support — upper-floor wall should sit on lower wall or columns
    levels = sorted(bim["levels"], key=lambda lv: float(lv["elevation_m"]))
    cols_by_level: dict[str, list[dict]] = {}
    for c in columns:
        cols_by_level.setdefault(c["level"], []).append(c)
    for i in range(1, len(levels)):
        upper_name = levels[i]["name"]
        lower_name = levels[i - 1]["name"]
        upper = walls_by_level.get(upper_name, [])
        lower = walls_by_level.get(lower_name, [])
        lower_cols = cols_by_level.get(lower_name, [])
        for w in upper:
            ws, we = w["geometry"]["start"], w["geometry"]["end"]
            if any(_segments_overlap(ws, we, lw["geometry"]["start"], lw["geometry"]["end"])
                   for lw in lower):
                continue
            mid = [(ws[0] + we[0]) / 2.0, (ws[1] + we[1]) / 2.0]
            if any(math.dist(c["geometry"]["center"], mid) < 1.5 for c in lower_cols):
                continue
            add("warning", "WALL_NOT_SUPPORTED",
                f"Wall {w['id']} on {upper_name} has no wall or column under it on {lower_name}",
                w["id"])

    # 4. Stair connectivity — each adjacent pair of levels must have at least one stair
    if len(levels) > 1 and stairs:
        stair_pairs = set()
        for s in stairs:
            f, t = s["geometry"].get("from_level"), s["geometry"].get("to_level")
            if f and t:
                stair_pairs.add(frozenset([f, t]))
        for i in range(len(levels) - 1):
            pair = frozenset([levels[i]["name"], levels[i + 1]["name"]])
            if pair not in stair_pairs:
                add("warning", "NO_STAIR_BETWEEN",
                    f"No stair between {levels[i]['name']} and {levels[i + 1]['name']}",
                    {"from": levels[i]["name"], "to": levels[i + 1]["name"]})

    # 5. Interior room access — each non-outdoor space should have a door nearby
    for sp in spaces:
        func = sp["geometry"].get("function", "general")
        if func in ("courtyard", "garden", "water"):
            continue
        poly = sp["geometry"]["profile"]
        has_door = False
        for d in doors:
            if d["level"] != sp["level"]:
                continue
            c = d["geometry"]["center"]
            if _point_in_polygon(c, poly) or _point_near_polygon_edge(c, poly, 0.6):
                has_door = True
                break
        if not has_door:
            add("info", "SPACE_NO_DOOR",
                f"Space '{sp['geometry'].get('name', sp['id'])}' ({func}) "
                f"has no door inside or on its perimeter", sp["id"])

    # 6. Iwan/Pishtaq host validity
    wall_ids = {w["id"] for w in walls}
    for it in param.get("iwans", []):
        if it.get("host_wall") not in wall_ids:
            add("error", "INVALID_HOST_WALL",
                f"Iwan {it['id']} hosts on unknown wall '{it.get('host_wall')}'", it["id"])
    iwan_ids = {it["id"] for it in param.get("iwans", [])}
    for p in param.get("pishtaqs", []):
        host = p.get("host_iwan")
        if host and host not in iwan_ids:
            add("error", "INVALID_HOST_IWAN",
                f"Pishtaq {p['id']} references unknown iwan '{host}'", p["id"])

    # 7. Footing under each column position
    cols_xy = {(round(c["geometry"]["center"][0], 1), round(c["geometry"]["center"][1], 1))
               for c in columns}
    footings_xy = {(round(e["geometry"]["center"][0], 1), round(e["geometry"]["center"][1], 1))
                   for e in bim["elements"] if e["type"] == "Footing"}
    missing = cols_xy - footings_xy
    if missing and columns:
        for xy in sorted(missing):
            add("info", "COLUMN_NO_FOOTING",
                f"Column at {xy} has no footing under it", list(xy))

    severity_counts = Counter(i["severity"] for i in issues)
    summary = {
        "ok": severity_counts.get("error", 0) == 0,
        "counts": {k: severity_counts.get(k, 0) for k in ("error", "warning", "info")},
        "checks_run": [
            "WALL_JUNCTION_GAP", "OPENING_PIER_NARROW", "WALL_NOT_SUPPORTED",
            "NO_STAIR_BETWEEN", "SPACE_NO_DOOR",
            "INVALID_HOST_WALL", "INVALID_HOST_IWAN", "COLUMN_NO_FOOTING",
        ],
        "issues": issues,
    }
    if out_path is not None:
        Path(out_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return summary


def _segments_overlap(a1, a2, b1, b2, lateral_tol=0.6, axial_overlap_min=0.4):
    da = (a2[0] - a1[0], a2[1] - a1[1])
    la = math.hypot(*da)
    if la == 0:
        return False
    ux, uy = da[0] / la, da[1] / la
    px, py = -uy, ux

    def project(p):
        rx, ry = p[0] - a1[0], p[1] - a1[1]
        return rx * ux + ry * uy, abs(rx * px + ry * py)

    t1, d1 = project(b1)
    t2, d2 = project(b2)
    if d1 > lateral_tol or d2 > lateral_tol:
        return False
    lo = max(0.0, min(t1, t2))
    hi = min(la, max(t1, t2))
    return (hi - lo) >= axial_overlap_min


def _point_in_polygon(p, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x_int = a[0] + (p[1] - a[1]) / (b[1] - a[1] + 1e-12) * (b[0] - a[0])
            if p[0] < x_int:
                inside = not inside
    return inside


def _point_near_polygon_edge(p, poly, tol):
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        abx, aby = b[0] - a[0], b[1] - a[1]
        ab_len = math.hypot(abx, aby)
        if ab_len == 0:
            d = math.dist(p, a)
        else:
            apx, apy = p[0] - a[0], p[1] - a[1]
            t = max(0.0, min(1.0, (apx * abx + apy * aby) / (ab_len * ab_len)))
            d = math.dist(p, (a[0] + t * abx, a[1] + t * aby))
        if d < tol:
            return True
    return False
