"""BIM JSON -> IFC4 with openings, materials, property sets and quantities."""
from __future__ import annotations

import math
from pathlib import Path

import ifcopenshell
import numpy as np
from ifcopenshell.api import run


WALL_MATERIAL_PRESETS = {
    "external": [("kashi_tile", 0.020), ("brick", 0.280), ("plaster", 0.020)],
    "courtyard": [("kashi_tile", 0.020), ("brick", 0.220), ("plaster", 0.020)],
    "internal": [("plaster", 0.020), ("brick", 0.140), ("plaster", 0.020)],
    "service": [("plaster", 0.015), ("brick", 0.150), ("plaster", 0.015)],
    "ramp": [("concrete", 0.220)],
}
ELEMENT_MATERIAL = {
    "Column": "concrete",
    "Slab": "concrete",
    "Roof": "concrete",
    "Stair": "concrete",
    "Door": "wood",
    "Window": "glass",
    "Pishtaq": "brick",
    "Pool": "stone",
    "Canal": "stone",
    "Screen": "wood",
    "Muqarnas": "plaster",
    "Garden": "stone",
    "Beam": "concrete",
    "Footing": "concrete",
    "DuctSegment": "steel",
    "PipeSegment": "steel",
    "LightFixture": "steel",
    "Outlet": "steel",
}
MATERIAL_DENSITY_KG_M3 = {
    "concrete": 2400.0,
    "brick": 1900.0,
    "insulation": 30.0,
    "kashi_tile": 2100.0,
    "plaster": 1600.0,
    "wood": 650.0,
    "glass": 2500.0,
    "steel": 7850.0,
    "stone": 2600.0,
}


def placement(x, y, z, angle):
    c, s = math.cos(angle), math.sin(angle)
    m = np.eye(4)
    m[:3, 0] = [c, s, 0.0]
    m[:3, 1] = [-s, c, 0.0]
    m[:3, 2] = [0.0, 0.0, 1.0]
    m[:3, 3] = [x, y, z]
    return m


_classification_cache: dict[int, dict] = {}


def _classify(model, product, code: str, description: str):
    """Attach a classification reference to a product (Persian element catalogue)."""
    mid = id(model)
    cache = _classification_cache.setdefault(mid, {})
    src = cache.get("source")
    if src is None:
        src = model.create_entity("IfcClassification",
                                  Source="stand_trans",
                                  Edition="1.0",
                                  Name="PersianArchitecture",
                                  Description="Persian/Islamic architectural element catalogue")
        cache["source"] = src
    ref = cache.get(code)
    if ref is None:
        ref = model.create_entity("IfcClassificationReference",
                                  Identification=code,
                                  Name=code.split("/")[-1],
                                  Description=description,
                                  ReferencedSource=src)
        cache[code] = ref
    rel = model.create_entity("IfcRelAssociatesClassification",
                              GlobalId=ifcopenshell.guid.new(),
                              RelatingClassification=ref,
                              RelatedObjects=[product])
    return rel


