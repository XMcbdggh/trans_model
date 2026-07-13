"""SceneBuilder -- the reliable Layer-2 generator.

The LLM never hand-writes wall coordinates or `host_id` wiring. It expresses
high-level intent (a Building Spec, see ../schema/building-spec.schema.json) and
this library turns that intent into a fully-valid stand_trans ``param.json``.

Guarantees provided here so the pipeline never rejects the output:
  * rectangular footprints emit exactly 4 external walls per storey and the loop
    closes exactly (start of wall i == end of wall i-1);
  * windows / doors automatically reference their host wall id (facade -> index);
  * levels stack with continuous elevations;
  * windows distribute evenly along a facade by *count* (LLM gives a number, not
    coordinates);
  * every emitted value is already in metres.

Coordinate system (building space, matches stand_trans):
  X = east, Y = north, Z = up. Right-handed. Units: metres.

Facade -> wall-index mapping for a rectangular box (x0,y0)-(x1,y1):
  south = 0  (y = y0)
  east  = 1  (x = x1)
  north = 2  (y = y1)
  west  = 3  (x = x0)
"""
from __future__ import annotations

import json
from pathlib import Path

FACADE_INDEX = {"south": 0, "east": 1, "north": 2, "west": 3}

# collections emitted into param.json (subset of stand_trans COLLECTIONS we drive)
_COLLECTIONS = (
    "walls", "columns", "slabs", "doors", "windows", "roofs", "stairs",
    "rooms", "domes", "iwans", "pishtaqs", "pools", "gardens", "canals",
    "trees", "vehicles", "terrain",
)


