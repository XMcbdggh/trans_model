---
name: building-image-to-json
description: Convert a photo/drawing of a building (plus optional text) into a structured Building Spec JSON. Use when the user shares a building image and wants it turned into a 3D model, or asks to "extract building JSON from this image". Produces the Layer-1 Building Spec that skill json-to-3d turns into a 3D model.
---

# Skill A — Building image → Building Spec JSON

You are given one or more images of a building (photo, site plan, elevation, or
sketch) and optionally a text description. Produce **one** Building Spec JSON
object. A deterministic generator expands it into precise geometry, so you give
sensible high-level estimates — **never** hand-write wall coordinates.

## Output contract

Emit a single JSON object conforming to `schema/building-spec.schema.json`
(canonical copy at `agent3d/schema/building-spec.schema.json`). Do not wrap it in
prose. Key rules:

- **Metres. Top-down site plan: X = east, Y = north.** Buildings are placed by a
  rectangular footprint `[x0, y0, x1, y1]` with `x1>x0`, `y1>y0`.
- Keep every building inside the site (`0..width_m`, `0..depth_m`) and do not
  overlap footprints.
- Estimate `floors` and `floor_height_m` from apparent proportions (typical
  storey 3–4.5 m).
- Count windows **per visible facade** as an integer per side in
  `windows.per_facade` — the generator distributes them; you never give window
  coordinates.
- `style` ∈ {persian, modern, classical, islamic}. `material` ∈
  {reinforced_concrete, stone_masonry, brick_masonry, steel, timber}.
- Only include features you can see or the text states (dome, pool, garden,
  trees, vehicles, perimeter wall). Prefer fewer correct elements.

## Procedure

1. Read the image(s) and text. Identify: number of distinct buildings, each
   one's approximate footprint and floor count, roof shape, dominant material and
   architectural style, window counts per facade, entrance side, and any site
   context (wall, landscape, vehicles).
2. Choose a site size that comfortably contains everything; place footprints on
   that plan.
3. Fill the Building Spec. Omit optional fields you are unsure about (defaults are
   sensible).
4. **Self-check before returning** (this is the reliability step):
   - every `footprint` has `x1>x0` and `y1>y0` and lies within the site;
   - footprints do not overlap;
   - `floors >= 1`; counts are non-negative integers;
   - enums use only the allowed values.
   Fix anything that fails, then return the JSON.

## Example

Input: a photo of a 2-storey brick house with a gable roof, 3 front windows and a
central door.

```json
{
  "meta": { "name": "brick_house", "style": "modern" },
  "site": { "width_m": 20, "depth_m": 16, "ground": { "surface": "grass" } },
  "buildings": [{
    "id": "house", "footprint": [4, 4, 16, 12], "floors": 2, "floor_height_m": 3.2,
    "material": "brick_masonry", "roof": { "type": "gable" },
    "windows": { "shape": "rect", "per_facade": { "south": 3, "north": 3, "east": 1, "west": 1 } },
    "entrance": { "facade": "south", "type": "door" }
  }]
}
```

See more in `agent3d/examples/*.spec.json`.

## Handoff

Pass the JSON to **skill `json-to-3d`** (or `POST /api/generate` with the JSON as
the `spec` form field) to build and view the 3D model.