def build_ifc(bim: dict, out_path: str | Path) -> dict:
    _classification_cache.clear()
    model, body, building = _new_model(bim["project"]["name"])
    levels = bim.get("levels") or [{"name": "1F", "elevation_m": 0.0, "height_m": 3.6}]
    storeys = {}
    for lv in levels:
        st = run("root.create_entity", model, ifc_class="IfcBuildingStorey", name=lv["name"])
        elev = float(lv["elevation_m"])
        st.Elevation = elev
        run("aggregate.assign_object", model, products=[st], relating_object=building)
        run("geometry.edit_object_placement", model, product=st, matrix=placement(0, 0, elev, 0), is_si=True)
        storeys[lv["name"]] = {"entity": st, "elevation_m": elev, "height_m": float(lv["height_m"])}

    materials = _create_materials(model)
    wall_layer_sets = _create_wall_layer_sets(model, materials)

    elements = bim.get("elements", [])
    walls_first = sorted(elements, key=lambda e: 1 if e["type"] in ("Door", "Window") else 0)

    wall_index: dict[str, dict] = {}
    counts: dict[str, int] = {}
    boq = _empty_boq()
    zones: dict[str, dict] = {}

    for elem in walls_first:
        typ = elem["type"]
        counts[typ] = counts.get(typ, 0) + 1
        level = elem.get("level") or levels[0]["name"]
        st_info = storeys.get(level) or next(iter(storeys.values()))
        st = st_info["entity"]
        elev = st_info["elevation_m"]
        h_default = st_info["height_m"]
        g = elem.get("geometry", {})

        if typ == "Wall":
            a, b = g["centerline"]
            length = math.dist(a, b)
            if length <= 0:
                continue
            angle = math.atan2(b[1] - a[1], b[0] - a[0])
            thickness = float(g.get("thickness", 0.24))
            height = float(g.get("height", h_default))
            category = g.get("category", "internal")
            entity = _box(model, body, st, "IfcWall", elem["id"], a[0], a[1], elev,
                          length, thickness, height, angle)
            _assign_wall_material(model, entity, wall_layer_sets, category)
            _add_wall_pset(model, entity, category, elem)
            wall_index[elem["id"]] = {
                "entity": entity,
                "length": length,
                "thickness": thickness,
                "height": height,
                "angle": angle,
                "start": a,
                "category": category,
                "elev": elev,
                "openings_area": 0.0,
            }
            _accumulate(boq, "Wall", category, length=length, gross_side_area=length * height,
                        gross_volume=length * height * thickness, layers=WALL_MATERIAL_PRESETS.get(category))
        elif typ == "Column":
            x0, y0, x1, y1 = _profile_bbox(g["profile"])
            w, d, h = x1 - x0, y1 - y0, g.get("height", h_default)
            entity = _box(model, body, st, "IfcColumn", elem["id"], x0, y0, elev, w, d, h, 0.0)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Column"]])
            _add_simple_pset(model, entity, "Pset_ColumnCommon", {
                "Reference": elem["id"],
                "LoadBearing": True,
                "IsExternal": False,
                "FireRating": "2HR",
            })
            _add_qto(model, entity, "Qto_ColumnBaseQuantities", {
                "Length": float(h),
                "CrossSectionArea": float(w * d),
                "OuterSurfaceArea": float(2 * (w + d) * h),
                "GrossVolume": float(w * d * h),
                "NetVolume": float(w * d * h),
            })
            _accumulate(boq, "Column", ELEMENT_MATERIAL["Column"], length=float(h),
                        gross_volume=float(w * d * h))
        elif typ == "Slab":
            x0, y0, x1, y1 = _profile_bbox(g["profile"])
            w, d, t = x1 - x0, y1 - y0, g.get("height", 0.15)
            area = _polygon_area(g["profile"])
            entity = _box(model, body, st, "IfcSlab", elem["id"], x0, y0, elev, w, d, t, 0.0)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Slab"]])
            _add_simple_pset(model, entity, "Pset_SlabCommon", {
                "Reference": elem["id"],
                "IsExternal": False,
                "LoadBearing": True,
                "FireRating": "2HR",
                "PitchAngle": 0.0,
            })
            _add_qto(model, entity, "Qto_SlabBaseQuantities", {
                "Width": float(w),
                "Length": float(d),
                "Depth": float(t),
                "GrossArea": float(area),
                "NetArea": float(area),
                "GrossVolume": float(area * t),
                "NetVolume": float(area * t),
            })
            _accumulate(boq, "Slab", ELEMENT_MATERIAL["Slab"], net_area=float(area),
                        gross_volume=float(area * t))
        elif typ in ("Door", "Window"):
            host_info = wall_index.get(g.get("host_id"))
            width = float(g.get("width", 0.9))
            height = float(g.get("height", 2.1))
            sill = float(g.get("sill_height", 0.0))
            angle = float(g.get("angle_rad", 0.0))
            c = g["center"]
            wall_thickness = host_info["thickness"] if host_info else 0.24
            opening_thickness = wall_thickness * 1.6
            depth_panel = 0.06 if typ == "Window" else 0.08
            ox, oy = _opening_corner(c, angle, width, opening_thickness)
            opening = _create_opening(model, body, st, elem["id"], ox, oy, elev + sill - 0.005,
                                      width, opening_thickness, height + 0.01, angle)
            if host_info:
                run("feature.add_feature", model, feature=opening, element=host_info["entity"])
                host_info["openings_area"] += width * height
            # door / window panel placed at the centre of the wall thickness
            px, py = _opening_corner(c, angle, width, depth_panel)
            ifc_cls = "IfcDoor" if typ == "Door" else "IfcWindow"
            product = _box(model, body, st, ifc_cls, elem["id"],
                           px, py, elev + sill, width, depth_panel, height, angle)
            if host_info:
                run("feature.add_filling", model, opening=opening, element=product)
            _assign_simple_material(model, product, materials[ELEMENT_MATERIAL[typ]])
            pset_name = "Pset_DoorCommon" if typ == "Door" else "Pset_WindowCommon"
            pset_props = {
                "Reference": elem["id"],
                "IsExternal": host_info["category"] == "external" if host_info else False,
                "FireRating": "1HR",
                "ThermalTransmittance": 2.2 if typ == "Window" else 1.8,
            }
            if typ == "Door":
                pset_props["FireExit"] = False
                pset_props["HandicapAccessible"] = width >= 0.9
                pset_props["SelfClosing"] = False
            else:
                pset_props["GlazingAreaFraction"] = 0.75
                pset_props["SecurityRating"] = "Standard"
            _add_simple_pset(model, product, pset_name, pset_props)
            qto_name = "Qto_DoorBaseQuantities" if typ == "Door" else "Qto_WindowBaseQuantities"
            _add_qto(model, product, qto_name, {
                "Width": float(width),
                "Height": float(height),
                "Area": float(width * height),
                "Perimeter": float(2 * (width + height)),
            })
            _accumulate(boq, typ, ELEMENT_MATERIAL[typ], count=1, gross_area=float(width * height))
        elif typ == "Stair":
            x0, y0, x1, y1 = g["bbox"]
            w, d, h = x1 - x0, y1 - y0, g.get("height", h_default)
            entity = _box(model, body, st, "IfcStair", elem["id"], x0, y0, elev, w, d, h, 0.0)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Stair"]])
            risers = int(g.get("riser_count") or 18)
            _add_simple_pset(model, entity, "Pset_StairCommon", {
                "Reference": elem["id"],
                "IsExternal": False,
                "FireRating": "2HR",
                "NumberOfRiser": risers,
                "NumberOfTreads": max(risers - 1, 1),
                "RiserHeight": float(h / max(risers, 1)),
                "TreadLength": float(d / max(risers, 1)),
            })
            _add_qto(model, entity, "Qto_StairFlightBaseQuantities", {
                "Length": float(d),
                "GrossVolume": float(w * d * h * 0.5),
                "NetVolume": float(w * d * h * 0.5),
            })
            _accumulate(boq, "Stair", ELEMENT_MATERIAL["Stair"], count=1,
                        gross_volume=float(w * d * h * 0.5))
        elif typ == "Space":
            entity = _create_space(model, body, st, elem, elev)
            area = _polygon_area(g["profile"])
            height = float(g.get("height", h_default))
            perimeter = _polygon_perimeter(g["profile"])
            _add_simple_pset(model, entity, "Pset_SpaceCommon", {
                "Reference": elem["id"],
                "IsExternal": g.get("function") in ("courtyard", "garden", "balcony"),
                "PubliclyAccessible": bool(g.get("public", False)),
                "GrossPlannedArea": float(area),
                "NetPlannedArea": float(area),
                "Category": g.get("function", "general"),
            })
            _add_qto(model, entity, "Qto_SpaceBaseQuantities", {
                "Height": float(height),
                "NetFloorArea": float(area),
                "GrossFloorArea": float(area),
                "NetVolume": float(area * height),
                "GrossVolume": float(area * height),
                "NetPerimeter": float(perimeter),
                "GrossPerimeter": float(perimeter),
            })
            zone_name = g.get("zone")
            if zone_name:
                zones.setdefault(zone_name, {"spaces": []})["spaces"].append(entity)
            _accumulate(boq, "Space", g.get("function", "general"),
                        net_area=float(area), gross_volume=float(area * height))
        elif typ == "Pishtaq":
            cw = float(g.get("width", 8.0))
            ch = float(g.get("height", 10.0))
            ct = float(g.get("thickness", 0.5))
            center = g.get("center", [0.0, 0.0])
            entity = _box(model, body, st, "IfcCurtainWall", elem["id"],
                          center[0] - cw / 2.0, center[1] - ct / 2.0 - float(g.get("projection", 0.18)),
                          elev, cw, ct, ch, 0.0)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Pishtaq"]])
            _add_simple_pset(model, entity, "Pset_CurtainWallCommon", {
                "Reference": elem["id"],
                "IsExternal": True,
                "FireRating": "1HR",
                "AcousticRating": "Rw 40dB",
            })
            _add_simple_pset(model, entity, "Pset_PersianElement", {
                "Element": "Pishtaq",
                "HostIwan": g.get("host_iwan") or "",
                "CalligraphyBand": bool(g.get("calligraphy_band", True)),
            })
            _add_qto(model, entity, "Qto_CurtainWallBaseQuantities", {
                "Length": cw,
                "Height": ch,
                "Width": ct,
                "GrossSideArea": cw * ch,
                "NetSideArea": cw * ch * 0.55,
                "GrossVolume": cw * ch * ct,
            })
            _classify(model, entity, "Persian/Pishtaq", "Frame portal around iwan")
            _accumulate(boq, "Pishtaq", "brick", count=1, gross_area=cw * ch, gross_volume=cw * ch * ct)
        elif typ == "Pool":
            x0, y0, x1, y1 = _profile_bbox(g["profile"])
            depth = float(g.get("depth", 0.35))
            area = _polygon_area(g["profile"])
            space = _create_space(model, body, st, {
                "geometry": {"profile": g["profile"], "height": depth,
                             "name": f"Pool {elem['id']}", "function": "water"},
                "id": elem["id"], "level": elem["level"],
            }, elev - depth)
            _add_simple_pset(model, space, "Pset_SpaceCommon", {
                "Reference": elem["id"],
                "IsExternal": True,
                "PubliclyAccessible": True,
                "Category": "water",
                "GrossPlannedArea": float(area),
                "NetPlannedArea": float(area),
            })
            _add_simple_pset(model, space, "Pset_PersianElement", {
                "Element": "Pool",
                "WaterDepth_m": depth,
                "WaterVolume_m3": float(area * depth),
            })
            _add_qto(model, space, "Qto_SpaceBaseQuantities", {
                "Height": depth,
                "NetFloorArea": float(area),
                "GrossFloorArea": float(area),
                "NetVolume": float(area * depth),
                "GrossVolume": float(area * depth),
            })
            _classify(model, space, "Persian/Pool", "Reflecting pool in charbagh garden")
            _accumulate(boq, "Pool", "water", count=1, net_area=float(area),
                        gross_volume=float(area * depth))
        elif typ == "Canal":
            s, e = g["start"], g["end"]
            length = math.hypot(e[0] - s[0], e[1] - s[1])
            angle = math.atan2(e[1] - s[1], e[0] - s[0]) if length else 0.0
            ww = float(g.get("width", 1.0))
            depth = float(g.get("depth", 0.20))
            entity = run("root.create_entity", model, ifc_class="IfcBuildingElementProxy",
                         name=elem["id"], predefined_type="USERDEFINED")
            entity.ObjectType = "WaterChannel"
            rep = run("geometry.add_wall_representation", model, context=body,
                      length=max(length, 0.05), height=max(depth, 0.05), thickness=max(ww, 0.05))
            run("geometry.assign_representation", model, product=entity, representation=rep)
            cx0 = s[0] - math.sin(angle) * ww / 2.0
            cy0 = s[1] + math.cos(angle) * ww / 2.0
            run("geometry.edit_object_placement", model, product=entity,
                matrix=placement(cx0, cy0, elev - depth, angle), is_si=True)
            run("spatial.assign_container", model, products=[entity], relating_structure=st)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Canal"]])
            _add_simple_pset(model, entity, "Pset_PersianElement", {
                "Element": "Canal",
                "WaterDepth_m": depth,
                "Length_m": length,
                "Width_m": ww,
                "WaterVolume_m3": float(length * ww * depth),
            })
            _add_qto(model, entity, "Qto_BuildingElementProxyBaseQuantities", {
                "Length": float(length),
                "Width": ww,
                "Height": depth,
                "GrossVolume": float(length * ww * depth),
            })
            _classify(model, entity, "Persian/Canal", "Water channel in charbagh garden")
            _accumulate(boq, "Canal", "water", count=1,
                        gross_volume=float(length * ww * depth))
        elif typ == "Garden":
            area = _polygon_area(g["profile"])
            space = _create_space(model, body, st, {
                "geometry": {"profile": g["profile"], "height": 0.05,
                             "name": f"Garden {elem['id']}", "function": "garden"},
                "id": elem["id"], "level": elem["level"],
            }, elev)
            _add_simple_pset(model, space, "Pset_SpaceCommon", {
                "Reference": elem["id"],
                "IsExternal": True,
                "PubliclyAccessible": True,
                "Category": "garden",
                "GrossPlannedArea": float(area),
                "NetPlannedArea": float(area),
            })
            _add_simple_pset(model, space, "Pset_PersianElement", {
                "Element": "Garden",
                "PavingPattern": g.get("paving_pattern", "charbagh_4quad"),
            })
            _classify(model, space, "Persian/Charbagh", "Four-quadrant Persian garden")
            _accumulate(boq, "Garden", "garden", count=1, net_area=float(area))
        elif typ == "Screen":
            pw = float(g.get("panel_width", 1.2))
            ph = float(g.get("panel_height", 1.4))
            pt = float(g.get("thickness", 0.045))
            # Position screen at a default location (host_id resolution is best-effort)
            host_id = g.get("host_id") or g.get("host_wall") or ""
            entity = run("root.create_entity", model, ifc_class="IfcCovering",
                         name=elem["id"], predefined_type="CLADDING")
            rep = run("geometry.add_wall_representation", model, context=body,
                      length=pw, height=ph, thickness=pt)
            run("geometry.assign_representation", model, product=entity, representation=rep)
            run("geometry.edit_object_placement", model, product=entity,
                matrix=placement(0.0, 0.0, elev + 0.9, 0.0), is_si=True)
            run("spatial.assign_container", model, products=[entity], relating_structure=st)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Screen"]])
            _add_simple_pset(model, entity, "Pset_CoveringCommon", {
                "Reference": elem["id"],
                "IsExternal": False,
                "FireRating": "30min",
            })
            _add_simple_pset(model, entity, "Pset_PersianElement", {
                "Element": "MashrabiyaScreen",
                "Pattern": g.get("pattern", "lattice"),
                "CellSize_m": float(g.get("cell_size", 0.18)),
                "HostId": host_id,
            })
            _add_qto(model, entity, "Qto_CoveringBaseQuantities", {
                "Width": pw,
                "Height": ph,
                "GrossArea": pw * ph,
                "NetArea": pw * ph * 0.55,
            })
            _classify(model, entity, "Persian/Mashrabiya", "Wooden geometric screen")
            _accumulate(boq, "Screen", "wood", count=1, gross_area=pw * ph)
        elif typ in ("DuctSegment", "PipeSegment"):
            s, e = g["start"], g["end"]
            length = math.dist(s, e)
            if length <= 0:
                continue
            angle = math.atan2(e[1] - s[1], e[0] - s[0])
            dia = float(g.get("diameter", 0.30))
            offset = float(g.get("elevation_offset", -0.40))
            mep_z = elev + h_default + offset
            cls = "IfcDuctSegment" if typ == "DuctSegment" else "IfcPipeSegment"
            entity = _box(model, body, st, cls, elem["id"],
                          s[0], s[1], mep_z, length, dia, dia, angle)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL[typ]])
            pset_name = "Pset_DuctSegmentTypeCommon" if typ == "DuctSegment" else "Pset_PipeSegmentTypeCommon"
            _add_simple_pset(model, entity, pset_name, {
                "Reference": elem["id"],
                "System": g.get("system", "HVAC" if typ == "DuctSegment" else "ColdWater"),
                "NominalDiameter": dia,
            })
            qto_name = "Qto_DuctSegmentBaseQuantities" if typ == "DuctSegment" else "Qto_PipeSegmentBaseQuantities"
            _add_qto(model, entity, qto_name, {
                "Length": float(length),
                "CrossSectionArea": float(math.pi * (dia / 2) ** 2),
                "OuterSurfaceArea": float(math.pi * dia * length),
                "GrossWeight": float(length * dia * 0.005 * 7850),
            })
            _classify(model, entity, f"MEP/{typ}", g.get("system", ""))
            _accumulate(boq, typ, "steel", length=float(length))
        elif typ == "LightFixture":
            c = g["center"]
            light_z = elev + h_default - 0.05
            entity = _box(model, body, st, "IfcLightFixture", elem["id"],
                          c[0] - 0.18, c[1] - 0.18, light_z - 0.04, 0.36, 0.36, 0.06, 0.0)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["LightFixture"]])
            _add_simple_pset(model, entity, "Pset_LightFixtureTypeCommon", {
                "Reference": elem["id"],
                "TotalWattage": 36.0,
                "LightFixtureMountingType": "SURFACE",
            })
            _classify(model, entity, "MEP/Lighting", "Ceiling fixture")
            _accumulate(boq, "LightFixture", "steel", count=1)
        elif typ == "Beam":
            s, e = g["start"], g["end"]
            length = math.dist(s, e)
            if length <= 0:
                continue
            angle = math.atan2(e[1] - s[1], e[0] - s[0])
            bw = float(g.get("width", 0.4))
            bh = float(g.get("height", 0.6))
            beam_z = elev + h_default - bh
            entity = _box(model, body, st, "IfcBeam", elem["id"],
                          s[0], s[1], beam_z, length, bw, bh, angle)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Beam"]])
            _add_simple_pset(model, entity, "Pset_BeamCommon", {
                "Reference": elem["id"],
                "LoadBearing": True,
                "IsExternal": False,
                "FireRating": "2HR",
                "Span": float(length),
            })
            _add_qto(model, entity, "Qto_BeamBaseQuantities", {
                "Length": float(length),
                "CrossSectionArea": float(bw * bh),
                "OuterSurfaceArea": float(2 * (bw + bh) * length),
                "GrossVolume": float(length * bw * bh),
                "NetVolume": float(length * bw * bh),
            })
            _classify(model, entity, "Structure/Beam", "Reinforced concrete beam")
            _accumulate(boq, "Beam", "concrete", length=float(length),
                        gross_volume=float(length * bw * bh))
        elif typ == "Footing":
            c = g["center"]
            sz = g.get("size", [1.5, 1.5])
            ft_t = float(g.get("thickness", 0.6))
            top = float(g.get("top_z", elev))
            x0 = c[0] - sz[0] / 2.0
            y0 = c[1] - sz[1] / 2.0
            entity = _box(model, body, st, "IfcFooting", elem["id"],
                          x0, y0, top - ft_t, sz[0], sz[1], ft_t, 0.0)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Footing"]])
            _add_simple_pset(model, entity, "Pset_FootingCommon", {
                "Reference": elem["id"],
                "LoadBearing": True,
                "IsExternal": False,
                "FireRating": "4HR",
            })
            _add_qto(model, entity, "Qto_FootingBaseQuantities", {
                "Width": float(sz[0]),
                "Length": float(sz[1]),
                "Depth": float(ft_t),
                "GrossVolume": float(sz[0] * sz[1] * ft_t),
                "NetVolume": float(sz[0] * sz[1] * ft_t),
            })
            _classify(model, entity, "Structure/Footing", "Concrete pad footing")
            _accumulate(boq, "Footing", "concrete", count=1,
                        gross_volume=float(sz[0] * sz[1] * ft_t))
        elif typ == "Muqarnas":
            tiers = int(g.get("tiers", 4))
            cells = int(g.get("cells_base", 10))
            entity = run("root.create_entity", model, ifc_class="IfcCovering",
                         name=elem["id"], predefined_type="CEILING")
            rep = run("geometry.add_wall_representation", model, context=body,
                      length=2.0, height=2.0, thickness=1.5)
            run("geometry.assign_representation", model, product=entity, representation=rep)
            run("geometry.edit_object_placement", model, product=entity,
                matrix=placement(0.0, 0.0, elev + h_default * 0.7, 0.0), is_si=True)
            run("spatial.assign_container", model, products=[entity], relating_structure=st)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Muqarnas"]])
            _add_simple_pset(model, entity, "Pset_CoveringCommon", {
                "Reference": elem["id"],
                "IsExternal": False,
                "FireRating": "1HR",
            })
            _add_simple_pset(model, entity, "Pset_PersianElement", {
                "Element": "Muqarnas",
                "TierCount": tiers,
                "CellsBase": cells,
                "Half": bool(g.get("half", True)),
                "HostIwan": g.get("host_iwan") or "",
            })
            _classify(model, entity, "Persian/Muqarnas", "Stalactite cellular vault")
            _accumulate(boq, "Muqarnas", "plaster", count=1)
        elif typ == "Roof":
            x0, y0, x1, y1 = _profile_bbox(g["profile"])
            w, d = x1 - x0, y1 - y0
            t = g.get("height", 0.2)
            top = max(float(l["elevation_m"]) + float(l["height_m"]) for l in levels)
            area = _polygon_area(g["profile"])
            entity = _box(model, body, st, "IfcRoof", elem["id"], x0, y0, top, w, d, t, 0.0)
            _assign_simple_material(model, entity, materials[ELEMENT_MATERIAL["Roof"]])
            _add_simple_pset(model, entity, "Pset_RoofCommon", {
                "Reference": elem["id"],
                "IsExternal": True,
                "FireRating": "1HR",
                "ProjectedArea": float(area),
                "TotalArea": float(area),
            })
            _add_qto(model, entity, "Qto_RoofBaseQuantities", {
                "GrossArea": float(area),
                "NetArea": float(area),
                "ProjectedArea": float(area),
                "GrossVolume": float(area * t),
            })
            _accumulate(boq, "Roof", ELEMENT_MATERIAL["Roof"], net_area=float(area),
                        gross_volume=float(area * t))

    # After all openings have voided walls, write wall quantities and finalize.
    for wid, info in wall_index.items():
        net_side_area = max(info["length"] * info["height"] - info["openings_area"], 0.0)
        net_volume = max(info["length"] * info["height"] * info["thickness"]
                         - info["openings_area"] * info["thickness"], 0.0)
        _add_simple_pset(model, info["entity"], "Pset_WallCommon", {
            "Reference": wid,
            "IsExternal": info["category"] in ("external", "courtyard"),
            "LoadBearing": info["category"] in ("external", "courtyard"),
            "FireRating": "2HR" if info["category"] in ("external", "courtyard") else "1HR",
            "ThermalTransmittance": 0.35 if info["category"] == "external" else 0.9,
            "AcousticRating": "Rw 50dB" if info["category"] in ("external", "courtyard") else "Rw 35dB",
            "Combustible": False,
            "ExtendToStructure": True,
        })
        _add_qto(model, info["entity"], "Qto_WallBaseQuantities", {
            "Length": float(info["length"]),
            "Width": float(info["thickness"]),
            "Height": float(info["height"]),
            "GrossSideArea": float(info["length"] * info["height"]),
            "NetSideArea": float(net_side_area),
            "GrossVolume": float(info["length"] * info["height"] * info["thickness"]),
            "NetVolume": float(net_volume),
            "GrossFootprintArea": float(info["length"] * info["thickness"]),
        })
        # subtract opening contributions from the gross-initialised net values
        by = boq["categories"]["Wall"]["by_category"][info["category"]]
        by["net_side_area"] = max(by["net_side_area"] - info["openings_area"], 0.0)
        by["net_volume"] = max(by["net_volume"] - info["openings_area"] * info["thickness"], 0.0)

    # IfcZone groups for spaces with the same `zone` name
    for zone_name, info in zones.items():
        zone_entity = run("root.create_entity", model, ifc_class="IfcZone", name=zone_name)
        run("group.assign_group", model, products=info["spaces"], group=zone_entity)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(out))
    storey_stats = [{"name": lv["name"], "elevation_m": lv["elevation_m"], "height_m": lv["height_m"]} for lv in levels]
    boq_path = out.with_suffix(".boq.json")
    _finalize_and_write_boq(boq, boq_path)
    return {
        "elements": counts,
        "components": sum(counts.values()),
        "storeys": storey_stats,
        "boq_path": str(boq_path),
        "structural_totals": boq.get("structural_totals", {}),
        "spatial_totals": boq.get("spatial_totals", {}),
        "openings": int(counts.get("Door", 0) + counts.get("Window", 0)),
        "spaces": int(counts.get("Space", 0)),
        "zones": len(zones),
    }


