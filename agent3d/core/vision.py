"""Server-side vision: image(s) + text description -> Building Spec (Layer 1).

Used by the web app so ordinary users never touch an agent. The same contract
(system prompt + schema) is mirrored in the Skill A SKILL.md for agent use.

Uses the Anthropic Messages API with a forced tool call, so the model MUST
return an object matching the Building Spec schema (no free-form parsing). One
automatic repair retry is attempted if validation fails.

Config (model / API key / base URL) resolves via agent3d.core.settings:
  UI settings (⚙️ 模型设置, persisted) > env/.env (ANTHROPIC_API_KEY,
  ANTHROPIC_BASE_URL, WIDE_SIM_VISION_MODEL) > default "claude-sonnet-5".
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from agent3d.core import settings

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "building-spec.schema.json"


def _anthropic_client():
    """Anthropic/OpenRouter client with a few automatic retries + a bounded per-attempt
    timeout, so a transient network blip (the APIConnectionError users hit on flaky
    networks) is retried instead of failing the whole request. Key/base URL come from
    agent3d.core.settings (web-UI overrides, then ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL)."""
    import anthropic  # lazy: only web/vision paths need the SDK
    key = settings.api_key()
    if not key:
        raise RuntimeError(
            "未配置模型 API Key。请点页面右上角「⚙️ 模型设置」填写 API Key"
            "（及可选的 Base URL / 模型名），或在 .env 中设置 ANTHROPIC_API_KEY。")
    kwargs = {
        "api_key": key,
        "max_retries": int(os.getenv("AGENT3D_MAX_RETRIES", "4")),
        "timeout": float(os.getenv("AGENT3D_HTTP_TIMEOUT", "90")),
    }
    base = settings.base_url()
    if base:
        kwargs["base_url"] = base
    return anthropic.Anthropic(**kwargs)

SYSTEM_PROMPT = """\
You convert a photograph or drawing of a building (plus an optional text
description) into a structured Building Spec that a deterministic generator turns
into a 3D model. You are NOT asked for exact millimetre survey data -- you give
sensible high-level estimates a downstream script expands into precise geometry.

Rules:
- Work in metres. The site plan is a top-down XY plane: X = east, Y = north.
- Place each building by a rectangular footprint [x0, y0, x1, y1]. Keep buildings
  inside the site if a site size is given, and do not overlap footprints.
- Estimate floors and floor_height_m from apparent proportions (typical storey
  3-4.5 m). Count windows per visible facade as an integer per side -- never
  invent coordinates; the generator distributes them.
- Choose a style from: persian, modern, classical, islamic. Choose materials
  from: reinforced_concrete, stone_masonry, brick_masonry, steel, timber.
- Only include features you can actually see or that the text states (domes,
  pools, gardens, trees, vehicles, perimeter wall).
