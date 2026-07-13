---
name: json-to-3d
description: Turn a Building Spec JSON (or a param.json) into a 3D model — GLB visual mesh + Minecraft .litematic voxels + a browser three.js viewer. Use after skill building-image-to-json, or whenever the user has a building JSON and wants to generate/view the 3D model.
---

# Skill B — Building JSON → 3D model + viewer

Takes a **Building Spec** (Layer 1, from skill `building-image-to-json` or written
by hand) and produces a viewable 3D model. It never needs vision — pure geometry.

## What it produces (into a scene folder)

- `model.glb` — visual 3D model (open in three.js `GLTFLoader`)
- `model.litematic` — Minecraft voxel schematic (**kept** for the future blast module)
- `voxels.json` — litematic decoded to `{dims, palette, blocks:[x,y,z,idx…], count, pitch_m}` for the browser viewer (no NBT parsing needed)
- `param.json` — the exact stand_trans pipeline input (for debugging/extension)

## How to run

Prereqs: repo `requirements.txt` installed (trimesh, litemapy, manifold3d, …).

### Option 1 — declarative spec (common case)

```bash
python -c "import json,sys; sys.path.insert(0,'.'); \
from agent3d.core import spec_to_param, build_scene; \
spec=json.load(open('my.spec.json',encoding='utf-8')); \
build_scene(spec_to_param(spec), './scene_out', name='my_scene', blocks_per_meter=4.0)"
```

### Option 2 — bespoke layout (irregular scenes)

When the spec can't express it (odd angles, programmatic repetition), write a
short scene function against `SceneBuilder` — see
`agent3d/examples/build_scene_example.py`. Then run the pipeline on its output:

```bash
python -m agent3d.core.pipeline_runner my_scene.param.json --out ./scene_out --name my_scene
```

`blocks_per_meter` controls voxel resolution: 4.0 (0.25 m blocks, default) for
buildings; drop to ~2.0 for very large sites to keep the voxel count manageable.

## How to view

Serve the scene folder and open the viewer:

```bash
python -m http.server 8080 --directory ./scene_out
# then open agent3d/webapp/static/viewer.html?glb=/model.glb&vox=/voxels.json
```

Or, in the web app, the response from `POST /api/generate` includes
`urls.viewer`. The viewer supports **GLB ⇄ 体素(litematic) 切换** and **自动旋转**.

## Reliability notes (why this doesn't fail)

- `SceneBuilder` guarantees valid geometry: closed wall loops, auto `host_id`
  wiring, evenly distributed windows, continuous level elevations, correct roof
  fields per type. So the stand_trans validator never rejects the output.
- If `build_scene` raises, the error names the offending element — fix the spec
  (usually an out-of-range footprint or a facade window count that's too dense)
  and rerun.

## Extension: blast module

The `.litematic` is preserved specifically so a future step-5 blast/damage module
(the repo's `stand_trans/step5_blast` + `backend/blast_runner.py`) can consume it.
See `docs/AGENT_3D_PIPELINE.md` §"litematic 与爆炸扩展点".
