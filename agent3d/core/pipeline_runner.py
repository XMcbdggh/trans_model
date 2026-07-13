"""Run the stand_trans pipeline on a param.json and collect viewer-ready artifacts.

Given a param.json (or a param dict), produces into ``<scene_dir>``:
  * model.glb        -- visual 3D model (three.js GLTFLoader)
  * model.litematic  -- Minecraft voxel schematic (KEPT for the future blast module)
  * voxels.json      -- {dims, palette, palette_ids, blocks:[x,y,z,idx...], count, pitch_m}
                        decoded from the litematic so the browser viewer needs no NBT parser
  * param.json       -- the exact pipeline input (for reproducibility / debugging)
  * result.json      -- stand_trans stats

Requires the stand_trans package (repo root) to be importable.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from stand_trans.pipeline import convert                     # noqa: E402
from stand_trans.step4_voxel import litematic_to_voxels      # noqa: E402
from stand_trans.shared import materials as _materials       # noqa: E402


def attach_resistance(vox: dict) -> dict:
    """Add ``palette_resistance`` (blast overpressure threshold, kPa) and
    ``palette_material`` (material key) to a decoded-voxels dict, aligned to
    ``palette_ids``.

    This is the browser blast module's single source of truth: the viewer reads
    per-voxel resistance straight from voxels.json instead of hard-coding its own
    block->kPa table (which would drift from the backend). Values come from
    ``stand_trans/shared/materials.py`` -- the authoritative material table that
    also drives the Minecraft block and display colour. Unknown blocks fall back
    to 100 kPa (matches blast.py's DEFAULT_RESISTANCE); air is 0."""
    res_by_block = _materials.block_resistance()                 # block id -> blast_kPa
    mat_by_block = {m["block"]: key for key, m in _materials.MATERIALS.items()}
    res: list[float] = []
    mats: list[str] = []
    for bid in vox.get("palette_ids") or []:
        if bid == "minecraft:air":
            res.append(0.0)
            mats.append("air")
        else:
            res.append(float(res_by_block.get(bid, 100.0)))
            mats.append(mat_by_block.get(bid, ""))
    vox["palette_resistance"] = res
    vox["palette_material"] = mats
    return vox


def attach_mesh_colors(vox: dict, sidecar_path) -> dict:
    """Inject per-voxel REAL display colours (the GLB mesh face colours) into a decoded-
    voxels dict so the browser voxel view shows the rich ~20-colour palette instead of the
    ~9 monotonous Minecraft block colours.

    Reads ``color_palette`` (deduped hex list) and ``colors`` (per-voxel index into it)
    from the ``.voxelclass.json`` sidecar written by ``build_litematic`` -- both already
    aligned to the same voxel order as ``vox["blocks"]``. Injects them as ``mesh_palette``
    / ``mesh_colors``. Silently no-ops (viewer then falls back to the block palette) if the
    sidecar is missing, unparseable, or the arrays don't line up with the voxel count."""
    try:
        doc = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    except Exception:
        return vox
    pal, cols = doc.get("color_palette"), doc.get("colors")
    if isinstance(pal, list) and isinstance(cols, list) and len(cols) == vox.get("count"):
        vox["mesh_palette"] = pal
        vox["mesh_colors"] = cols
    return vox


def attach_materials(vox: dict, sidecar_path) -> dict:
    """Inject a per-voxel MATERIAL index into a decoded-voxels dict so the browser voxel
    view can sample a per-material texture tile from the block atlas (textured cubes read
    far more "real" than flat-coloured ones, at zero extra geometry cost).

    Reads ``materials`` (per-voxel index into ``material_legend``) and ``material_legend``
    (ordered material metadata) from the ``.voxelclass.json`` sidecar written by
    ``build_litematic`` -- both already aligned to the same voxel order as ``vox["blocks"]``.
    Injects ``mat_indices`` (per-voxel int) + ``mat_keys`` (index -> material key string).
    The atlas tile for each key is resolved on the client (see voxel_material.js). Silently
    no-ops (viewer then keeps the flat per-voxel colour) if the sidecar is missing, stale,
    or the arrays don't line up with the voxel count."""
    try:
        doc = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    except Exception:
        return vox
    mats = doc.get("materials")
    legend = doc.get("material_legend")
    if (isinstance(mats, list) and isinstance(legend, list)
            and len(mats) == vox.get("count") and legend):
        vox["mat_indices"] = mats
        vox["mat_keys"] = [m.get("id") for m in legend]
    return vox

# surface (and matching color material) for the auto ground, keyed by style preset
_GROUND_BY_STYLE = {
    "persian":   ("sand", "sand"),
    "islamic":   ("sand", "sand"),
    "modern":    ("concrete", "concrete_light"),
    "classical": ("grass", "foliage"),
}


def ensure_site_ground(param: dict, *, margin_m: float = 2.0, thickness_m: float = 0.3) -> dict:
    """Guarantee a continuous ground plane under the whole scene.

    Many models emit only partial floor patches (a couple of gardens/pools) and no
    site-wide ``terrain``, leaving the compound interior bare. If no ``terrain`` is
    present we inject ONE flat ground covering the XY bounding box of every built
    element (+ margin), at the base level. Idempotent — a model-provided terrain wins.
    Disable with env AGENT3D_AUTO_GROUND=0."""
    if os.getenv("AGENT3D_AUTO_GROUND", "1") == "0":
        return param
    if param.get("terrain"):            # respect a model-provided ground
        return param
    if param.get("ships"):              # ships float on their own (GLB-only) sea; no land slab
        return param

    xs: list[float] = []
    ys: list[float] = []

    def _pt(p):
        if isinstance(p, (list, tuple)) and len(p) >= 2 \
                and all(isinstance(c, (int, float)) for c in p[:2]):
            xs.append(float(p[0]))
            ys.append(float(p[1]))

    for w in param.get("walls", []):
        _pt(w.get("start"))
        _pt(w.get("end"))
    for coll in ("slabs", "roofs", "gardens", "pools", "rooms"):
        for it in param.get(coll, []):
            poly = it.get("polygon")
            if isinstance(poly, list):
                for pt in poly:
                    _pt(pt)
            bb = it.get("bbox")
            if isinstance(bb, list) and len(bb) == 4:
                _pt([bb[0], bb[1]])
                _pt([bb[2], bb[3]])

    levels = param.get("levels") or []
    if not xs or not ys or not levels:
        return param
    base_level = levels[0].get("name")
    if not base_level:
        return param

    preset = ((param.get("style") or {}).get("preset")
              or (param.get("project") or {}).get("style") or "modern").lower()
    surface, material = _GROUND_BY_STYLE.get(preset, ("grass", "foliage"))

    param.setdefault("terrain", []).append({
        "id": "auto_ground",
        "level": base_level,
        "bbox": [round(min(xs) - margin_m, 2), round(min(ys) - margin_m, 2),
                 round(max(xs) + margin_m, 2), round(max(ys) + margin_m, 2)],
        "thickness_m": thickness_m,
        "surface": surface,
        "material": material,
    })
    return param


def _pack_voxels(scene_dir: Path, lit_dst: Path, name: str, blocks_per_meter: float) -> dict:
    """Decode a litematic into ``voxels.json`` for the browser viewer/blast, with the
    per-palette blast resistance and the real GLB display colours attached. Shared by
    build_scene() and voxelize_scene(). Returns the decoded vox dict."""
    vox = litematic_to_voxels(lit_dst)
    vox["pitch_m"] = 1.0 / float(blocks_per_meter)            # so the viewer can scale to metres
    attach_resistance(vox)                                     # per-palette blast_kPa for the browser blast module
    sidecar = scene_dir / f"{name}.voxelclass.json"
    attach_mesh_colors(vox, sidecar)                           # real GLB colours for the voxel view
    attach_materials(vox, sidecar)                             # per-voxel material -> atlas tile (textured voxels)
    (scene_dir / "voxels.json").write_text(json.dumps(vox), encoding="utf-8")
    return vox


def build_scene(param, scene_dir, *, name="scene", blocks_per_meter: float = 4.0,
                make_voxels: bool = True, progress=None) -> dict:
    """param (dict or path) -> artifacts in scene_dir. Returns a manifest dict.

    make_voxels=False builds only the visual GLB (skips the litematic + voxels.json),
    which is much faster; voxels can be produced later at a chosen resolution with
    voxelize_scene(). convert() always persists the normalized param + BIM, so
    voxelize_scene() needs no regeneration afterwards."""
    scene_dir = Path(scene_dir)
    scene_dir.mkdir(parents=True, exist_ok=True)

    # accept a dict or an existing file path
    if isinstance(param, (str, Path)):
        param_path = Path(param)
    else:
        param = ensure_site_ground(param)   # fill a continuous ground if the model omitted it
        param_path = scene_dir / "param.json"
        param_path.write_text(json.dumps(param, ensure_ascii=False, indent=2), encoding="utf-8")

    res = convert(param_path, out_dir=scene_dir, name=name, make_glb=True,
                  make_litematic=make_voxels, blocks_per_meter=blocks_per_meter, progress=progress)
    if not res.ok:
        raise RuntimeError(f"stand_trans pipeline failed: {res.error}")

    # normalise output file names for the viewer
    glb_src = Path(res.glb_path)
    glb_dst = scene_dir / "model.glb"
    if glb_src.resolve() != glb_dst.resolve():
        glb_dst.write_bytes(glb_src.read_bytes())

    manifest = {
        "name": name,
        "glb": "model.glb",
        "param": param_path.name,
        "stats": {
            "visual_meshes": res.stats.get("visual_meshes"),
            "vertices": res.stats.get("vertices"),
            "bbox_min_m": res.stats.get("bbox_min_m"),
            "bbox_max_m": res.stats.get("bbox_max_m"),
        },
    }

    if make_voxels:
        lit_src = Path(res.litematic_path)
        lit_dst = scene_dir / "model.litematic"
        if lit_src.resolve() != lit_dst.resolve():
            lit_dst.write_bytes(lit_src.read_bytes())
        if progress: progress("解码体素并打包")
        vox = _pack_voxels(scene_dir, lit_dst, name, blocks_per_meter)
        manifest["litematic"] = "model.litematic"
        manifest["voxels"] = "voxels.json"
        manifest["stats"]["voxel_count"] = vox.get("count")
        manifest["stats"]["pitch_m"] = vox["pitch_m"]
        manifest["stats"]["blocks_per_meter"] = float(blocks_per_meter)

    (scene_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    return manifest


def voxelize_scene(scene_dir, *, blocks_per_meter: float, name: str | None = None,
                   progress=None) -> dict:
    """Decomposed voxelization step: (re)build ONLY the voxels for an already-generated
    scene at ``blocks_per_meter``, reusing the persisted normalized param + BIM. No LLM,
    no GLB rebuild. Overwrites model.litematic + voxels.json and updates manifest.json
    stats. Returns {voxel_count, pitch_m, blocks_per_meter}. This is what the blast page
    calls so the user can pick a resolution to voxelize + simulate at."""
    scene_dir = Path(scene_dir)
    manifest_path = scene_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if name is None:
        name = manifest.get("name") or scene_dir.name

    param_path = scene_dir / f"{name}.param.normalized.json"
    bim_path = scene_dir / f"{name}.bim.json"
    if not param_path.is_file():   # fall back to whatever normalized param the scene has
        cand = next(iter(scene_dir.glob("*.param.normalized.json")), None)
        if cand is not None:
            name = cand.name[: -len(".param.normalized.json")]
            param_path, bim_path = cand, scene_dir / f"{name}.bim.json"
    if not param_path.is_file() or not bim_path.is_file():
        raise FileNotFoundError(
            f"scene {scene_dir.name} is missing its normalized param / bim — regenerate the model first")
    param = json.loads(param_path.read_text(encoding="utf-8"))
    bim = json.loads(bim_path.read_text(encoding="utf-8"))

    from stand_trans.step4_voxel import build_litematic     # local import: only the voxel path needs it
    if progress: progress("体素化 (litematic)")
    lit_src = scene_dir / f"{name}.litematic"
    build_litematic(param, bim, lit_src, blocks_per_meter=blocks_per_meter, name=name)
    lit_dst = scene_dir / "model.litematic"
    if lit_src.resolve() != lit_dst.resolve():
        lit_dst.write_bytes(lit_src.read_bytes())

    if progress: progress("解码体素并打包")
    vox = _pack_voxels(scene_dir, lit_dst, name, blocks_per_meter)

    manifest.setdefault("stats", {})
    manifest["litematic"] = "model.litematic"
    manifest["voxels"] = "voxels.json"
    manifest["stats"]["voxel_count"] = vox.get("count")
    manifest["stats"]["pitch_m"] = vox["pitch_m"]
    manifest["stats"]["blocks_per_meter"] = float(blocks_per_meter)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"voxel_count": vox.get("count"), "pitch_m": vox["pitch_m"],
            "blocks_per_meter": float(blocks_per_meter)}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="param.json -> glb + litematic + voxels.json")
    ap.add_argument("param")
    ap.add_argument("--out", default="./scene_out")
    ap.add_argument("--name", default="scene")
    ap.add_argument("--blocks-per-meter", type=float, default=4.0)
    a = ap.parse_args()
    m = build_scene(a.param, a.out, name=a.name, blocks_per_meter=a.blocks_per_meter)
    print(json.dumps(m, ensure_ascii=False, indent=2))