def _new_model(name: str):
    model = run("project.create_file", version="IFC4")
    run("root.create_entity", model, ifc_class="IfcProject", name=name)
    metre = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    run("unit.assign_unit", model, units=[metre])
    ctx = run("context.add_context", model, context_type="Model")
    body = run("context.add_context", model, context_type="Model", context_identifier="Body",
               target_view="MODEL_VIEW", parent=ctx)
    project = model.by_type("IfcProject")[0]
    site = run("root.create_entity", model, ifc_class="IfcSite", name="Site")
    building = run("root.create_entity", model, ifc_class="IfcBuilding", name=name)
    run("aggregate.assign_object", model, products=[site], relating_object=project)
    run("aggregate.assign_object", model, products=[building], relating_object=site)
    return model, body, building


def _box(model, body, storey, ifc_class, name, x, y, z, length, width, height, angle):
    product = run("root.create_entity", model, ifc_class=ifc_class, name=name)
    rep = run("geometry.add_wall_representation", model, context=body,
              length=max(float(length), 0.01), height=max(float(height), 0.01),
              thickness=max(float(width), 0.01), offset=0)
    run("geometry.assign_representation", model, product=product, representation=rep)
    run("geometry.edit_object_placement", model, product=product,
        matrix=placement(float(x), float(y), float(z), float(angle)), is_si=True)
    run("spatial.assign_container", model, products=[product], relating_structure=storey)
    return product


