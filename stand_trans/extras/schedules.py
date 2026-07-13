"""Cost estimate + material/area/opening/storey schedules.

Reads the BIM JSON and the BOQ produced by ifc_builder and writes:
- {stem}.schedule.json    — combined schedule report
- {stem}.cost.json        — focused cost estimate

Unit rates are defaults intended to give an order-of-magnitude estimate; users
can override per project.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .ifc_builder import MATERIAL_DENSITY_KG_M3


# All rates in USD; per m³ unless noted.
UNIT_RATES_USD = {
    "concrete": 220.0,
    "brick": 150.0,
    "insulation": 95.0,
    "kashi_tile": 480.0,
    "plaster": 75.0,
    "wood": 750.0,
    "steel": 2400.0,
    "stone": 320.0,
    "glass": 95.0,   # per m²
    "water": 0.0,
    "garden": 60.0,  # per m² of paving
    "ceremonial": 0.0,
}

PER_AREA_TYPES = {"Door": 220.0, "Window": 360.0}
PER_ITEM_TYPES = {
    "Muqarnas": 12000.0,
    "Pishtaq": 0.0,   # handled by volume
    "LightFixture": 90.0,
    "Outlet": 35.0,
}
SPACE_FITOUT_USD_M2 = {
    "office": 800.0,
    "hall": 600.0,
    "ceremonial": 1500.0,
    "archive": 350.0,
    "circulation": 400.0,
    "courtyard": 150.0,
    "garden": 80.0,
    "water": 0.0,
    "general": 500.0,
}


def generate_schedules(bim: dict, boq_path: str | Path, out_dir: str | Path,
                       stem: str) -> dict:
    boq = json.loads(Path(boq_path).read_text(encoding="utf-8"))
    cost = _cost_estimate(boq)
    materials = _material_schedule(boq)
    areas = _area_schedule(bim)
    openings = _opening_schedule(bim)
    storeys = _storey_schedule(bim)
    schedule = {
        "project": bim["project"]["name"],
        "currency": "USD",
        "totals": {
            "structural_cost_usd": cost["total_usd"],
            "total_floor_area_m2": sum(s["net_floor_area_m2"] for s in storeys.values()),
            "total_mass_kg": sum(m["mass_kg"] for m in materials.values()),
        },
        "cost_by_type": cost["by_type"],
        "materials": materials,
        "areas_by_function": areas,
        "openings": openings,
        "storeys": storeys,
    }
    schedule_path = Path(out_dir) / f"{stem}.schedule.json"
    cost_path = Path(out_dir) / f"{stem}.cost.json"
    schedule_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    cost_path.write_text(json.dumps(cost, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return {"schedule_path": str(schedule_path), "cost_path": str(cost_path),
            "summary": schedule["totals"], "cost_by_type": cost["by_type"]}


def _cost_estimate(boq: dict) -> dict:
    by_type: dict[str, float] = {}
    total = 0.0
    for elem_type, cats in boq.get("categories", {}).items():
        type_cost = 0.0
        for cat_name, vals in cats["by_category"].items():
            cost = 0.0
            if elem_type == "Space":
                # Fit-out cost is per m² of floor area, by function category
                rate = SPACE_FITOUT_USD_M2.get(cat_name, SPACE_FITOUT_USD_M2["general"])
                cost = vals.get("net_area", 0.0) * rate
            elif vals.get("layers"):
                total_t = sum(t for _, t in vals["layers"]) or 1.0
                for mat_name, t in vals["layers"]:
                    rate = UNIT_RATES_USD.get(mat_name, 100.0)
                    layer_vol = vals["net_volume"] * (t / total_t)
                    cost += layer_vol * rate
            elif elem_type in PER_AREA_TYPES:
                cost = vals["gross_area"] * PER_AREA_TYPES[elem_type]
            elif elem_type in PER_ITEM_TYPES and PER_ITEM_TYPES[elem_type] > 0:
                cost = vals["count"] * PER_ITEM_TYPES[elem_type]
            else:
                rate = UNIT_RATES_USD.get(cat_name, 100.0)
                if cat_name == "garden":
                    cost = vals.get("net_area", 0.0) * rate
                else:
                    cost = vals["net_volume"] * rate
            type_cost += cost
        by_type[elem_type] = round(type_cost, 2)
        total += type_cost
    return {"by_type": by_type, "total_usd": round(total, 2)}


def _material_schedule(boq: dict) -> dict:
    by_mat: dict[str, dict] = {}
    for elem_type, cats in boq.get("categories", {}).items():
        for cat_name, vals in cats["by_category"].items():
            if vals.get("layers"):
                total_t = sum(t for _, t in vals["layers"]) or 1.0
                for mat_name, t in vals["layers"]:
                    info = by_mat.setdefault(mat_name, {"volume_m3": 0.0, "mass_kg": 0.0, "rate_usd_m3": UNIT_RATES_USD.get(mat_name, 0.0)})
                    layer_vol = vals["net_volume"] * (t / total_t)
                    info["volume_m3"] += layer_vol
                    info["mass_kg"] += layer_vol * MATERIAL_DENSITY_KG_M3.get(mat_name, 1800.0)
            else:
                info = by_mat.setdefault(cat_name, {"volume_m3": 0.0, "mass_kg": 0.0, "rate_usd_m3": UNIT_RATES_USD.get(cat_name, 0.0)})
                info["volume_m3"] += vals["net_volume"]
                info["mass_kg"] += vals.get("mass_kg", 0.0)
    for mat, info in by_mat.items():
        info["volume_m3"] = round(info["volume_m3"], 3)
        info["mass_kg"] = round(info["mass_kg"], 1)
        info["cost_usd"] = round(info["volume_m3"] * info["rate_usd_m3"], 2)
    return by_mat


def _area_schedule(bim: dict) -> dict:
    by_function: dict[str, dict] = {}
    for sp in bim["elements"]:
        if sp["type"] != "Space":
            continue
        func = sp["geometry"].get("function", "general")
        area = _polygon_area(sp["geometry"]["profile"])
        info = by_function.setdefault(func, {"count": 0, "area_m2": 0.0,
                                              "rooms": []})
        info["count"] += 1
        info["area_m2"] += area
        info["rooms"].append({
            "id": sp["id"],
            "name": sp["geometry"].get("name", sp["id"]),
            "level": sp["level"],
            "area_m2": round(area, 2),
        })
    for func, info in by_function.items():
        info["area_m2"] = round(info["area_m2"], 2)
    return by_function


def _opening_schedule(bim: dict) -> dict:
    doors: list[dict] = []
    windows: list[dict] = []
    for e in bim["elements"]:
        g = e.get("geometry", {})
        if e["type"] == "Door":
            doors.append({
                "id": e["id"], "level": e["level"],
                "width_m": float(g.get("width", 0.0)),
                "height_m": float(g.get("height", 0.0)),
                "area_m2": round(float(g.get("width", 0)) * float(g.get("height", 0)), 2),
                "host_wall": g.get("host_id", ""),
            })
        elif e["type"] == "Window":
            windows.append({
                "id": e["id"], "level": e["level"],
                "width_m": float(g.get("width", 0.0)),
                "height_m": float(g.get("height", 0.0)),
                "sill_height_m": float(g.get("sill_height", 0.0)),
                "area_m2": round(float(g.get("width", 0)) * float(g.get("height", 0)), 2),
                "host_wall": g.get("host_id", ""),
            })
    door_summary = _summarise_openings(doors)
    window_summary = _summarise_openings(windows)
    return {
        "doors": doors,
        "door_summary": door_summary,
        "windows": windows,
        "window_summary": window_summary,
    }


def _summarise_openings(items: list[dict]) -> dict:
    groups: dict[tuple, dict] = {}
    for it in items:
        key = (it["width_m"], it["height_m"])
        info = groups.setdefault(key, {"width_m": key[0], "height_m": key[1],
                                       "count": 0, "total_area_m2": 0.0})
        info["count"] += 1
        info["total_area_m2"] += it["area_m2"]
    for info in groups.values():
        info["total_area_m2"] = round(info["total_area_m2"], 2)
    return {"by_size": list(groups.values()),
            "total_count": len(items),
            "total_area_m2": round(sum(i["area_m2"] for i in items), 2)}


def _storey_schedule(bim: dict) -> dict:
    by_storey: dict[str, dict] = {}
    for lv in bim["levels"]:
        by_storey[lv["name"]] = {
            "elevation_m": float(lv["elevation_m"]),
            "height_m": float(lv["height_m"]),
            "wall_count": 0, "column_count": 0,
            "door_count": 0, "window_count": 0,
            "space_count": 0,
            "gross_floor_area_m2": 0.0,
            "net_floor_area_m2": 0.0,
        }
    for e in bim["elements"]:
        lvl = e.get("level")
        if not lvl or lvl not in by_storey:
            continue
        st = by_storey[lvl]
        t = e["type"]
        if t == "Wall":
            st["wall_count"] += 1
        elif t == "Column":
            st["column_count"] += 1
        elif t == "Door":
            st["door_count"] += 1
        elif t == "Window":
            st["window_count"] += 1
        elif t == "Space":
            area = _polygon_area(e["geometry"]["profile"])
            st["space_count"] += 1
            st["net_floor_area_m2"] += area
        elif t == "Slab":
            area = _polygon_area(e["geometry"]["profile"])
            st["gross_floor_area_m2"] += area
    for st in by_storey.values():
        st["gross_floor_area_m2"] = round(st["gross_floor_area_m2"], 2)
        st["net_floor_area_m2"] = round(st["net_floor_area_m2"], 2)
    return by_storey


def _polygon_area(poly) -> float:
    a = 0.0
    for i in range(len(poly)):
        b = poly[(i + 1) % len(poly)]
        a += poly[i][0] * b[1] - b[0] * poly[i][1]
    return abs(a) * 0.5
