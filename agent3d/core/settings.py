"""Runtime LLM connection settings (model / API key / base URL), configurable
from the web UI (⚙️ 模型设置) instead of only via environment variables.

Resolution order per field (first non-empty wins):
    1. UI override, persisted to agent3d_settings.json (set via POST /api/settings)
    2. environment / .env  (ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, WIDE_SIM_VISION_MODEL …)
    3. built-in default

The API key is persisted locally at the same trust level as the existing .env, but
is NEVER returned to the browser in full -- as_public() exposes only a masked hint.
Only fields the user explicitly sets in the UI are written to the file, so relying
purely on .env keeps the secret out of agent3d_settings.json entirely.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_DEFAULT_MODEL = "claude-sonnet-5"
_FIELDS = ("model", "describe_model", "base_url", "api_key")

_LOCK = threading.Lock()
_STORE_PATH = Path(os.getenv(
    "AGENT3D_SETTINGS",
    str(Path(__file__).resolve().parents[2] / "agent3d_settings.json")))

_overrides: dict = {}


def _load() -> None:
    """(Re)load the UI overrides from disk. Missing/corrupt file -> no overrides."""
    global _overrides
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        _overrides = data if isinstance(data, dict) else {}
    except Exception:
        _overrides = {}


_load()


def _resolve(field: str, *env_keys: str, default: str = "") -> str:
    val = str(_overrides.get(field) or "").strip()
    if val:
        return val
    for k in env_keys:
        v = os.getenv(k)
        if v and v.strip():
            return v.strip()
    return default


def api_key() -> str:
    return _resolve("api_key", "ANTHROPIC_API_KEY")


def base_url() -> str:
    return _resolve("base_url", "ANTHROPIC_BASE_URL")


def model() -> str:
    return _resolve("model", "WIDE_SIM_VISION_MODEL", default=_DEFAULT_MODEL)


def describe_model() -> str:
    """Model for the natural-language describe step; falls back to the main model."""
    return _resolve("describe_model", "WIDE_SIM_DESCRIBE_MODEL") or model()


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:6]}…{key[-4:]}" if len(key) > 14 else "…" + key[-2:]


def _origin(field: str, *env_keys: str) -> str:
    """Where the effective value comes from: 'ui' (this app's settings), 'env', or 'default'."""
    if str(_overrides.get(field) or "").strip():
        return "ui"
    if any(os.getenv(k) and os.getenv(k).strip() for k in env_keys):
        return "env"
    return "default"


def as_public() -> dict:
    """Browser-safe snapshot: never includes the raw key -- only a masked hint plus
    the source (ui / env / default) of each effective value so the UI can label it."""
    key = api_key()
    return {
        "model": model(),
        "describe_model": str(_overrides.get("describe_model") or "").strip(),
        "base_url": base_url(),
        "has_key": bool(key),
        "key_hint": _mask(key),
        "origin": {
            "model": _origin("model", "WIDE_SIM_VISION_MODEL"),
            "base_url": _origin("base_url", "ANTHROPIC_BASE_URL"),
            "api_key": _origin("api_key", "ANTHROPIC_API_KEY"),
        },
    }


def update(patch: dict) -> dict:
    """Apply the provided fields and persist. Per-field semantics:
        * field absent / value None -> unchanged (lets the UI omit the key to keep it)
        * value "" (empty)          -> clears the override (revert to env / default)
        * value non-empty           -> set the override
    Returns the new as_public() snapshot."""
    if not isinstance(patch, dict):
        raise ValueError("settings payload must be an object")
    with _LOCK:
        for field in _FIELDS:
            if field not in patch or patch[field] is None:
                continue
            val = str(patch[field]).strip()
            if val:
                _overrides[field] = val
            else:
                _overrides.pop(field, None)
        try:
            _STORE_PATH.write_text(
                json.dumps(_overrides, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass   # in-memory overrides still apply even if the file can't be written
    return as_public()