def _create_opening(model, body, storey, name, x, y, z, length, thickness, height, angle):
    opening = run("root.create_entity", model, ifc_class="IfcOpeningElement", name=f"{name}_opening")
    rep = run("geometry.add_wall_representation", model, context=body,
              length=max(float(length), 0.01), height=max(float(height), 0.01),
              thickness=max(float(thickness), 0.01))
    run("geometry.assign_representation", model, product=opening, representation=rep)
    run("geometry.edit_object_placement", model, product=opening,
        matrix=placement(float(x), float(y), float(z), float(angle)), is_si=True)
    return opening


def _opening_corner(center, angle, length, perpendicular_thickness):
    """World coordinate of the corner where add_wall_representation starts, so that
    the resulting box is centred on `center` along the length axis and on the wall
    centreline along the perpendicular axis."""
    dx, dy = math.cos(angle), math.sin(angle)
    nx, ny = -math.sin(angle), math.cos(angle)
    half_len = length / 2.0
    half_th = perpendicular_thickness / 2.0
    x0 = center[0] - dx * half_len - nx * half_th
    y0 = center[1] - dy * half_len - ny * half_th
    return x0, y0


def _profile_bbox(profile):
    xs = [p[0] for p in profile]
    ys = [p[1] for p in profile]
    return min(xs), min(ys), max(xs), max(ys)


