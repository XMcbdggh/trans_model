"""Versioned external REST API (``/api/v1``) for the agent3d service.

This is the clean, self-describing surface intended for *programmatic* clients
(other backends / scripts), as opposed to the browser-driven ``/api/*`` routes in
``server.py`` that back the web UI. It is mounted onto the same FastAPI app, so it
shares the same process, job store and artifact directory.

Flow (async, resource-oriented):

    POST /api/v1/models            multipart: images + description (or a ready ``spec``)
        -> { "job_id": "..." }
    GET  /api/v1/jobs/{job_id}     poll; on success returns { status, scene_id, resources }
    GET  /api/v1/models                       list generated models
    GET  /api/v1/models/{scene_id}            one model's metadata + resource links
    GET  /api/v1/models/{scene_id}/glb        download model.glb        (model/gltf-binary)
    GET  /api/v1/models/{scene_id}/litematic  download model.litematic  (octet-stream)
    GET  /api/v1/models/{scene_id}/voxels     download voxels.json      (application/json)

The heavy lifting (job store, worker threads, artifact layout) lives in
``server.py``; this module only re-shapes those into a stable public contract. It
reaches into ``server`` lazily (inside handlers) so there is no import cycle: this
module never imports ``server`` at module load time.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/v1", tags=["v1"])

# artifacts that a generated scene can expose, mapped to (filename, media_type,
# immutable?). ``immutable`` files are content-addressed by scene_id and never change
# once written, so they get a long-lived cache; voxels.json is re-written by
# re-voxelization, so it must not be cached.
_ARTIFACTS = {
    "glb": ("model.glb", "model/gltf-binary", True),
    "litematic": ("model.litematic", "application/octet-stream", True),
    "voxels": ("voxels.json", "application/json", False),
}


def _srv():
    """Lazily fetch the server module (shared job store + artifact helpers).

    Imported inside handlers, not at module load, so ``server.py`` can safely
    ``include_router`` us at its bottom without a circular import."""
    from agent3d.webapp import server
    return server


def _validate_scene_id(scene_id: str) -> None:
    if not scene_id.isalnum():
        raise HTTPException(400, "bad scene_id")


def _scene_dir_or_404(scene_id: str) -> Path:
    """Resolve a scene directory that actually holds a generated model (has a
    manifest.json). 400 on a malformed id, 404 if unknown."""
    _validate_scene_id(scene_id)
    scene_dir = _srv().SCENES_ROOT / scene_id
    if not (scene_dir / "manifest.json").is_file():
        raise HTTPException(404, "unknown model")
    return scene_dir


def _resources(request: Request, scene_id: str) -> dict:
    """Absolute URLs for a scene's downloadable artifacts + its metadata endpoint.

    Always URLs (never bare file paths) so the contract is stable even if the
    storage backend later moves behind object storage / a CDN."""
    base = str(request.base_url).rstrip("/")
    model_url = f"{base}/api/v1/models/{scene_id}"
    return {
        "meta": model_url,
        "glb": f"{model_url}/glb",
        "litematic": f"{model_url}/litematic",
        "voxels": f"{model_url}/voxels",
    }


def _job_status(job: dict) -> str:
    """Map the internal job flags onto a stable public status enum."""
    if job.get("error"):
        return "failed"
    if job.get("done"):
        return "succeeded"
    if job.get("stage") == "排队中":
        return "queued"
    return "running"


@router.post("/models")
async def create_model(
    request: Request,
    description: str = Form(""),
    spec: str = Form(""),
    mode: str = Form(""),
    bpm: str = Form(""),
    images: list[UploadFile] = None,
):
    """Start a 2D->3D generation job and return ``{ job_id }``.

    Supply EITHER image files (+ optional ``description``) OR a ready Building Spec
    JSON in ``spec``. ``mode`` = param|spec picks the image path (defaults to the
    server's ``AGENT3D_GEN_MODE``). ``bpm`` sets the voxel resolution. Unlike the
    web ``/api/generate``, this always produces the litematic + voxels too, so the
    resulting model is immediately downloadable in every format."""
    srv = _srv()
    gen_mode = (mode or srv.GEN_MODE).lower()
    payload = None
    if not spec.strip():
        payload = await srv._read_images(images)  # read while request-bound
    bpm_val = srv._parse_bpm(bpm)

    job_id = srv._new_job()
    threading.Thread(
        target=srv._run_generate,
        args=(job_id, payload, spec, description, gen_mode),
        kwargs={"make_voxels": True, "blocks_per_meter": bpm_val},
        daemon=True,
    ).start()
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    """Poll a generation job. Returns a stable ``status`` enum
    (queued|running|succeeded|failed) plus a human ``stage``. On ``succeeded`` also
    returns ``scene_id`` and ``resources`` (download URLs); on ``failed`` an
    ``error`` string."""
    srv = _srv()
    job = srv.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    import time

    status = _job_status(job)
    out = {
        "job_id": job_id,
        "status": status,
        "stage": job.get("stage"),
        "detail": job.get("detail", ""),
        "elapsed": round(time.time() - job["t0"], 1),
        "model": job.get("model"),
        "error": job.get("error"),
    }
    result = job.get("result") or {}
    scene_id = result.get("scene_id")
    if status == "succeeded" and scene_id:
        out["scene_id"] = scene_id
        out["name"] = result.get("name")
        out["stats"] = result.get("stats")
        out["resources"] = _resources(request, scene_id)
    return out


def _read_description(scene_dir: Path) -> str | None:
    dt = scene_dir / "description.txt"
    if not dt.is_file():
        return None
    try:
        return dt.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


@router.get("/models")
def list_models(request: Request):
    """List generated models (newest first), each with metadata + resource URLs."""
    srv = _srv()
    scenes = srv.list_scenes()["scenes"]
    models = []
    for s in scenes:
        models.append({
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
            "has_voxels": s["has_voxels"],
            "voxel_count": s["voxel_count"],
            "blocks_per_meter": s["blocks_per_meter"],
            "created_at": s["mtime"],
            "resources": _resources(request, s["id"]),
        })
    return {"models": models}


@router.get("/models/{scene_id}")
def get_model(scene_id: str, request: Request):
    """Metadata for one generated model (from its manifest.json) + resource URLs."""
    scene_dir = _scene_dir_or_404(scene_id)
    manifest = json.loads((scene_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        "id": scene_id,
        "name": manifest.get("name") or scene_id,
        "description": _read_description(scene_dir),
        "stats": manifest.get("stats") or {},
        "has_voxels": (scene_dir / "voxels.json").is_file(),
        "resources": _resources(request, scene_id),
    }


def _download(scene_dir: Path, artifact: str) -> FileResponse:
    """Serve one artifact file with the right media type, an attachment filename and
    cache policy. 409 if the scene exists but the file isn't ready yet (still
    generating / not voxelized). Starlette's FileResponse adds ETag + Last-Modified
    and honours Range/If-Range, so downloads are conditional and resumable for free."""
    filename, media_type, immutable = _ARTIFACTS[artifact]
    fp = scene_dir / filename
    if not fp.is_file():
        raise HTTPException(
            409, f"{filename} is not ready yet (model still generating or not voxelized)")
    if immutable:
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    else:
        headers = {"Cache-Control": "no-store"}
    return FileResponse(fp, media_type=media_type, filename=filename, headers=headers)


@router.get("/models/{scene_id}/glb")
def download_glb(scene_id: str):
    """Download the visual mesh ``model.glb`` (model/gltf-binary)."""
    return _download(_scene_dir_or_404(scene_id), "glb")


@router.get("/models/{scene_id}/litematic")
def download_litematic(scene_id: str):
    """Download the Minecraft voxel schematic ``model.litematic``."""
    return _download(_scene_dir_or_404(scene_id), "litematic")


@router.get("/models/{scene_id}/voxels")
def download_voxels(scene_id: str):
    """Download the decoded ``voxels.json`` (browser/blast-ready voxel data)."""
    return _download(_scene_dir_or_404(scene_id), "voxels")
