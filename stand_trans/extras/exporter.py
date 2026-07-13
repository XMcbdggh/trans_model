"""IFC -> GLB exporter."""
from __future__ import annotations

import math
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import numpy as np
import trimesh


def ifc_to_glb(ifc_path: str | Path, glb_path: str | Path) -> dict:
    model = ifcopenshell.open(str(ifc_path))
    settings = ifcopenshell.geom.settings()
    try:
        settings.set("use-world-coords", True)
    except Exception:
        pass
    iterator = ifcopenshell.geom.iterator(settings, model)
    meshes = []
    if iterator.initialize():
        while True:
            geom = iterator.get().geometry
            vertices = np.array(geom.verts, dtype=float).reshape(-1, 3)
            faces = np.array(geom.faces, dtype=np.int64).reshape(-1, 3)
            if len(vertices) and len(faces):
                meshes.append(trimesh.Trimesh(vertices=vertices, faces=faces, process=False))
            if not iterator.next():
                break
    if not meshes:
        raise RuntimeError("IFC has no triangulatable geometry")
    scene = trimesh.Scene(meshes)
    scene.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    out = Path(glb_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    scene.export(str(out))
    bounds = scene.bounds
    return {
        "components": len(meshes),
        "vertices": int(sum(len(m.vertices) for m in meshes)),
        "bbox_min_m": [round(float(v), 3) for v in bounds[0]],
        "bbox_max_m": [round(float(v), 3) for v in bounds[1]],
    }