def _polygon_area(poly):
    area = 0.0
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        area += a[0] * b[1] - b[0] * a[1]
    return abs(area) * 0.5


def _polygon_perimeter(poly):
    total = 0.0
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def _create_space(model, body, storey, elem, z):
    g = elem["geometry"]
    name = g.get("name") or elem["id"]
    space = run("root.create_entity", model, ifc_class="IfcSpace", name=name)
    polyline = [(p[0], p[1]) for p in g["profile"]]
    if polyline[0] != polyline[-1]:
        polyline.append(polyline[0])
    rep = run("geometry.add_slab_representation", model, context=body,
              depth=max(float(g.get("height", 3.0)), 0.01), polyline=polyline)
    run("geometry.assign_representation", model, product=space, representation=rep)
    run("geometry.edit_object_placement", model, product=space,
        matrix=placement(0.0, 0.0, float(z), 0.0), is_si=True)
    run("aggregate.assign_object", model, products=[space], relating_object=storey)
    return space


def _create_materials(model):
    mats = {}
    for name, category in [
        ("concrete", "concrete"),
        ("brick", "brick"),
        ("insulation", "insulation"),
        ("kashi_tile", "ceramic"),
        ("plaster", "plaster"),
        ("wood", "wood"),
        ("glass", "glass"),
        ("steel", "steel"),
        ("stone", "stone"),
    ]:
        mats[name] = run("material.add_material", model, name=name.upper(), category=category)
    return mats


