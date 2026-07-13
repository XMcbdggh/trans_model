"""Parametric Building JSON -> BIM JSON -> IFC -> GLB."""
from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .extras.drawings import render_elevation, render_plan
from .extras.exporter import ifc_to_glb
from .extras.ifc_builder import build_ifc
from .step1_normalize import load_parametric
from .extras.qa import qa_report
from .extras.schedules import generate_schedules
from .step2_bim import to_bim
from .extras.validator import validate as validate_ifc
from .step3_glb import build_visual_glb


@dataclass
class Result:
    ok: bool
    input: str
    normalized_param_json: str | None = None
    bim_json: str | None = None
    ifc_path: str | None = None
    glb_path: str | None = None
    litematic_path: str | None = None
    result_json: str | None = None
    stats: dict = field(default_factory=dict)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def convert(input_path: str | Path, out_dir: str | Path | None = None, name: str | None = None,
            unit: str | None = None, make_glb: bool = True, emit_normalized: bool = True,
            visual_glb: bool = True, make_litematic: bool = False,
            blocks_per_meter: float = 4.0, progress=None) -> Result:
    inp = Path(input_path)
    out = Path(out_dir) if out_dir else inp.parent / "out"
    stem = name or inp.stem.replace(".param", "")
    res = Result(ok=False, input=str(inp))
    try:
        out.mkdir(parents=True, exist_ok=True)
        if progress: progress("解析与校验参数")
        param = load_parametric(inp, unit_override=unit)
        if emit_normalized:
            normalized_path = out / f"{stem}.param.normalized.json"
            normalized_path.write_text(json.dumps(param, ensure_ascii=False, indent=2), encoding="utf-8")
            res.normalized_param_json = str(normalized_path)
        if progress: progress("生成 BIM 结构")
        bim = to_bim(param)
        bim_path = out / f"{stem}.bim.json"
        bim_path.write_text(json.dumps(bim, ensure_ascii=False, indent=2), encoding="utf-8")
        res.bim_json = str(bim_path)
        if progress: progress("质检与 IFC 导出")
        qa_path = out / f"{stem}.qa.json"
        qa = qa_report(bim, param, qa_path)
        res.stats["qa"] = {"ok": qa["ok"], "counts": qa["counts"], "path": str(qa_path)}
        ifc_path = out / f"{stem}.ifc"
        res.stats.update(build_ifc(bim, ifc_path))
        res.ifc_path = str(ifc_path)
        validation_path = out / f"{stem}.validation.json"
        val = validate_ifc(ifc_path, validation_path)
        res.stats["validation"] = {
            "ok": val["ok"],
            "counts": val["counts"],
            "path": str(validation_path),
        }
        if progress: progress("生成平立面图纸")
        drawings: list[str] = []
        for lv in bim.get("levels", []):
            p = out / f"{stem}_plan_{lv['name']}.svg"
            render_plan(bim, lv["name"], p, project_name=bim["project"]["name"])
            drawings.append(str(p))
        for direction in ("N", "E", "S", "W"):
            p = out / f"{stem}_elevation_{direction}.svg"
            render_elevation(bim, direction, p, project_name=bim["project"]["name"])
            drawings.append(str(p))
        res.stats["drawings"] = drawings
        boq_path = res.stats.get("boq_path")
        if boq_path:
            sched = generate_schedules(bim, boq_path, out, stem)
            res.stats["schedule"] = sched
        if make_glb:
            if progress: progress("构建 3D 网格 (GLB)")
            glb_path = out / f"{stem}.glb"
            if visual_glb:
                res.stats.update(build_visual_glb(param, bim, glb_path))
            else:
                res.stats.update(ifc_to_glb(ifc_path, glb_path))
            res.glb_path = str(glb_path)
        if make_litematic:
            if progress: progress("体素化 (litematic)")
            from .step4_voxel import build_litematic
            litematic_path = out / f"{stem}.litematic"
            res.stats["litematic"] = build_litematic(
                param, bim, litematic_path,
                blocks_per_meter=blocks_per_meter, name=stem)
            res.litematic_path = str(litematic_path)
        res.ok = True
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
        res.warnings.append(traceback.format_exc().strip().splitlines()[-1])
    finally:
        try:
            result_path = out / f"{stem}.result.json"
            result_path.write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            res.result_json = str(result_path)
        except Exception:
            pass
    return res
