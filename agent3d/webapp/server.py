"""FastAPI web app: image + text -> 3D model, viewable in the browser.

Flow (one request):
  POST /api/generate  (multipart: image files + `description` + optional `spec` JSON)
    1. vision.image_to_spec(images, description)  -> Building Spec (Layer 1)
       (skipped if the caller supplies a ready `spec` -- e.g. from Skill A)
    2. spec_to_param(spec)                         -> param.json (Layer 2)
    3. build_scene(param, ...)                     -> model.glb + model.litematic + voxels.json
    -> returns { scene_id, urls: {glb, voxels, spec, param, viewer} }

  GET  /api/scenes/{id}/{file}   static artifacts
  GET  /                          upload UI + embedded viewer

Run:  uvicorn agent3d.webapp.server:app --host 127.0.0.1 --port 8060
Env:  ANTHROPIC_API_KEY (for the vision step), WIDE_SIM_VISION_MODEL (optional),
      AGENT3D_BPM (voxel resolution, default 4.0; use ~2.0 for very large sites).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_dotenv(root: Path) -> None:
    """Load KEY=VALUE lines from <root>/.env into os.environ. Zero dependency.
    A variable already present in the real environment is NOT overridden, so the
    OS/shell always wins over the file (standard dotenv semantics)."""
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:   # blank value == "not configured"
            os.environ[key] = val


_load_dotenv(_REPO_ROOT)

from agent3d.core import build_scene, spec_to_param, voxelize_scene, settings   # noqa: E402
from agent3d.core.vision import (image_to_spec, image_to_param, describe_building,   # noqa: E402
                                 validate_spec, ping_model)

SCENES_ROOT = Path(os.getenv("AGENT3D_SCENES", str(_REPO_ROOT / "artifacts_web")))
SCENES_ROOT.mkdir(parents=True, exist_ok=True)
BPM = float(os.getenv("AGENT3D_BPM", "4.0"))
# default generation path for image uploads: "param" (direct param.json + repair loop)
# or "spec" (two-layer Building Spec). A per-request `mode` form field overrides this.
GEN_MODE = os.getenv("AGENT3D_GEN_MODE", "param").lower()

# in-memory job store for background generation (job_id -> status dict), updated by the
# worker thread and read by GET /api/jobs/{id}. Bounded; fine for a single-node dev app.
JOBS: dict = {}

app = FastAPI(title="agent3d", description="image -> building JSON -> 3D model")

_MEDIA = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
          "webp": "image/webp", "gif": "image/gif"}


async def _read_images(images: "list[UploadFile] | None") -> list[tuple[bytes, str]]:
    """Read uploaded files into (bytes, media_type) pairs. 400 if none supplied."""
    if not images:
        raise HTTPException(400, "provide at least one image")
    payload: list[tuple[bytes, str]] = []
    for up in images:
        data = await up.read()
        ext = (up.filename or "x.jpg").rsplit(".", 1)[-1].lower()
        payload.append((data, _MEDIA.get(ext, "image/jpeg")))
    return payload


@app.post("/api/describe")
async def describe(description: str = Form(""), images: list[UploadFile] = None):
    """Step 1 of the two-step flow: image(s) + optional notes -> a natural-language
    feature description the user can edit before generating. Returns {features}."""
    try:
        payload = await _read_images(images)
        model = settings.describe_model()   # resolve here so we can report the exact model used
        features = describe_building(payload, description, model=model)
        return {"features": features, "model": model}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500,
                            content={"error": f"{type(exc).__name__}: {exc}",
                                     "trace": traceback.format_exc().splitlines()[-3:]})


@app.get("/api/settings")
def get_settings():
    """Effective model / base-URL config + whether an API key is set (masked hint only).
    Never returns the raw key. Backs the ⚙️ 模型设置 panel."""
    return settings.as_public()


@app.post("/api/settings")
async def post_settings(payload: dict = Body(default={})):
    """Update model / base_url / describe_model / api_key. Omit api_key (or send null) to
    keep the current key; send "" to clear an override (revert to .env / default)."""
    try:
        return settings.update(payload or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/settings/test")
def test_settings():
    """User-triggered: a tiny live round-trip that verifies the saved key/URL/model work.
    Costs a negligible amount of credit; only runs when the user clicks 测试连接."""
    try:
        return {"ok": True, **ping_model()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _job_result(scene_id: str, name: str, spec_doc, description: str, manifest: dict,
                model: str | None = None) -> dict:
    base = f"/api/scenes/{scene_id}"
    return {
        "scene_id": scene_id, "name": name, "spec": spec_doc, "model": model,
        "description": description or None, "stats": manifest["stats"],
        "urls": {
            "glb": f"{base}/model.glb", "voxels": f"{base}/voxels.json",
            "spec": f"{base}/spec.json", "param": f"{base}/param.json",
            "litematic": f"{base}/model.litematic", "viewer": f"/viewer.html?scene={scene_id}",
        },
    }


def _run_generate(job_id: str, payload, spec: str, description: str, gen_mode: str) -> None:
    """Worker thread: image/spec -> param -> visual GLB only (voxels are produced later on
    the blast page at a chosen resolution). Writes real stage names into JOBS[job_id] via
    the ``prog`` callback so the browser can poll live progress."""
    job = JOBS[job_id]

    def prog(stage, detail=""):
        job["stage"] = stage
        job["detail"] = detail

    try:
        spec_doc = param = None
        model_used = None
        if spec and spec.strip():
            spec_doc = json.loads(spec)                 # ready Spec supplied -> no vision model
        elif gen_mode == "param":
            model_used = settings.model()
            job["model"] = model_used                   # expose live so the browser can show it
            param = image_to_param(payload, description, model=model_used, progress=prog)
        else:
            model_used = settings.model()
            job["model"] = model_used
            prog(f"AI 读图并生成 Building Spec（模型 {model_used}）")
            spec_doc = image_to_spec(payload, description, model=model_used)

        if spec_doc is not None:
            ok, err = validate_spec(spec_doc)
            if not ok:
                raise ValueError(f"invalid building spec: {err}")
            prog("展开为 param.json")
            param = spec_to_param(spec_doc)

        scene_id = uuid.uuid4().hex[:12]
        scene_dir = SCENES_ROOT / scene_id
        scene_dir.mkdir(parents=True, exist_ok=True)
        if description and description.strip():
            (scene_dir / "description.txt").write_text(description, encoding="utf-8")
        if spec_doc is not None:
            (scene_dir / "spec.json").write_text(json.dumps(spec_doc, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")

        name = ((spec_doc or {}).get("meta") or {}).get("name") \
            or (param.get("project") or {}).get("name") or scene_id
        manifest = build_scene(param, scene_dir, name=name, make_voxels=False, progress=prog)
        job["result"] = _job_result(scene_id, name, spec_doc, description, manifest, model_used)
        job["stage"] = "完成"
    except Exception as exc:
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["trace"] = traceback.format_exc().splitlines()[-3:]
    finally:
        job["done"] = True


def _new_job() -> str:
    """Create a queued JOBS entry and return its id, evicting old finished jobs."""
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"stage": "排队中", "detail": "", "done": False, "error": None,
                    "result": None, "t0": time.time()}
    done_old = [k for k in JOBS if JOBS[k]["done"]]
    if len(JOBS) > 60 and done_old:      # keep the store bounded
        for k in sorted(done_old, key=lambda k: JOBS[k]["t0"])[:20]:
            JOBS.pop(k, None)
    return job_id


def _parse_bpm(bpm: str, lo: float = 0.25, hi: float = 8.0) -> float:
    """Parse a blocks-per-meter form value; default AGENT3D_BPM, clamp to [lo, hi]."""
    try:
        v = float(bpm) if bpm.strip() else BPM
    except ValueError:
        v = BPM
    return max(lo, min(hi, v))


@app.post("/api/generate")
async def generate(description: str = Form(""), spec: str = Form(""), mode: str = Form(""),
                   images: list[UploadFile] = None):
    """Start a background generation job and return {job_id}. Builds the visual GLB only
    (no voxels -- those are produced later on the blast page at a chosen resolution). Poll
    GET /api/jobs/{id} for live stage + result. Provide a ready `spec` JSON OR image files
    (+ optional description); `mode` = param|spec selects the image path."""
    gen_mode = (mode or GEN_MODE).lower()
    payload = None
    if not spec.strip():
        payload = await _read_images(images)   # read while request-bound, before threading

    job_id = _new_job()
    threading.Thread(target=_run_generate,
                     args=(job_id, payload, spec, description, gen_mode),
                     daemon=True).start()
    return {"job_id": job_id}


def _run_voxelize(job_id: str, scene_id: str, bpm: float) -> None:
    """Worker thread: voxelize an existing scene at ``bpm`` (blocks per meter) via the
    decomposed voxelize_scene() -- no LLM, no GLB rebuild. Writes live stage names into
    JOBS[job_id]. Backs the blast page's resolution picker."""
    job = JOBS[job_id]

    def prog(stage, detail=""):
        job["stage"] = stage
        job["detail"] = detail

    try:
        stats = voxelize_scene(SCENES_ROOT / scene_id, blocks_per_meter=bpm, progress=prog)
        base = f"/api/scenes/{scene_id}"
        job["result"] = {"scene_id": scene_id, "stats": stats,
                         "urls": {"voxels": f"{base}/voxels.json",
                                  "viewer": f"/viewer.html?scene={scene_id}"}}
        job["stage"] = "完成"
    except Exception as exc:
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["trace"] = traceback.format_exc().splitlines()[-3:]
    finally:
        job["done"] = True


