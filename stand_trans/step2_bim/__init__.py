"""步骤 2：归一化参数模型 -> BIM 中间模型（21 种 Element）。

入口：to_bim(param: dict) -> dict
"""
from .to_bim import to_bim

__all__ = ["to_bim"]
