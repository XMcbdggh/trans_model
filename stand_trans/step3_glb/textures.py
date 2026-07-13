"""Procedurally generated PBR-ready textures for kashi tile, calligraphy bands etc.

Each generator returns a PIL.Image RGB tile of the requested size, intended to be
seamlessly tileable. Used by glb_exporter to wrap selected meshes (onion dome,
drum, facade bands) with patterned appearance.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw


def _np_to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


@lru_cache(maxsize=None)
def kashi_star_tile(size: int = 512) -> Image.Image:
    """8-point star tile in Iranian turquoise + cobalt with cream interstices."""
    bg = np.zeros((size, size, 3), dtype=np.float32)
    bg[..., 0] = 28  # near-black background
    bg[..., 1] = 90
    bg[..., 2] = 110
    img = _np_to_image(bg)
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2.0, size / 2.0
    r_outer = size * 0.46
    r_inner = size * 0.20
    # 8-point star
    pts = []
    for i in range(16):
        r = r_outer if i % 2 == 0 else r_inner
        a = i * math.pi / 8 - math.pi / 8
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    draw.polygon(pts, fill=(40, 165, 195))
    # Inner rosette (cream)
    rosette = []
    for i in range(16):
        r = r_inner * 0.78 if i % 2 == 0 else r_inner * 0.32
        a = i * math.pi / 8
        rosette.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    draw.polygon(rosette, fill=(238, 220, 170))
    # Corner accents (creates seamless tiling)
    accent = (210, 90, 50)  # warm ochre
    s = size * 0.12
    for (px, py) in [(0, 0), (size, 0), (0, size), (size, size)]:
        diamond = [(px, py - s), (px + s, py), (px, py + s), (px - s, py)]
        draw.polygon(diamond, fill=accent)
    # Edge bands (cobalt) connecting tiles
    cobalt = (28, 70, 140)
    edge_t = size * 0.04
    draw.rectangle([0, size / 2 - edge_t / 2, size, size / 2 + edge_t / 2], fill=cobalt)
    draw.rectangle([size / 2 - edge_t / 2, 0, size / 2 + edge_t / 2, size], fill=cobalt)
    return img


@lru_cache(maxsize=None)
def kashi_lotus_tile(size: int = 512) -> Image.Image:
    bg = np.full((size, size, 3), [220, 200, 160], dtype=np.float32)
    img = _np_to_image(bg)
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2.0, size / 2.0
    petal_color = (40, 145, 175)
    for i in range(12):
        a = i * math.tau / 12
        cosx, sinx = math.cos(a), math.sin(a)
        tip_x = cx + cosx * size * 0.42
        tip_y = cy + sinx * size * 0.42
        side1_x = cx + math.cos(a + math.pi / 12) * size * 0.18
        side1_y = cy + math.sin(a + math.pi / 12) * size * 0.18
        side2_x = cx + math.cos(a - math.pi / 12) * size * 0.18
        side2_y = cy + math.sin(a - math.pi / 12) * size * 0.18
        draw.polygon([(cx, cy), (side1_x, side1_y), (tip_x, tip_y), (side2_x, side2_y)],
                     fill=petal_color)
    draw.ellipse([cx - size * 0.10, cy - size * 0.10, cx + size * 0.10, cy + size * 0.10],
                 fill=(230, 200, 70))
    # Border for tiling
    border_color = (90, 40, 30)
    draw.rectangle([0, 0, size - 1, size - 1], outline=border_color, width=int(size * 0.03))
    return img


@lru_cache(maxsize=None)
def calligraphy_band_tile(width: int = 1024, height: int = 256) -> Image.Image:
    bg = np.full((height, width, 3), [22, 60, 110], dtype=np.float32)
    img = _np_to_image(bg)
    draw = ImageDraw.Draw(img)
    # Approximate Kufic-style strokes — vertical risers and curving connections
    cream = (238, 220, 170)
    line_w = max(int(height * 0.10), 4)
    base_y = int(height * 0.55)
    top_y = int(height * 0.15)
    unit = width // 8
    for i in range(8):
        x0 = i * unit
        # Vertical stroke
        draw.rectangle([x0 + int(unit * 0.18), top_y, x0 + int(unit * 0.18) + line_w, base_y], fill=cream)
        # Horizontal connector
        draw.rectangle([x0 + int(unit * 0.18), base_y - line_w // 2, x0 + unit, base_y + line_w // 2], fill=cream)
        # Curl
        ellipse_pad = int(unit * 0.15)
        draw.arc([x0 + int(unit * 0.45), top_y, x0 + int(unit * 0.95), base_y - line_w],
                 start=-60, end=240, fill=cream, width=line_w)
    # Border bands top/bottom
    border = (200, 165, 80)
    draw.rectangle([0, 0, width, int(height * 0.06)], fill=border)
    draw.rectangle([0, int(height * 0.94), width, height], fill=border)
    return img


@lru_cache(maxsize=None)
def glazed_brick_tile(size: int = 512) -> Image.Image:
    """Subtle teal-glazed brick pattern: solid base + bricks outlined in cobalt."""
    bg = np.full((size, size, 3), [38, 155, 185], dtype=np.float32)
    img = _np_to_image(bg)
    draw = ImageDraw.Draw(img)
    brick_h = size // 8
    brick_w = size // 4
    border = (24, 70, 110)
    for row in range(8):
        offset = (brick_w // 2) if row % 2 else 0
        y0 = row * brick_h
        for col in range(-1, 5):
            x0 = col * brick_w + offset
            draw.rectangle([x0, y0, x0 + brick_w, y0 + brick_h], outline=border, width=2)
    return img


TEXTURE_REGISTRY = {
    "kashi_star": kashi_star_tile,
    "kashi_lotus": kashi_lotus_tile,
    "calligraphy_band": calligraphy_band_tile,
    "glazed_brick": glazed_brick_tile,
}


def get_texture(name: str, **kwargs) -> Image.Image | None:
    fn = TEXTURE_REGISTRY.get(name)
    return fn(**kwargs) if fn else None


@lru_cache(maxsize=None)
def load_external_image(path: str, max_px: int = 1024) -> Image.Image | None:
    """Load an external image (jpg/png) as an RGB PIL.Image for use as a wall texture.

    Downscaled so the long edge ≤ max_px: source photos can be huge (e.g. a 38 MB 8K
    JPG) and would bloat the GLB; ~1024px is plenty for a wall diffuse map. Cached so
    the same file is decoded + resized once even when many walls share it. Returns None
    on any failure (missing file / decode error) so callers fall back to flat color.
    """
    try:
        img = Image.open(path).convert("RGB")
        longest = max(img.size)
        if longest > max_px:
            s = max_px / float(longest)
            img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                             Image.LANCZOS)
        return img
    except Exception:
        return None