def _create_wall_layer_sets(model, materials):
    sets = {}
    for category, layers in WALL_MATERIAL_PRESETS.items():
        ms = run("material.add_material_set", model,
                 name=f"WALL_{category.upper()}", set_type="IfcMaterialLayerSet")
        for mat_name, thickness in layers:
            layer = run("material.add_layer", model, layer_set=ms, material=materials[mat_name])
            run("material.edit_layer", model, layer=layer, attributes={"LayerThickness": float(thickness)})
        sets[category] = {"set": ms, "layers": layers}
    return sets


def _assign_wall_material(model, wall, wall_layer_sets, category):
    info = wall_layer_sets.get(category) or wall_layer_sets["internal"]
    run("material.assign_material", model, products=[wall],
        type="IfcMaterialLayerSet", material=info["set"])


def _assign_simple_material(model, product, material):
    run("material.assign_material", model, products=[product],
        type="IfcMaterial", material=material)


def _add_simple_pset(model, product, pset_name, properties):
    pset = run("pset.add_pset", model, product=product, name=pset_name)
    run("pset.edit_pset", model, pset=pset, properties=properties)


def _add_wall_pset(model, product, category, elem):
    # Placeholder so that wall_index can re-call with full quantities later; we use
    # _add_simple_pset directly in the finalization pass and skip here to avoid
    # double-writing.
    return


