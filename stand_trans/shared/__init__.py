"""共享层：材料库、风格预设、结构支承图/级联倒塌。

被步骤 3/4/5 共用，不属于任何单一步骤。
"""
from . import materials, structure
from .styles import get_style, resolve_style

__all__ = ["materials", "structure", "get_style", "resolve_style"]
