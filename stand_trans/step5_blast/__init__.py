"""步骤 5：体素模型 -> 爆炸毁伤仿真。

Kinney-Graham 超压 + 动能侵彻 + 遮蔽衰减 + 构件级级联倒塌（shared.structure）。
入口：
  unpack(voxels) -> (coords, palette_idx, palette_ids)
  compute_blast(...) -> dict   逐体素 damage/fall + 级联统计
  export_damaged(litematic_path, damage, out_path, name=...) -> int
  write_report(path, params, result) -> None
"""
from .blast import compute_blast, export_damaged, unpack, write_report

__all__ = ["compute_blast", "export_damaged", "unpack", "write_report"]
