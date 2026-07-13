"""步骤 3：BIM -> 视觉 3D 网格 / GLB（trimesh + CSG 开洞 + PBR 纹理）。

入口：
  build_visual_glb(param, bim, glb_path) -> dict   导出 GLB
  collect_meshes(param, bim) -> (meshes, kinds, csg_stats)  供步骤 4 复用几何
"""
from .glb_exporter import build_visual_glb, collect_meshes

__all__ = ["build_visual_glb", "collect_meshes"]