def _add_qto(model, product, qto_name, quantities):
    qto = run("pset.add_qto", model, product=product, name=qto_name)
    run("pset.edit_qto", model, qto=qto, properties=quantities)


def _empty_boq():
    return {
        "categories": {},
        "totals": {
            "length_m": 0.0,
            "net_side_area_m2": 0.0,
            "gross_side_area_m2": 0.0,
            "gross_area_m2": 0.0,
            "net_area_m2": 0.0,
            "gross_volume_m3": 0.0,
            "net_volume_m3": 0.0,
            "mass_kg": 0.0,
            "count": 0,
        },
    }


def _accumulate(boq, element_type, category, length=0.0, gross_side_area=0.0,
                net_side_area=None, gross_area=0.0, net_area=None,
                gross_volume=0.0, net_volume=None, count=0, layers=None):
    if net_side_area is None:
        net_side_area = gross_side_area
    if net_area is None:
        net_area = gross_area
    if net_volume is None:
        net_volume = gross_volume
    cats = boq["categories"].setdefault(element_type, {"by_category": {}, "totals": {}})
    by = cats["by_category"].setdefault(category, {
        "count": 0, "length": 0.0,
        "gross_side_area": 0.0, "net_side_area": 0.0,
        "gross_area": 0.0, "net_area": 0.0,
        "gross_volume": 0.0, "net_volume": 0.0,
        "layers": layers or [],
        "mass_kg": 0.0,
    })
    by["count"] += 1 if count == 0 else count
    by["length"] += length
    by["gross_side_area"] += gross_side_area
    by["net_side_area"] += net_side_area
    by["gross_area"] += gross_area
    by["net_area"] += net_area
    by["gross_volume"] += gross_volume
    by["net_volume"] += net_volume


