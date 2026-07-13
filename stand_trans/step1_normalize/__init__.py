"""步骤 1：参数化建筑 JSON -> 校验 + 单位归一化的参数模型。

入口：load_parametric(path, unit_override=None) -> dict
"""
from .normalize import load_parametric

__all__ = ["load_parametric"]
