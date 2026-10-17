# -*- coding: UTF-8 -*-
"""Search civitai: models (GET /api/v1/models) and the images people posted (GET /api/v1/images).

Used by the API routes GET /civitai/models and GET /civitai/images (and their MCP tools
civitai_search_models / civitai_search_images). The answer is civitai's own JSON plus a
"_query" object that says how the search was run (base models after alias resolution, the
model a picture search was narrowed to, ...).

Requests go to the domain set in Settings ("Civitai Domain"), with the API key and proxy from
Settings, like every other civitai call of this extension.
"""
import re

import requests

from . import civitai
from . import util

# civitai baseModel names are exact and case-sensitive ("Illustrious", "SD 1.5");
# these aliases (lower-case) map what people type to them, families to several.
_WAN = ["Wan Video", "Wan Video 14B t2v", "Wan Video 14B i2v 480p", "Wan Video 14B i2v 720p", "Wan Video 1.3B t2v",
        "Wan Video 2.2 T2V-A14B", "Wan Video 2.2 I2V-A14B", "Wan Video 2.2 TI2V-5B"]
_KLEIN = ["Flux.2 Klein 9B", "Flux.2 Klein 9B-base", "Flux.2 Klein 4B", "Flux.2 Klein 4B-base"]
BASE_MODEL_ALIASES = {
    "sd 1.5": ["SD 1.5"], "sd1.5": ["SD 1.5"], "sd15": ["SD 1.5"], "1.5": ["SD 1.5"],
    "sd 1.4": ["SD 1.4"], "sd1.4": ["SD 1.4"],
    "sdxl": ["SDXL 1.0"], "sdxl 1.0": ["SDXL 1.0"], "xl": ["SDXL 1.0"],
    "pony": ["Pony"], "ponyxl": ["Pony"], "pony xl": ["Pony"], "pony diffusion": ["Pony"],
    "illustrious": ["Illustrious"], "illustrious xl": ["Illustrious"], "illustriousxl": ["Illustrious"],
    "illu": ["Illustrious"], "il": ["Illustrious"], "ilxl": ["Illustrious"],
    "noobai": ["NoobAI"], "noob": ["NoobAI"], "noobai xl": ["NoobAI"],
    "flux": ["Flux.1 D"], "flux.1": ["Flux.1 D", "Flux.1 S"], "flux.1 d": ["Flux.1 D"], "flux dev": ["Flux.1 D"],
    "flux.1 s": ["Flux.1 S"], "flux schnell": ["Flux.1 S"],
    "flux.2": ["Flux.2 D"] + _KLEIN, "flux 2": ["Flux.2 D"] + _KLEIN, "flux.2 d": ["Flux.2 D"],
    "klein": _KLEIN, "flux.2 klein": _KLEIN,
    "anima": ["Anima"], "krea": ["Krea 2"], "krea 2": ["Krea 2"],
    "z image": ["ZImageTurbo", "ZImageBase"], "z-image": ["ZImageTurbo", "ZImageBase"], "zimage": ["ZImageTurbo", "ZImageBase"],
    "z image turbo": ["ZImageTurbo"], "z-image turbo": ["ZImageTurbo"], "zit": ["ZImageTurbo"], "zimageturbo": ["ZImageTurbo"],
    "z image base": ["ZImageBase"], "zimagebase": ["ZImageBase"],
    "qwen": ["Qwen", "Qwen 2", "Qwen 2.1"], "qwen image": ["Qwen", "Qwen 2", "Qwen 2.1"],
    "wan": _WAN, "wan video": _WAN, "wan 2.2": [w for w in _WAN if "2.2" in w],
    "ltxv": ["LTXV", "LTXV2", "LTXV 2.3", "LTXV 2.5"], "ltx": ["LTXV", "LTXV2", "LTXV 2.3", "LTXV 2.5"],
    "hunyuan": ["Hunyuan Video"], "chroma": ["Chroma"], "hidream": ["HiDream"], "lumina": ["Lumina"],
    "ernie": ["Ernie"], "minimax": ["MiniMax H3"], "other": ["Other"],
}


def _api_base() -> str:
    return civitai.url_dict["images"].rsplit("/images", 1)[0]  # https://<domain>/api/v1


