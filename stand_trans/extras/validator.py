"""IFC4 validator for stand_trans output: required Psets, opening integrity,
material coverage, quantity sanity, spatial hierarchy and GUID uniqueness."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import ifcopenshell


REQUIRED_PSETS = {
    "IfcWall": ["Pset_WallCommon"],
    "IfcDoor": ["Pset_DoorCommon"],
    "IfcWindow": ["Pset_WindowCommon"],
    "IfcColumn": ["Pset_ColumnCommon"],
    "IfcSlab": ["Pset_SlabCommon"],
    "IfcRoof": ["Pset_RoofCommon"],
    "IfcStair": ["Pset_StairCommon"],
    "IfcSpace": ["Pset_SpaceCommon"],
    "IfcBeam": ["Pset_BeamCommon"],
    "IfcFooting": ["Pset_FootingCommon"],
    "IfcCurtainWall": ["Pset_CurtainWallCommon"],
    "IfcCovering": ["Pset_CoveringCommon"],
}

LOAD_BEARING_CLASSES = ("IfcWall", "IfcColumn", "IfcBeam", "IfcSlab", "IfcRoof", "IfcFooting")

POSITIVE_QTY_NAMES = (
    "Length", "Height", "Width", "Depth",
    "Area", "GrossSideArea", "NetSideArea",
    "GrossArea", "NetArea", "GrossPlannedArea", "NetPlannedArea",
    "GrossVolume", "NetVolume", "CrossSectionArea",
    "GrossFloorArea", "NetFloorArea",
)


def validate(ifc_path: str | Path, out_path: str | Path | None = None) -> dict:
    model = ifcopenshell.open(str(ifc_path))
    issues: list[dict] = []

    def add(severity: str, code: str, msg: str, ref: str | None = None):
        issues.append({"severity": severity, "code": code, "message": msg, "ref": ref})

    # 1. GUID uniqueness
    guid_counts = Counter(p.GlobalId for p in model.by_type("IfcRoot") if p.GlobalId)
    for guid, n in guid_counts.items():
        if n > 1:
            add("error", "DUPLICATE_GUID", f"GlobalId {guid} occurs {n} times", guid)

    # 2. Missing names
    for product in model.by_type("IfcProduct"):
        if not getattr(product, "Name", None):
            add("info", "MISSING_NAME", f"{product.is_a()} has no Name", product.GlobalId)

    # 3. Required Psets
    pset_index: dict[str, set[str]] = {}
    for obj in model.by_type("IfcObject"):
        if not obj.GlobalId:
            continue
        names: set[str] = set()
        for rel in obj.IsDefinedBy or []:
            if rel.is_a("IfcRelDefinesByProperties"):
                pd = rel.RelatingPropertyDefinition
                if pd.is_a("IfcPropertySet"):
                    names.add(pd.Name)
        pset_index[obj.GlobalId] = names

    for cls, required in REQUIRED_PSETS.items():
        for product in model.by_type(cls):
            names = pset_index.get(product.GlobalId, set())
            for r in required:
                if r not in names:
                    add("warning", "MISSING_PSET",
                        f"{cls} '{product.Name}' missing {r}", product.GlobalId)

    # 4. Opening/voiding integrity
    voided_walls: set[str] = set()
    for r in model.by_type("IfcRelVoidsElement"):
        wall = r.RelatingBuildingElement
        opening = r.RelatedOpeningElement
        if wall is None:
            add("error", "VOID_NO_HOST",
                f"IfcRelVoidsElement {r.GlobalId} has no host element", r.GlobalId)
        if opening is None:
            add("error", "VOID_NO_OPENING",
                f"IfcRelVoidsElement {r.GlobalId} has no opening", r.GlobalId)
        if wall:
            voided_walls.add(wall.GlobalId)

    filled_openings: set[str] = set()
    for r in model.by_type("IfcRelFillsElement"):
        op = r.RelatingOpeningElement
        if op:
            filled_openings.add(op.GlobalId)

    for opening in model.by_type("IfcOpeningElement"):
        if opening.GlobalId not in filled_openings:
            add("info", "OPENING_UNFILLED",
                f"IfcOpeningElement '{opening.Name}' not filled by door or window",
                opening.GlobalId)

    # 5. Material assignment for load-bearing classes
    has_material: set[str] = set()
    for r in model.by_type("IfcRelAssociatesMaterial"):
        for obj in r.RelatedObjects or []:
            has_material.add(obj.GlobalId)
    for cls in LOAD_BEARING_CLASSES:
        for p in model.by_type(cls):
            if p.GlobalId not in has_material:
                add("warning", "NO_MATERIAL",
                    f"{cls} '{p.Name}' has no material", p.GlobalId)

    # 6. Quantity sanity
    for qto in model.by_type("IfcElementQuantity"):
        for q in qto.Quantities or []:
            value = (getattr(q, "AreaValue", None)
                     or getattr(q, "LengthValue", None)
                     or getattr(q, "VolumeValue", None)
                     or getattr(q, "WeightValue", None)
                     or getattr(q, "CountValue", None))
            if q.Name in POSITIVE_QTY_NAMES and value is not None and float(value) < 0:
                add("error", "NEGATIVE_QUANTITY",
                    f"{qto.Name}.{q.Name} = {value}", qto.GlobalId)

    # 7. Spatial hierarchy
    storeys = model.by_type("IfcBuildingStorey")
    if not storeys:
        add("error", "NO_STOREY", "Model has no IfcBuildingStorey")
    if not model.by_type("IfcBuilding"):
        add("error", "NO_BUILDING", "Model has no IfcBuilding")

    # 8. Spaces must be aggregated to a storey
    space_parent: dict[str, str | None] = {sp.GlobalId: None for sp in model.by_type("IfcSpace")}
    for r in model.by_type("IfcRelAggregates"):
        parent = r.RelatingObject
        for child in r.RelatedObjects or []:
            if child.GlobalId in space_parent:
                space_parent[child.GlobalId] = parent.GlobalId if parent else None
    for sp in model.by_type("IfcSpace"):
        if not space_parent.get(sp.GlobalId):
            add("warning", "SPACE_UNAGG",
                f"Space '{sp.Name}' not aggregated to storey", sp.GlobalId)

    # 9. Classification coverage (informational)
    classified: set[str] = set()
    for r in model.by_type("IfcRelAssociatesClassification"):
        for obj in r.RelatedObjects or []:
            classified.add(obj.GlobalId)
    proxy_classes = ("IfcCurtainWall", "IfcCovering", "IfcBuildingElementProxy")
    for cls in proxy_classes:
        for p in model.by_type(cls):
            if p.GlobalId not in classified:
                add("info", "NO_CLASSIFICATION",
                    f"{cls} '{p.Name}' has no classification reference", p.GlobalId)

    severity_counts = Counter(i["severity"] for i in issues)
    summary = {
        "ifc_path": str(ifc_path),
        "ok": severity_counts.get("error", 0) == 0,
        "counts": {
            "error": severity_counts.get("error", 0),
            "warning": severity_counts.get("warning", 0),
            "info": severity_counts.get("info", 0),
        },
        "total_entities": len(model.by_type("IfcRoot")),
        "by_class": {cls: len(model.by_type(cls)) for cls in sorted({
            "IfcWall", "IfcColumn", "IfcSlab", "IfcDoor", "IfcWindow",
            "IfcStair", "IfcRoof", "IfcSpace", "IfcZone",
            "IfcBeam", "IfcFooting",
            "IfcOpeningElement", "IfcCovering", "IfcCurtainWall",
            "IfcBuildingElementProxy",
            "IfcDuctSegment", "IfcPipeSegment", "IfcLightFixture",
            "IfcMaterial", "IfcMaterialLayerSet",
            "IfcRelVoidsElement", "IfcRelFillsElement",
            "IfcClassificationReference",
        })},
        "issues": issues,
    }

    if out_path is not None:
        Path(out_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return summary
