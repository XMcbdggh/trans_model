"""Procedurally generate the voxel block-texture atlas (no external art assets).

The atlas is a 4x4 grid of TILE-px tiles. Each tile is a near-grayscale *luminance
detail* map (mortar lines, wood grain, leaf noise, glass frame ...) with a mean around
~0.85 and values roughly in [0.6, 1.0]. The browser voxel material multiplies this
detail by each voxel's REAL GLB colour (mesh_palette) and by a baked ambient-occlusion
factor, so the surface pattern comes from the material while the colour comes from the
real model -- textured cubes that read far more "real" than flat-coloured ones, at zero
extra geometry cost.

Tile order MUST stay in sync with MATERIAL_TILE in ../voxel_material.js.

Run:  python agent3d/webapp/static/textures/make_atlas.py
Out:  blocks_atlas.png  (RGB, COLS*TILE square)
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image

TILE = 32          # px per tile
COLS = 4           # 4x4 = 16 slots
SIZE = TILE * COLS

# index -> generator name; MUST match MATERIAL_TILE in voxel_material.js
TILES = [
    "reinforced_concrete",  # 0
    "stone_masonry",        # 1
    "brick_masonry",        # 2
    "steel",                # 3
    "timber",               # 4
    "glass",                # 5
    "tile",                 # 6
    "concrete_light",       # 7
    "copper",               # 8
    "foliage",              # 9
    "sand",                 # 10
    "soil",                 # 11
    "vehicle_body",         # 12
    "default",              # 13
]


def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v * 255))))


def _put(px, x, y, v):
    g = _clamp(v)
    px[x, y] = (g, g, g)


def gen_tile(kind: str, seed: int):
    img = Image.new("RGB", (TILE, TILE), (0, 0, 0))
    px = img.load()
    rnd = random.Random(seed)

    def speckle(base, amp):
        for y in range(TILE):
            for x in range(TILE):
                _put(px, x, y, base + (rnd.random() - 0.5) * amp)

    if kind in ("reinforced_concrete", "concrete_light", "default"):
        speckle(0.88, 0.10)
        # a few faint pores
        for _ in range(6):
            cx, cy = rnd.randrange(TILE), rnd.randrange(TILE)
            _put(px, cx, cy, 0.72)

    elif kind == "stone_masonry":            # sandstone strata (horizontal bands)
        for y in range(TILE):
            band = 0.9 + 0.06 * math.sin(y * math.pi / 5.0)
            for x in range(TILE):
                _put(px, x, y, band + (rnd.random() - 0.5) * 0.05)
        for y in range(0, TILE, 8):          # bedding lines
            for x in range(TILE):
                _put(px, x, y, 0.70)

    elif kind == "brick_masonry":            # running-bond brick
        bh, bw = 8, 16
        for y in range(TILE):
            row = y // bh
            off = (bw // 2) if (row % 2) else 0
            for x in range(TILE):
                mortar = (y % bh == 0) or ((x + off) % bw == 0)
                v = 0.66 if mortar else 0.9 + (rnd.random() - 0.5) * 0.06
                _put(px, x, y, v)

    elif kind == "timber":                   # vertical wood grain
        for x in range(TILE):
            base = 0.88 + 0.06 * math.sin(x * 0.9)
            for y in range(TILE):
                grain = 0.05 * math.sin(y * 0.35 + x * 0.6)
                _put(px, x, y, base + grain + (rnd.random() - 0.5) * 0.04)
        for x in range(0, TILE, 11):         # plank seams
            for y in range(TILE):
                _put(px, x, y, 0.72)

    elif kind == "glass":                    # frame border + faint diagonal panes
        for y in range(TILE):
            for x in range(TILE):
                edge = x < 2 or y < 2 or x >= TILE - 2 or y >= TILE - 2
                diag = 0.04 * math.sin((x + y) * 0.5)
                _put(px, x, y, (0.70 if edge else 0.97) + diag)

    elif kind == "tile":                     # overlapping roof tiles / scallops
        for y in range(TILE):
            for x in range(TILE):
                wave = math.sin((x % 8) / 8.0 * math.pi)
                v = 0.78 + 0.16 * wave
                if y % 8 == 0:
                    v = 0.66
                _put(px, x, y, v + (rnd.random() - 0.5) * 0.04)

    elif kind == "steel":                    # brushed vertical + rivets
        for x in range(TILE):
            base = 0.9 + 0.05 * math.sin(x * 2.3)
            for y in range(TILE):
                _put(px, x, y, base + (rnd.random() - 0.5) * 0.03)
        for cx in (4, TILE - 5):             # corner rivets
            for cy in (4, TILE - 5):
                _put(px, cx, cy, 0.68)

    elif kind == "copper":                   # mottled metallic patina
        speckle(0.86, 0.12)
        for _ in range(10):
            cx, cy = rnd.randrange(TILE), rnd.randrange(TILE)
            _put(px, cx, cy, 0.75 + rnd.random() * 0.15)

    elif kind == "foliage":                  # leafy clumps
        for y in range(TILE):
            for x in range(TILE):
                _put(px, x, y, 0.78 + (rnd.random() - 0.5) * 0.22)
        for _ in range(18):                  # darker leaf gaps
            cx, cy = rnd.randrange(TILE), rnd.randrange(TILE)
            _put(px, cx, cy, 0.6)

    elif kind == "sand":
        speckle(0.9, 0.08)

    elif kind == "soil":                     # coarse clumps
        speckle(0.82, 0.16)
        for _ in range(12):
            cx, cy = rnd.randrange(TILE), rnd.randrange(TILE)
            _put(px, cx, cy, 0.66)

    elif kind == "vehicle_body":             # smooth panel + seam
        for y in range(TILE):
            for x in range(TILE):
                _put(px, x, y, 0.92 + (rnd.random() - 0.5) * 0.03)
        for y in range(TILE):
            _put(px, TILE // 2, y, 0.74)     # panel seam

    else:
        speckle(0.88, 0.10)

    return img


def main():
    atlas = Image.new("RGB", (SIZE, SIZE), (220, 220, 220))
    for i, kind in enumerate(TILES):
        col, row = i % COLS, i // COLS
        atlas.paste(gen_tile(kind, seed=1000 + i), (col * TILE, row * TILE))
    out = Path(__file__).resolve().parent / "blocks_atlas.png"
    atlas.save(out)
    print(f"wrote {out}  ({SIZE}x{SIZE}, {len(TILES)} tiles, {TILE}px)")


if __name__ == "__main__":
    main()
