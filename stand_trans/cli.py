"""stand_trans command line interface."""
from __future__ import annotations

import argparse
import json
import sys

from .pipeline import convert


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(prog="stand_trans", description="Parametric JSON -> BIM JSON -> IFC -> GLB")
    sub = parser.add_subparsers(dest="cmd")

    def add_convert_args(sp, with_litematic_default=False):
        sp.add_argument("input")
        sp.add_argument("--out-dir", default=None)
        sp.add_argument("--name", default=None)
        sp.add_argument("--unit", choices=["m", "cm", "mm"], default=None)
        sp.add_argument("--no-glb", action="store_true")
        sp.add_argument("--no-normalized", action="store_true")
        sp.add_argument("--ifc-glb", action="store_true", help="generate GLB by triangulating IFC instead of direct visual meshes")
        sp.add_argument("--litematic", dest="litematic", action="store_true",
                        default=with_litematic_default,
                        help="also export a Minecraft .litematic (semantic voxelization)")
        sp.add_argument("--blocks-per-meter", type=float, default=4.0,
                        help="litematic voxel resolution (4 = pitch 0.25 m, higher = finer)")

    p = sub.add_parser("convert", help="convert Parametric Building JSON to IFC/GLB")
    add_convert_args(p)

    pl = sub.add_parser("litematic", help="convert Parametric Building JSON to a Minecraft .litematic")
    add_convert_args(pl, with_litematic_default=True)

    args = parser.parse_args(argv)
    if args.cmd is None:
        # Compatibility: stand_trans input.json --out-dir out
        parser = argparse.ArgumentParser(prog="stand_trans")
        add_convert_args(parser)
        args = parser.parse_args(argv)

    result = convert(args.input, out_dir=args.out_dir, name=args.name, unit=args.unit,
                     make_glb=not args.no_glb, emit_normalized=not args.no_normalized,
                     visual_glb=not args.ifc_glb, make_litematic=args.litematic,
                     blocks_per_meter=args.blocks_per_meter)
    print(f"\n=== stand_trans {'OK' if result.ok else 'FAILED'} ===")
    print(f"input : {result.input}")
    print(f"param : {result.normalized_param_json}")
    print(f"bim   : {result.bim_json}")
    print(f"ifc   : {result.ifc_path}")
    print(f"glb   : {result.glb_path}")
    print(f"litem : {result.litematic_path}")
    if result.stats:
        print(f"stats : {json.dumps(result.stats, ensure_ascii=False)}")
    print(f"result: {result.result_json}")
    if result.error:
        print(f"error : {result.error}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
