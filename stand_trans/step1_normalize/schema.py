"""Strict schema helpers for parametric building JSON."""
from __future__ import annotations


class ValidationError(ValueError):
    pass


UNITS = {"m": 1.0, "meter": 1.0, "meters": 1.0, "cm": 0.01, "mm": 0.001}
COLLECTIONS = (
    "walls", "columns", "slabs", "doors", "windows", "stairs", "roofs",
    "facades", "iwans", "domes", "vaults", "arcades", "decorations",
    "rooms", "pishtaqs", "gardens", "pools", "canals", "screens", "muqarnas",
    "beams", "footings", "mep",
    # 非建筑场景元素(树/车/地形)—— 与建筑构件同走 normalize/validate/to_bim/litematic
    "trees", "vehicles", "terrain",
    # 船舶(航母):param-only feature,几何在 step3_glb/ship.py 从 param["ships"] 直接展开
    "ships",
)


def fail(path: str, msg: str) -> None:
    raise ValidationError(f"{path}: {msg}")


def obj(v, path: str) -> dict:
    if not isinstance(v, dict):
        fail(path, "expected object")
    return v


def arr(v, path: str) -> list:
    if not isinstance(v, list):
        fail(path, "expected array")
    return v


def text(v, path: str) -> str:
    if not isinstance(v, str) or not v.strip():
        fail(path, "expected non-empty string")
    return v


def num(v, path: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        fail(path, "expected number")
    return float(v)


def pos(v, path: str) -> float:
    n = num(v, path)
    if n <= 0:
        fail(path, "must be > 0")
    return n


def point(v, path: str) -> list[float]:
    if not isinstance(v, list) or len(v) != 2:
        fail(path, "expected [x, y]")
    return [num(v[0], f"{path}[0]"), num(v[1], f"{path}[1]")]


def bbox(v, path: str) -> list[float]:
    if not isinstance(v, list) or len(v) != 4:
        fail(path, "expected [xmin, ymin, xmax, ymax]")
    b = [num(v[i], f"{path}[{i}]") for i in range(4)]
    if b[2] <= b[0] or b[3] <= b[1]:
        fail(path, "expected xmax > xmin and ymax > ymin")
    return b


def polygon(v, path: str) -> list[list[float]]:
    pts = arr(v, path)
    if len(pts) < 3:
        fail(path, "expected at least 3 points")
    return [point(p, f"{path}[{i}]") for i, p in enumerate(pts)]


def polygon_area(poly: list[list[float]]) -> float:
    area = 0.0
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        area += a[0] * b[1] - b[0] * a[1]
    return abs(area) * 0.5
