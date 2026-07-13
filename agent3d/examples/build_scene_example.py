"""Hand-written scene function -- the escape hatch for irregular layouts.

When a scene is too bespoke for the declarative Building Spec (odd angles,
programmatic repetition, per-element tweaks), the LLM writes a short function
like this directly against SceneBuilder. It is the same idiom as the repo's
examples/palace_compound_scene.py, but using the packaged, reusable builder.

Run:  python -m agent3d.examples.build_scene_example   ->  build_scene_example.param.json
Then: python -m agent3d.core.pipeline_runner build_scene_example.param.json --out ./scene_out
"""
from __future__ import annotations

from pathlib import Path

from agent3d.core import SceneBuilder


def build():
    b = SceneBuilder("courtyard_block", style="persian")

    b.add_level("G", 0.0, 4.0)                       # site / ground props
    b.add_terrain("ground", "G", [0, 0, 60, 60], surface="sand")
    b.perimeter_wall([0, 0, 60, 60], "G", height_m=4.0, corner_towers=True)

    # four identical two-storey blocks around a court, generated in a loop
    blocks = {"nw": [8, 34, 26, 52], "ne": [34, 34, 52, 52],
              "sw": [8, 8, 26, 26], "se": [34, 8, 52, 26]}
    for bid, bbox in blocks.items():
        levels = b.stack_levels([f"{bid}_F1", f"{bid}_F2"], 0.0, 3.6)
        h = b.box_building(bid, bbox, levels, material="brick_masonry", roof_type="flat")
        b.add_windows(h, "south", 3, shape="pointed_arch")
        b.add_windows(h, "north", 3, shape="pointed_arch")
        b.add_door(h, "south", levels[0], shape="pointed_arch")

    # a central pool
    b.add_pool("court_pool", "G", [[26, 26], [34, 26], [34, 34], [26, 34]], depth_m=0.4)
    return b.to_param()


if __name__ == "__main__":
    import json
    param = build()
    out = Path(__file__).with_name("build_scene_example.param.json")
    out.write_text(json.dumps(param, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)
    print("counts:", {k: len(v) for k, v in param.items() if isinstance(v, list)})
