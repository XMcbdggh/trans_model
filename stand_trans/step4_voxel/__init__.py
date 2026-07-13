"""步骤 4：BIM -> 体素模型（.litematic）+ 语义边车（voxelclass.json）。

复用步骤 3 的 collect_meshes 几何，按材料映射到 Minecraft 方块并体素化。
入口：
  build_litematic(param, bim, path, blocks_per_meter=4.0, name=None) -> dict
  litematic_to_voxels(path) -> dict   解析体素供前端/步骤 5 使用
"""
from .litematic import build_litematic, litematic_to_voxels

__all__ = ["build_litematic", "litematic_to_voxels"]