def _get(path: str, params: dict = None) -> dict:
    q = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
    try:
        r = requests.get(_api_base() + path, params=q, headers=util.def_headers, proxies=util.proxies,
                         timeout=(15, 60))
    except requests.RequestException as e:
        raise RuntimeError(f"civitai request failed: {e}")
    if not r.ok:
        raise RuntimeError(f"civitai {path} failed: HTTP {r.status_code} {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"civitai {path} answered something that is not JSON")


def normalize_base_models(values) -> list:
    """['illustrious', 'SD1.5,pony'] -> ['Illustrious', 'SD 1.5', 'Pony']; unknown names pass through."""
    out = []
    for raw in values or []:
        for part in str(raw).split(","):
            key = part.strip()
            if not key:
                continue
            low = re.sub(r"\s+", " ", key.lower())
            names = BASE_MODEL_ALIASES.get(low) or BASE_MODEL_ALIASES.get(low.replace(" ", "")) or [key]
            out.extend(n for n in names if n not in out)
    return out


def _matches_base(model: dict, base_models: list) -> bool:
    return any(v.get("baseModel") in base_models for v in model.get("modelVersions") or [])


def search_models(query=None, types=None, base_model=None, limit=6, sort=None, period=None,
                  username=None, tag=None, nsfw=None, cursor=None) -> dict:
    base_models = normalize_base_models(base_model)
    params = {
        "query": query, "limit": limit, "cursor": cursor, "types": types, "sort": sort, "period": period,
        "username": username, "tag": tag, "baseModels": base_models or None,
        "nsfw": str(nsfw).lower() if nsfw is not None else None,
    }
    data = _get("/models", params)
    items = data.get("items") or []
    extra = []
    if base_models and query and not cursor and len(items) < limit:
        # civitai's text search combined with baseModels misses many models: also search the
        # text alone and with the base model name added, keeping models that have a version
        # for one of the wanted base models
        seen = {m.get("id") for m in items}
        for q in [query] + [f"{query} {bm}" for bm in base_models[:3]]:
            for m in _get("/models", {**params, "baseModels": None, "query": q, "limit": 100}).get("items") or []:
                if m.get("id") not in seen and _matches_base(m, base_models):
                    seen.add(m.get("id"))
                    extra.append(m)
            if len(items) + len(extra) >= limit:
                break
        if extra:
            data["items"] = (items + extra)[:limit]
            data["metadata"] = {}  # the cursor of the first query does not cover the merged list
    data["_query"] = {"base_models": base_models, "text_search_fallback": bool(extra)}
    return data


def parse_model_ref(ref) -> tuple:
    """'12345' or a civitai page URL (…/models/12345/name?modelVersionId=678) -> (model_id, version_id)."""
    ref = str(ref or "").strip()
    if not ref:
        return None, None
    if ref.isdigit():
        return int(ref), None
    m = re.search(r"/models/(\d+)", ref)
    v = re.search(r"modelVersionId=(\d+)", ref)
    return (int(m.group(1)) if m else None), (int(v.group(1)) if v else None)


def search_images(username=None, model=None, model_version_id=None, sort=None, period=None, nsfw=None,
                  limit=12, cursor=None) -> dict:
    model_id, version_id = None, model_version_id
    if model:
        mid, vid = parse_model_ref(model)
        if mid is None and vid is None:
            raise ValueError(f"model must be a civitai model id or model page URL, got {model!r}")
        model_id, version_id = mid, version_id or vid
    if not any((model_id, version_id, username)):
        raise ValueError("give at least one of username, model or model_version_id")
    params = {
        "modelVersionId": version_id,
        "modelId": None if version_id else model_id,
        "username": username, "limit": limit, "cursor": cursor, "sort": sort, "period": period,
        "nsfw": nsfw.lower() if nsfw and nsfw.lower() in ("true", "false") else nsfw,
        "withMeta": "true",  # without it the API leaves out prompt / sampler / seed / resources
    }
    data = _get("/images", params)
    model_name = None
    if version_id:
        v = _get(f"/model-versions/{version_id}")
        model_name = f"{(v.get('model') or {}).get('name') or '?'} / {v.get('name') or version_id}"
        model_id = model_id or v.get("modelId")
    elif model_id:
        m = _get(f"/models/{model_id}")
        model_name = m.get("name")
        if not data.get("items") and not cursor:
            # the modelId filter returns nothing for some models; ask per version instead
            items = []
            for v in (m.get("modelVersions") or [])[:8]:
                items += _get("/images", {**params, "modelId": None, "modelVersionId": v.get("id")}).get("items") or []
                if len(items) >= limit:
                    break
            data = {"items": items[:limit], "metadata": {}}
    data["_query"] = {
        "username": username, "model_id": model_id, "model_version_id": version_id, "model_name": model_name,
        "sort": sort, "period": period, "nsfw": nsfw,
    }
    return data