class SceneBuilder:
    def __init__(self, name: str, style: str = "persian", unit: str = "m"):
        self.project = {"name": name, "unit": unit, "style": style}
        self.style = {"preset": style}
        self.levels: list[dict] = []
        self._level_names: set[str] = set()
        self._level_meta: dict[str, tuple[float, float]] = {}   # name -> (elevation, height)
        self.auto_structure = False
        self.auto_mep = False
        for coll in _COLLECTIONS:
            setattr(self, coll, [])

    # ------------------------------------------------------------------ levels
    def add_level(self, name: str, elevation_m: float, height_m: float) -> str:
        if name not in self._level_names:
            self.levels.append({"name": name, "elevation_m": float(elevation_m),
                                "height_m": float(height_m)})
            self._level_names.add(name)
            self._level_meta[name] = (float(elevation_m), float(height_m))
        return name

    def _level_top(self, name: str) -> float:
        elev, h = self._level_meta.get(name, (0.0, 3.5))
        return elev + h

    def stack_levels(self, names, base_elevation_m: float, height_m: float) -> list[str]:
        """Create consecutive levels bottom->top with continuous elevations."""
        elev = float(base_elevation_m)
        for nm in names:
            self.add_level(nm, elev, height_m)
            elev += float(height_m)
        return list(names)

    # -------------------------------------------------------------- primitives
    @staticmethod
    def _poly(x0, y0, x1, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

    def _box_walls(self, prefix, level, x0, y0, x1, y1, thickness, *,
                   category, load_bearing, material=None, height_m=None):
        segs = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
        ids = []
        for i, (a, b) in enumerate(segs):
            wid = f"{prefix}_{i}"
            w = {"id": wid, "level": level, "start": list(a), "end": list(b),
                 "thickness_m": thickness, "category": category,
                 "load_bearing": load_bearing}
            if material:
                w["material"] = material
            if height_m:
                w["height_m"] = height_m
            self.walls.append(w)
            ids.append(wid)
        return ids  # [south, east, north, west]

    # --------------------------------------------------- high-level: buildings
    def box_building(self, prefix, bbox, level_names, *, material="reinforced_concrete",
                     ext_thickness=0.4, slab_thickness=0.4, roof_type="flat",
                     column_spacing_m=0.0, load_bearing=True):
        """A rectangular multi-storey building: outer walls + slab (+ columns) per
        level, plus one roof on the top level. Returns a handle used to attach
        windows/doors."""
        x0, y0, x1, y1 = bbox
        facade_walls: dict[str, dict[str, str]] = {}
        for lv in level_names:
            ids = self._box_walls(f"{prefix}_{lv}_o", lv, x0, y0, x1, y1, ext_thickness,
                                   category="external", load_bearing=load_bearing, material=material)
            facade_walls[lv] = {"south": ids[0], "east": ids[1], "north": ids[2], "west": ids[3]}
            self.slabs.append({"id": f"{prefix}_{lv}_slab", "level": lv,
                               "polygon": self._poly(x0, y0, x1, y1),
                               "thickness_m": slab_thickness, "material": material})
            if column_spacing_m and column_spacing_m > 0:
                step = max(1, int(column_spacing_m))
                for ix, x in enumerate(range(int(x0) + step, int(x1), step)):
                    for iy, y in enumerate(range(int(y0) + step, int(y1), step)):
                        self.columns.append({"id": f"{prefix}_{lv}_c{ix}_{iy}", "level": lv,
                                             "center": [float(x), float(y)], "shape": "rect",
                                             "size": [0.5, 0.5], "material": material})
        if roof_type:
            self._add_roof(f"{prefix}_roof", bbox, level_names[-1], roof_type,
                           max(slab_thickness, 0.3))
        return {"prefix": prefix, "bbox": list(bbox), "facade_walls": facade_walls,
                "levels": list(level_names)}

    def _add_roof(self, roof_id, bbox, level, roof_type, thickness):
        """Emit a roof, filling the extra fields each type needs so normalize()
        never rejects it. `flat` needs nothing extra; `gable` needs a ridge line
        and eave/ridge heights; `hip`/`pyramidal` get a ridge (apex) height."""
        x0, y0, x1, y1 = bbox
        roof = {"id": roof_id, "level": level, "type": roof_type,
                "polygon": self._poly(x0, y0, x1, y1), "thickness_m": thickness}
        top = self._level_top(level)               # eave sits at the top of the storey
        span_x, span_y = (x1 - x0), (y1 - y0)
        if roof_type == "gable":
            if span_x >= span_y:                   # ridge runs along the longer axis
                ymid = (y0 + y1) / 2.0
                roof["ridge_start"], roof["ridge_end"] = [x0, ymid], [x1, ymid]
                rise = 0.6 * (span_y / 2.0)
            else:
                xmid = (x0 + x1) / 2.0
                roof["ridge_start"], roof["ridge_end"] = [xmid, y0], [xmid, y1]
                rise = 0.6 * (span_x / 2.0)
            roof["eave_height_m"] = top
            roof["ridge_height_m"] = top + max(rise, 1.0)
        elif roof_type in ("hip", "pyramidal"):
            roof["eave_height_m"] = top
            roof["ridge_height_m"] = top + max(0.6 * min(span_x, span_y) / 2.0, 1.0)
        self.roofs.append(roof)

    def add_windows(self, building, facade, count, *, levels=None, width_m=1.4,
                    height_m=1.6, sill_m=0.9, shape="rect", margin_m=2.0):
        """Distribute `count` windows evenly along `facade` on each given level.
        The LLM supplies a *count*; exact centres are computed here and always
        land on the host wall (so normalize() never rejects them)."""
        if count <= 0:
            return
        x0, y0, x1, y1 = building["bbox"]
        levels = levels or building["levels"]
        for lv in levels:
            wid = building["facade_walls"][lv][facade]
            if facade in ("south", "north"):
                lo, hi = x0 + margin_m, x1 - margin_m
                fixed = y0 if facade == "south" else y1
                pts = [(lo + (hi - lo) * (k + 0.5) / count, fixed) for k in range(count)]
            else:
                lo, hi = y0 + margin_m, y1 - margin_m
                fixed = x1 if facade == "east" else x0
                pts = [(fixed, lo + (hi - lo) * (k + 0.5) / count) for k in range(count)]
            for k, (cx, cy) in enumerate(pts):
                self.windows.append({"id": f"{building['prefix']}_{lv}_{facade}_w{k}",
                                     "level": lv, "host_id": wid,
                                     "center": [round(cx, 3), round(cy, 3)],
                                     "width_m": width_m, "height_m": height_m,
                                     "sill_height_m": sill_m, "shape": shape})

    def add_door(self, building, facade, level, *, offset=0.5, width_m=1.6,
                 height_m=2.4, shape="rect", door_id=None):
        x0, y0, x1, y1 = building["bbox"]
        wid = building["facade_walls"][level][facade]
        if facade in ("south", "north"):
            cx = x0 + (x1 - x0) * offset
            cy = y0 if facade == "south" else y1
        else:
            cy = y0 + (y1 - y0) * offset
            cx = x1 if facade == "east" else x0
        self.doors.append({"id": door_id or f"{building['prefix']}_{facade}_door",
                           "level": level, "host_id": wid,
                           "center": [round(cx, 3), round(cy, 3)], "width_m": width_m,
                           "height_m": height_m, "sill_height_m": 0.0, "shape": shape})

    def add_iwan(self, iwan_id, level, host_wall, center, *, width_m, depth_m,
                 height_m, arch_height_m):
        self.iwans.append({"id": iwan_id, "level": level, "host_wall": host_wall,
                           "center": list(center), "width_m": width_m, "depth_m": depth_m,
                           "height_m": height_m, "arch_height_m": arch_height_m})

    # --------------------------------------------------------- site / features
    def perimeter_wall(self, bbox, level, *, thickness=1.0, height_m=5.0,
                       material="stone_masonry", corner_towers=True, tower_size=6.0):
        x0, y0, x1, y1 = bbox
        self._box_walls("perim", level, x0, y0, x1, y1, thickness, category="external",
                        load_bearing=False, material=material, height_m=height_m)
        if corner_towers:
            ts = tower_size
            corners = [(x0, y0), (x1 - ts, y0), (x1 - ts, y1 - ts), (x0, y1 - ts)]
            for i, (tx, ty) in enumerate(corners):
                self._box_walls(f"tower{i}", level, tx, ty, tx + ts, ty + ts, thickness,
                                category="external", load_bearing=True, material=material,
                                height_m=height_m * 1.6)

    def add_dome(self, dome_id, level, center, radius_m, height_m, *, shape="hemisphere",
                 base_height_m=0.0, **extra):
        d = {"id": dome_id, "level": level, "center": list(center), "radius_m": radius_m,
             "height_m": height_m, "shape": shape, "base_height_m": base_height_m}
        d.update(extra)
        self.domes.append(d)

    def add_pool(self, pool_id, level, polygon, *, depth_m=0.4, rim_height_m=0.15):
        self.pools.append({"id": pool_id, "level": level, "polygon": polygon,
                           "depth_m": depth_m, "rim_height_m": rim_height_m})

    def add_garden(self, garden_id, level, polygon, *, paving_pattern="charbagh_4quad"):
        self.gardens.append({"id": garden_id, "level": level, "polygon": polygon,
                             "paving_pattern": paving_pattern})

    def add_terrain(self, terrain_id, level, bbox, *, surface="sand", material=None,
                    thickness_m=0.5):
        t = {"id": terrain_id, "level": level, "bbox": list(bbox), "surface": surface,
             "thickness_m": thickness_m}
        if material:
            t["material"] = material
        self.terrain.append(t)

    def add_tree(self, tree_id, level, center, *, species="palm", height_m=8.0,
                 canopy_radius_m=2.5, trunk_radius_m=0.3):
        self.trees.append({"id": tree_id, "level": level, "center": list(center),
                           "species": species, "height_m": height_m,
                           "canopy_radius_m": canopy_radius_m, "trunk_radius_m": trunk_radius_m})

    def add_vehicle(self, vehicle_id, level, center, *, kind="car", heading_deg=0.0,
                    length_m=4.5, width_m=2.0, height_m=1.6):
        self.vehicles.append({"id": vehicle_id, "level": level, "center": list(center),
                              "kind": kind, "heading_deg": heading_deg, "length_m": length_m,
                              "width_m": width_m, "height_m": height_m})

    def add_room(self, room_id, level, name, function, polygon, *, public=False):
        self.rooms.append({"id": room_id, "level": level, "name": name,
                           "function": function, "public": public, "polygon": polygon})

    def add_stair(self, stair_id, from_level, to_level, bbox, *, width_m=1.2, riser_count=20):
        self.stairs.append({"id": stair_id, "from_level": from_level, "to_level": to_level,
                            "bbox": list(bbox), "width_m": width_m, "riser_count": riser_count})

    # ------------------------------------------------------------------- emit
    def to_param(self) -> dict:
        param = {"project": self.project, "style": self.style,
                 "auto_structure": self.auto_structure, "auto_mep": self.auto_mep,
                 "levels": self.levels}
        for coll in _COLLECTIONS:
            param[coll] = getattr(self, coll)
        return param

    def write(self, path) -> str:
        Path(path).write_text(json.dumps(self.to_param(), ensure_ascii=False, indent=2),
                              encoding="utf-8")
        return str(path)