def _finalize_and_write_boq(boq, path):
    import json
    grand = {"length_m": 0.0, "net_side_area_m2": 0.0, "gross_side_area_m2": 0.0,
             "gross_area_m2": 0.0, "net_area_m2": 0.0, "gross_volume_m3": 0.0,
             "net_volume_m3": 0.0, "mass_kg": 0.0, "count": 0}
    structural = {"net_volume_m3": 0.0, "mass_kg": 0.0, "count": 0}
    spatial = {"net_floor_area_m2": 0.0, "net_volume_m3": 0.0, "count": 0}
    for elem_type, cats in boq["categories"].items():
        type_total = {"length_m": 0.0, "net_side_area_m2": 0.0, "gross_side_area_m2": 0.0,
                      "gross_area_m2": 0.0, "net_area_m2": 0.0, "gross_volume_m3": 0.0,
                      "net_volume_m3": 0.0, "count": 0, "mass_kg": 0.0}
        for category, vals in cats["by_category"].items():
            mass = 0.0
            if elem_type == "Space":
                # Spaces are void; volume tracked but no mass contribution.
                mass = 0.0
            elif vals["layers"]:
                total_t = sum(t for _, t in vals["layers"]) or 1.0
                for mat_name, t in vals["layers"]:
                    layer_vol = vals["net_volume"] * (t / total_t)
                    mass += layer_vol * MATERIAL_DENSITY_KG_M3.get(mat_name, 1800.0)
            else:
                mass = vals["net_volume"] * MATERIAL_DENSITY_KG_M3.get(category, 1800.0)
            vals["mass_kg"] = round(mass, 1)
            type_total["length_m"] += vals["length"]
            type_total["net_side_area_m2"] += vals["net_side_area"]
            type_total["gross_side_area_m2"] += vals["gross_side_area"]
            type_total["gross_area_m2"] += vals["gross_area"]
            type_total["net_area_m2"] += vals["net_area"]
            type_total["gross_volume_m3"] += vals["gross_volume"]
            type_total["net_volume_m3"] += vals["net_volume"]
            type_total["count"] += vals["count"]
            type_total["mass_kg"] += vals["mass_kg"]
            for k in ("length", "gross_side_area", "net_side_area",
                      "gross_area", "net_area", "gross_volume", "net_volume"):
                vals[k] = round(vals[k], 3)
        for k in type_total:
            grand[k] += type_total[k]
            type_total[k] = round(type_total[k], 3)
        cats["totals"] = type_total
        if elem_type == "Space":
            spatial["net_floor_area_m2"] += type_total["net_area_m2"]
            spatial["net_volume_m3"] += type_total["net_volume_m3"]
            spatial["count"] += type_total["count"]
        else:
            structural["net_volume_m3"] += type_total["net_volume_m3"]
            structural["mass_kg"] += type_total["mass_kg"]
            structural["count"] += type_total["count"]
    for k in grand:
        grand[k] = round(grand[k], 3)
    for k in structural:
        structural[k] = round(structural[k], 3)
    for k in spatial:
        spatial[k] = round(spatial[k], 3)
    boq["totals"] = grand
    boq["structural_totals"] = structural
    boq["spatial_totals"] = spatial
    path.write_text(json.dumps(boq, ensure_ascii=False, indent=2), encoding="utf-8")