- Prefer fewer, correct elements over many speculative ones.
Return ONLY the Building Spec via the emit_building_spec tool."""


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _image_block(image_bytes: bytes, media_type: str) -> dict:
    return {"type": "image", "source": {
        "type": "base64", "media_type": media_type,
        "data": base64.standard_b64encode(image_bytes).decode("ascii")}}


def image_to_spec(images: list[tuple[bytes, str]], description: str = "",
                  *, model: str | None = None) -> dict:
    """images: list of (bytes, media_type e.g. 'image/jpeg'). Returns a Building Spec dict."""
    client = _anthropic_client()  # key/base URL from settings (UI override, then env)
    model = model or settings.model()
    schema = _load_schema()

    tool = {"name": "emit_building_spec",
            "description": "Emit the structured Building Spec for the depicted building(s).",
            "input_schema": schema}

    content: list[dict] = [_image_block(b, mt) for b, mt in images]
    text = "Text description (may be empty):\n" + (description or "(none)")
    content.append({"type": "text", "text": text})

    messages = [{"role": "user", "content": content}]
    spec = _call(client, model, tool, messages)

    ok, err = validate_spec(spec)
    if not ok:
        # one self-repair round trip
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": "prev", "name": tool["name"], "input": spec}]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "prev",
             "content": f"Validation failed: {err}. Re-emit a corrected Building Spec."}]})
        spec = _call(client, model, tool, messages)
        ok, err = validate_spec(spec)
        if not ok:
            raise ValueError(f"Building Spec invalid after repair: {err}")
    return spec


def _call(client, model, tool, messages) -> dict:
    resp = client.messages.create(
        model=model, max_tokens=4096, system=SYSTEM_PROMPT, tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]}, messages=messages)
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise RuntimeError("model did not return a Building Spec tool call")


def validate_spec(spec: dict) -> tuple[bool, str]:
    """Light structural validation independent of the pipeline (fast fail before build)."""
    if not isinstance(spec, dict):
        return False, "spec is not an object"
    buildings = spec.get("buildings")
    if not isinstance(buildings, list) or not buildings:
        return False, "buildings must be a non-empty array"
    for i, b in enumerate(buildings):
        fp = b.get("footprint")
        if not (isinstance(fp, list) and len(fp) == 4):
            return False, f"buildings[{i}].footprint must be [x0,y0,x1,y1]"
        x0, y0, x1, y1 = fp
        if not (x1 > x0 and y1 > y0):
            return False, f"buildings[{i}].footprint must have x1>x0 and y1>y0"
        if int(b.get("floors", 1)) < 1:
            return False, f"buildings[{i}].floors must be >= 1"
    return True, ""


# ---------------------------------------------------------------------------
# Direct-param path: image + text -> full stand_trans param.json (Layer 2),
# skipping the Building Spec. Uses the general system prompt in
# ../prompts/direct_param_system_prompt.md and an automatic validate->repair
# loop driven by the real pipeline validators (so the model fixes its own
# geometry/host_id/polygon mistakes before we ever try to build).
# ---------------------------------------------------------------------------

_PARAM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "direct_param_system_prompt.md"


def _load_param_prompt() -> str:
    return _PARAM_PROMPT_PATH.read_text(encoding="utf-8")


def _strip_json_fences(raw: str) -> str:
    """Best-effort removal of markdown code fences the model may add despite instructions."""
    t = raw.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl != -1 else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def dry_validate_param(param: dict) -> None:
    """Run the cheap front of the pipeline (validate -> normalize -> to_bim) so any
    schema / host_id / polygon / opening error surfaces as an exception we can feed
    back to the model. Raises on the first problem; returns None if the param would
    pass into geometry generation."""
    from stand_trans.step1_normalize.normalize import validate, normalize  # noqa: E402
    from stand_trans.step2_bim import to_bim                               # noqa: E402

    validate(param)
    to_bim(normalize(param))


def image_to_param(images: list[tuple[bytes, str]], description: str = "",
                   *, model: str | None = None, max_repairs: int = 2, progress=None) -> dict:
    """images -> a validated stand_trans param.json (dict). Emits the full param
    directly from the model, then runs up to ``max_repairs`` validate->repair rounds
    against the real pipeline validators. Raises ValueError if still invalid.
    ``progress(stage)`` is called before each model attempt for live status."""
    client = _anthropic_client()  # key/base URL from settings (UI override, then env)
    model = model or settings.model()
    max_tokens = int(os.getenv("AGENT3D_MAX_TOKENS", "16000"))
    system = _load_param_prompt()

    content: list[dict] = [_image_block(b, mt) for b, mt in images]
    content.append({"type": "text",
                    "text": "Text description (may be empty):\n" + (description or "(none)")
                            + "\n\nReturn ONLY the param.json for the depicted building(s)."})
    messages = [{"role": "user", "content": content}]

    last_err = ""
    for attempt in range(max_repairs + 1):
        if progress:
            progress("AI 生成 param.json" + ("" if attempt == 0 else f"（几何修复第 {attempt} 次）"))
        raw = _call_text(client, model, system, messages, max_tokens)
        param = None
        try:
            param = json.loads(_strip_json_fences(raw))
        except Exception as exc:  # malformed / truncated JSON
            last_err = f"JSON parse error: {exc}"
        if param is not None:
            try:
                dry_validate_param(param)
                return param
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
        # feed the exact error back for a targeted repair
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user",
                         "content": f"VALIDATION ERROR: {last_err}\nFix ONLY this problem and "
                                    f"re-emit the FULL corrected param.json. Output JSON only."})
    raise ValueError(f"param.json invalid after {max_repairs} repair attempt(s): {last_err}")


def _call_text(client, model, system, messages, max_tokens: int) -> str:
    resp = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                  messages=messages)
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


# ---------------------------------------------------------------------------
# Stage-1 of the two-step flow: image(s) + optional notes -> a natural-language
# feature description ("特征描述"). The web UI shows this to the user for editing
# before it is fed, together with the image, into image_to_param() (stage 2).
# ---------------------------------------------------------------------------

_DESCRIBE_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "describe_building.md"


def _load_describe_prompt() -> str:
    return _DESCRIBE_PROMPT_PATH.read_text(encoding="utf-8")


def describe_building(images: list[tuple[bytes, str]], description: str = "",
                      *, model: str | None = None) -> str:
    """Analyse the building image(s) and return a structured natural-language feature
    description (no JSON, no coordinates). Reuses the same image/text plumbing as the
    other vision calls; model overridable via WIDE_SIM_DESCRIBE_MODEL."""
    client = _anthropic_client()  # key/base URL from settings (UI override, then env)
    model = model or settings.describe_model()
    max_tokens = int(os.getenv("AGENT3D_DESC_MAX_TOKENS", "2000"))
    system = _load_describe_prompt()

    content: list[dict] = [_image_block(b, mt) for b, mt in images]
    content.append({"type": "text", "text": "用户补充说明（可能为空）：\n" + (description or "（无）")})
    messages = [{"role": "user", "content": content}]
    return _call_text(client, model, system, messages, max_tokens).strip()


def ping_model() -> dict:
    """Tiny live round-trip that validates the configured key / base URL / model.
    Costs ~1 output token; used by the ⚙️ settings 'test connection' button (which the
    user triggers explicitly). Raises on any auth/URL/model error."""
    client = _anthropic_client()
    m = settings.model()
    client.messages.create(model=m, max_tokens=1,
                           messages=[{"role": "user", "content": "ping"}])
    return {"model": m, "base_url": settings.base_url() or "(default)"}