@app.post("/api/voxelize")
async def voxelize(scene_id: str = Form(...), bpm: str = Form("")):
    """Start a background job that voxelizes an existing scene at `bpm` blocks/meter and
    return {job_id}. Poll GET /api/jobs/{id}. Reuses the scene's persisted param/BIM, so it
    never re-runs the model or rebuilds the GLB. Lower bpm = fewer blocks."""
    if not scene_id.isalnum():
        raise HTTPException(400, "bad scene_id")
    if not (SCENES_ROOT / scene_id / "manifest.json").is_file():
        raise HTTPException(404, "unknown scene")
    job_id = _new_job()
    threading.Thread(target=_run_voxelize, args=(job_id, scene_id, _parse_bpm(bpm)),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return {"stage": job["stage"], "detail": job.get("detail", ""),
            "elapsed": round(time.time() - job["t0"], 1), "model": job.get("model"),
            "done": job["done"], "error": job.get("error"), "result": job.get("result")}


@app.get("/api/scenes")
def list_scenes():
    """List generated scenes (newest first) for the blast-sim model picker.

    Any scene with a manifest.json (i.e. a generated model) is returned, whether or not it
    has been voxelized yet -- the blast page lets the user voxelize on demand at a chosen
    resolution. ``has_voxels`` says if voxels.json exists; ``voxel_count`` /
    ``blocks_per_meter`` (from the manifest) are null until voxelized. A short
    ``description`` comes from description.txt if present."""
    out = []
    for d in SCENES_ROOT.iterdir():
        if not d.is_dir():
            continue
        man = d / "manifest.json"
        if not man.is_file():
            continue
        name = d.name
        voxel_count = bpm = None
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
            name = m.get("name") or name
            stats = m.get("stats") or {}
            voxel_count = stats.get("voxel_count")
            bpm = stats.get("blocks_per_meter")
        except Exception:
            pass
        description = None
        dt = d / "description.txt"
        if dt.is_file():
            try:
                description = dt.read_text(encoding="utf-8").strip() or None
                if description and len(description) > 120:
                    description = description[:120].rstrip() + "…"
            except Exception:
                pass
        out.append({"id": d.name, "name": name,
                    "has_voxels": (d / "voxels.json").is_file(),
                    "voxel_count": voxel_count, "blocks_per_meter": bpm,
                    "description": description, "mtime": man.stat().st_mtime})
    out.sort(key=lambda s: s["mtime"], reverse=True)
    return {"scenes": out}


@app.get("/api/scenes/{scene_id}/{filename}")
def scene_file(scene_id: str, filename: str):
    if not scene_id.isalnum() or "/" in filename or ".." in filename:
        raise HTTPException(400, "bad path")
    fp = SCENES_ROOT / scene_id / filename
    if not fp.is_file():
        raise HTTPException(404, "not found")
    # voxels.json is overwritten by re-voxelization; never let a stale copy be cached.
    headers = {"Cache-Control": "no-store"} if filename == "voxels.json" else None
    return FileResponse(fp, headers=headers)


# static UI (index.html upload page + viewer.html) served at the site root.
# Dev server: force revalidation on the UI assets so edits to viewer.html / *.js show up
# on the next load instead of being masked by the browser's ES-module cache (a stale
# cached inspect.js is why a code change can look like it "didn't take"). Unchanged files
# still 304 via ETag, so this is cheap; only the GLB/voxels (served by scene_file) are
# left on their own caching rules.
class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

app.mount("/", NoCacheStatic(directory=str(_HERE / "static"), html=True), name="static")
