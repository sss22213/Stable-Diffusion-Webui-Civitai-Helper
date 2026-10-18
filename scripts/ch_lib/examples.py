# -*- coding: UTF-8 -*-
"""Example images and example prompts of local models.

A model's .civitai.info file (a civitai *version* record) carries an
`images` list: the example images shown on the civitai page, most of them
with the generation parameters (`meta`: prompt, negative prompt, sampler,
steps, cfg, seed, ...). Civitai Helper only ever used the first one as the
card preview. This module makes the rest usable:

* download_examples()   save the example images next to the model as
                        `<model>.example_NN.<ext>`  (NN = position in the
                        civitai list, so the file maps back to its prompt)
* write_card_info()     write the trigger words and the first example prompt
                        into the WebUI card metadata file `<model>.json`
                        (fields `description` and `notes`, shown on the
                        Extra Networks card and in its edit dialog)
* fetch_preview()       (re)download the missing `<model>.preview.png`
* list_examples()       everything above as data, for the HTTP API
* add_user_examples()   save images that civitai *users* posted (found with
                        GET /api/v1/images, e.g. "what user X made with this
                        LoRA") as extra examples `<model>.example_101.<ext>`…
                        Their prompts live in the sidecar `<model>.examples.json`
                        because they are not part of the civitai info file.
* add_local_examples()  save images generated locally (PNG / JPEG / WebP, with the
                        WebUI's generation info) as extra examples the same way;
                        their sidecar entries have "source": "local".

Numbering: 1-100 = position in the civitai info list, 101-999 = images added
from civitai users or from local files (one shared range).

All three "run_*" helpers below take a list of model types (ti / hyper /
ckp / lora) or one (type, path) pair, and are shared by the Gradio buttons
and the HTTP API (see api.py).
"""
import hashlib
import io
import json
import os
import re
import shutil
import time
from urllib.parse import quote, urlparse

from . import util
from . import model
from . import civitai
from . import local_models


EXAMPLE_TAG = ".example_"
USER_BASE = 101              # first index used for user-posted example images
USER_MAX = 999
USER_SIDECAR = ".examples.json"
LOCAL_SOURCE = "local"         # sidecar entries of locally generated images
LOCAL_MAX_BYTES = 30 * 1024 * 1024
EXAMPLE_RE = re.compile(r"\.example_(\d{2,3})\.(png|jpe?g|webp|gif)$", re.IGNORECASE)
IMAGE_EXTS = ("png", "jpg", "jpeg", "webp", "gif")
DESC_PROMPT_LIMIT = 600      # card description: chars of example prompt kept
DESC_NEG_LIMIT = 200
NOTES_LIMIT = 40000          # card notes: total chars cap

# civitai `baseModel` -> Forge preset name (network.SD_VERSION / PresetArch)
_BASE_MODEL_MAP = (
    ("flux.2", "klein"),
    ("flux", "flux"),
    ("qwen", "qwen"),
    ("lumina", "lumina"),
    ("z image", "zit"),
    ("z-image", "zit"),
    ("wan", "wan"),
    ("anima", "anima"),
    ("ernie", "ernie"),
    ("sdxl", "xl"),
    ("pony", "xl"),
    ("illustrious", "xl"),
    ("noobai", "xl"),
    ("sd 1.", "sd"),
    ("sd 2.", "sd"),
)


# ---------------------------------------------------------------------------
# civitai info helpers
# ---------------------------------------------------------------------------

def sd_version_from_base_model(base_model) -> str:
    """Forge preset name for a civitai baseModel string ("" when unsure)."""
    b = (base_model or "").strip().lower()
    if not b:
        return ""
    for needle, preset in _BASE_MODEL_MAP:
        if needle in b:
            return preset
    return ""


def load_info(model_path: str):
    """The .civitai.info dict of a model, or None when missing / not on civitai."""
    base, _ = os.path.splitext(model_path)
    info_file = base + civitai.suffix + model.info_ext
    if not os.path.isfile(info_file):
        return None
    info = model.load_model_info(info_file)
    if not info or not info.get("id"):
        return None
    return info


def _ext_from_url(url: str) -> str:
    path = urlparse(url or "").path
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext if ext in IMAGE_EXTS else "jpeg"


def _num(v):
    """meta values come as int, float or str; keep numbers, drop junk."""
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return s or None
    return None


def image_view(index: int, img: dict) -> dict:
    """Trimmed view of one civitai image entry (index is 1-based position)."""
    meta = img.get("meta") or {}
    prompt = meta.get("prompt") or ""
    return {
        "index": index,
        "url": img.get("url"),
        "type": img.get("type"),
        "width": img.get("width"),
        "height": img.get("height"),
        "nsfw": civitai.is_nsfw_image(img),
        "nsfw_level": img.get("nsfwLevel"),
        "has_prompt": bool(prompt),
        "prompt": prompt,
        "negative_prompt": meta.get("negativePrompt") or "",
        "steps": _num(meta.get("steps")),
        "sampler": meta.get("sampler") or meta.get("Sampler"),
        "cfg_scale": _num(meta.get("cfgScale")),
        "seed": _num(meta.get("seed")),
        "size": meta.get("Size"),
        "clip_skip": _num(meta.get("clipSkip") or meta.get("Clip skip")),
        "model": meta.get("Model"),
    }


def info_images(info: dict) -> list:
    """(index, img) for every entry of info["images"] that is a still image."""
    out = []
    for i, img in enumerate(info.get("images") or [], 1):
        if i >= USER_BASE:
            break
        if not isinstance(img, dict):
            continue
        if img.get("type") not in (None, "image"):
            continue
        if not img.get("url"):
            continue
        out.append((i, img))
    return out


# ---------------------------------------------------------------------------
# local example files
# ---------------------------------------------------------------------------

_listing_cache = {}   # dir -> (mtime, sorted file names)


def _dir_listing(folder: str) -> list:
    try:
        mtime = os.stat(folder).st_mtime
    except OSError:
        return []
    hit = _listing_cache.get(folder)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    _listing_cache[folder] = (mtime, names)
    return names


def example_files(model_path: str) -> list:
    """Existing `<model>.example_NN.<ext>` files (absolute paths, sorted by NN)."""
    folder = os.path.dirname(model_path)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    prefix = stem + EXAMPLE_TAG
    found = []
    for name in _dir_listing(folder):
        if not name.startswith(prefix):
            continue
        m = EXAMPLE_RE.search(name[len(stem):])
        if not m:
            continue
        found.append((int(m.group(1)), os.path.join(folder, name)))
    found.sort()
    return [p for _, p in found]


def example_index_of(path: str):
    m = EXAMPLE_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else None


def example_file_path(model_path: str, index: int, url: str) -> str:
    base, _ = os.path.splitext(model_path)
    return f"{base}{EXAMPLE_TAG}{index:02d}.{_ext_from_url(url)}"


def thumb_url(path: str) -> str:
    """URL the WebUI serves this image at (route from modules/ui_extra_networks.py)."""
    return "./sd_extra_networks/thumb?filename=" + quote(os.path.abspath(path), safe="")


def list_examples(model_type: str, folder: str, path: str) -> dict:
    """Examples of one model: civitai list merged with what is on disk."""
    info = load_info(path)
    local = {example_index_of(p): p for p in example_files(path)}
    images = []
    if info:
        for index, img in info_images(info):
            v = image_view(index, img)
            lp = local.get(index)
            v["local_file"] = util.get_relative_path(lp, folder).replace("\\", "/") if lp else None
            v["local_url"] = thumb_url(lp) if lp else None
            images.append(v)
    for entry in load_user_examples(path):
        v = user_image_view(entry)
        lp = local.get(v["index"])
        v["local_file"] = util.get_relative_path(lp, folder).replace("\\", "/") if lp else None
        v["local_url"] = thumb_url(lp) if lp else None
        images.append(v)
    known = {v["index"] for v in images}
    for index, lp in sorted(local.items()):
        if index in known:
            continue
        images.append({
            "index": index, "url": None, "has_prompt": False, "prompt": "", "negative_prompt": "",
            "local_file": util.get_relative_path(lp, folder).replace("\\", "/"),
            "local_url": thumb_url(lp),
        })
    base, _ = os.path.splitext(path)
    card_json = base + ".json"
    return {
        "type": model_type,
        "name": os.path.basename(path),
        "path": util.get_relative_path(path, folder).replace("\\", "/"),
        "has_info": info is not None,
        "model_name": (info or {}).get("model", {}).get("name") if info else None,
        "version_name": (info or {}).get("name") if info else None,
        "base_model": (info or {}).get("baseModel") if info else None,
        "trained_words": (info or {}).get("trainedWords") or [] if info else [],
        "has_preview": any(os.path.isfile(base + s) for s in local_models.PREVIEW_SUFFIXES),
        "has_card_info": os.path.isfile(card_json),
        "downloaded": len(local),
        "total": len(images),
        "user_images": sum(1 for v in images if v.get("source") == "user"),
        "local_images": sum(1 for v in images if v.get("source") == LOCAL_SOURCE),
        "images": images,
    }


# ---------------------------------------------------------------------------
# user-posted images as extra examples (<model>.example_101+ + sidecar json)
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_META_KEYS = ("prompt", "negativePrompt", "steps", "sampler", "cfgScale", "seed", "Size", "clipSkip",
              "Clip skip", "Model", "resources", "civitaiResources", "baseModel")
_LORA_TYPES = ("lora", "locon", "lycoris", "dora")


def user_sidecar_path(model_path: str) -> str:
    base, _ = os.path.splitext(model_path)
    return base + USER_SIDECAR


def load_user_examples(model_path: str) -> list:
    """Entries of `<model>.examples.json`, sorted by index ([] when absent / broken)."""
    p = user_sidecar_path(model_path)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception as e:
        util.printD(f"Can not read {p}: {e}")
        return []
    items = data.get("images") if isinstance(data, dict) else None
    return sorted((e for e in items or [] if isinstance(e, dict) and e.get("index")), key=lambda e: e["index"])


def _save_user_examples(model_path: str, entries: list):
    p = user_sidecar_path(model_path)
    if not entries:
        if os.path.isfile(p):
            os.remove(p)
        return
    tmp = p + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "images": sorted(entries, key=lambda e: e["index"])}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def image_key(url: str) -> str:
    """Identity of a civitai image across API shapes: the UUID in its URL path."""
    m = _UUID_RE.search(url or "")
    return m.group(0).lower() if m else (url or "")


def _nsfw(level, flag) -> bool:
    if isinstance(level, int):
        return level > 1
    if isinstance(level, str) and level:
        return level != "None"
    return bool(flag)


def used_loras(meta: dict) -> list:
    """'name:weight' of the LoRAs an image was generated with (best effort from its meta)."""
    out = []
    for r in meta.get("resources") or []:
        if isinstance(r, dict) and str(r.get("type") or "").lower() in _LORA_TYPES and r.get("name"):
            out.append(f"{r['name']}:{r.get('weight', 1)}")
    for r in meta.get("civitaiResources") or []:
        if isinstance(r, dict) and str(r.get("type") or "").lower() in _LORA_TYPES:
            name = r.get("modelVersionName") or f"version {r.get('modelVersionId')}"
            out.append(f"{name} (v{r.get('modelVersionId')}):{r.get('weight', 1)}")
    return list(dict.fromkeys(out))


def user_image_view(entry: dict) -> dict:
    """Same shape as image_view(), plus who posted it and where (or, for a local image,
    its original file name)."""
    meta = entry.get("meta") or {}
    v = image_view(entry["index"], {
        "url": entry.get("url"), "type": "image", "width": entry.get("width"), "height": entry.get("height"),
        "nsfwLevel": entry.get("nsfw_level"), "meta": meta,
    })
    v["nsfw"] = bool(entry.get("nsfw"))
    if entry.get("source") == LOCAL_SOURCE:
        v.update({
            "source": LOCAL_SOURCE,
            "file_name": entry.get("file_name"),
            "loras": used_loras(meta),
            "added_at": entry.get("added_at"),
        })
        return v
    v.update({
        "source": "user",
        "image_id": entry.get("id"),
        "username": entry.get("username"),
        "post_id": entry.get("post_id"),
        "page_url": (civitai.url_dict["imagePage"] + str(entry["id"])) if entry.get("id") else None,
        "loras": used_loras(meta),
        "added_at": entry.get("added_at"),
    })
    return v


def add_user_examples(path: str, images: list, skip_nsfw: bool = False, overwrite: bool = False,
                      max_size: bool = True) -> dict:
    """Save civitai image records (GET /api/v1/images items, with meta) as
    extra examples of the model at `path`. Already-added images keep their
    number; images that are part of the model's own civitai example list are
    skipped (they are examples #1-#100 already)."""
    r = {"path": path, "status": "ok", "added": 0, "existed": 0, "skipped_nsfw": 0, "skipped_video": 0,
         "in_civitai_list": 0, "failed": 0, "images": []}
    entries = load_user_examples(path)
    others = [e for e in entries if e.get("id") is None]   # local images: no civitai id, kept as they are
    by_id = {e.get("id"): e for e in entries if e.get("id") is not None}
    info = load_info(path)
    info_keys = {image_key(img.get("url")): i for i, img in info_images(info)} if info else {}
    used = {e["index"] for e in entries} | {i for i in (example_index_of(p) for p in example_files(path)) if i}
    next_index = max([USER_BASE - 1] + [i for i in used if i >= USER_BASE]) + 1

    for img in images:
        if not isinstance(img, dict) or not img.get("url"):
            continue
        iid = img.get("id")
        row = {"image_id": iid, "username": img.get("username")}
        if img.get("type") not in (None, "image"):
            r["skipped_video"] += 1
            continue
        key = image_key(img["url"])
        if key in info_keys:
            r["in_civitai_list"] += 1
            r["images"].append({**row, "index": info_keys[key], "status": "in_civitai_list"})
            continue
        nsfw = _nsfw(img.get("nsfwLevel"), img.get("nsfw"))
        if skip_nsfw and nsfw:
            r["skipped_nsfw"] += 1
            continue
        entry = by_id.get(iid)
        is_new = entry is None
        if is_new:
            if next_index > USER_MAX:
                r["status"] = "full"
                break
            entry = {"index": next_index}
        meta = img.get("meta") or {}
        entry.update({
            "id": iid, "url": img["url"], "post_id": img.get("postId"), "username": img.get("username"),
            "width": img.get("width"), "height": img.get("height"), "nsfw": nsfw,
            "nsfw_level": img.get("nsfwLevel"), "base_model": img.get("baseModel"),
            "created_at": img.get("createdAt"),
            "meta": {k: meta[k] for k in _META_KEYS if k in meta},
        })
        entry.setdefault("added_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        url = civitai.get_example_image_url(img, max_size)
        existing = [p for p in example_files(path) if example_index_of(p) == entry["index"]]
        if existing and not overwrite:
            r["existed"] += 1
            status = "existed"
        else:
            for p in existing:
                os.remove(p)
            target = example_file_path(path, entry["index"], url)
            if not util.download_file(url, target):
                r["failed"] += 1
                r["images"].append({**row, "status": "failed"})
                continue
            r["added"] += 1
            status = "added"
        if is_new:
            next_index += 1
        by_id[iid] = entry
        r["images"].append({**row, "index": entry["index"], "status": status})
    _save_user_examples(path, others + list(by_id.values()))
    if r["failed"] and not (r["added"] or r["existed"]) and r["status"] == "ok":
        r["status"] = "failed"
    r["total_user_examples"] = len(by_id)
    return r


def remove_user_examples(path: str, image_ids=None, indexes=None, remove_all: bool = False,
                         source: str = None) -> dict:
    """Delete added example images (files + sidecar entries) by civitai image id or example
    number; remove_all takes every added one, or only those of `source` ("user" = from civitai
    users, "local" = local images)."""
    entries = load_user_examples(path)
    ids = {int(i) for i in image_ids or []}
    idx = {int(i) for i in indexes or []}
    keep, removed = [], []
    for e in entries:
        kind = e.get("source") or "user"
        if (remove_all and source in (None, "", kind)) or (e.get("id") is not None and e.get("id") in ids) \
                or e["index"] in idx:
            for p in example_files(path):
                if example_index_of(p) == e["index"]:
                    os.remove(p)
            removed.append({"index": e["index"], "image_id": e.get("id"), "source": kind})
        else:
            keep.append(e)
    _save_user_examples(path, keep)
    return {"path": path, "removed": removed, "remaining": len(keep)}


# ---------------------------------------------------------------------------
# locally generated images as extra examples (same numbering and sidecar)
# ---------------------------------------------------------------------------

_LORA_IN_PROMPT_RE = re.compile(r"<(?:lora|lyco):([^:>]+)(?::([^:>]*))?[^>]*>", re.IGNORECASE)
# key: value pairs of the WebUI's parameters line (same pattern as modules/infotext_utils.py)
_PARAM_RE = re.compile(r'\s*(\w[\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')
_FORMAT_EXT = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp", "GIF": "gif"}


def _parse_infotext(text: str) -> dict:
    """WebUI generation info text -> {"Prompt", "Negative prompt", "Steps", ...}. Uses the WebUI's own
    parser when it is there (inside Forge), else a small copy of it."""
    try:
        from modules import infotext_utils
        return infotext_utils.parse_generation_parameters(text)
    except Exception:
        pass
    lines = text.strip().split("\n")
    last = lines[-1] if len(_PARAM_RE.findall(lines[-1])) >= 3 else ""
    if last:
        lines = lines[:-1]
    prompt, negative, in_negative = [], [], False
    for line in lines:
        if line.startswith("Negative prompt:"):
            in_negative, line = True, line[len("Negative prompt:"):]
        (negative if in_negative else prompt).append(line.strip())
    out = {"Prompt": "\n".join(prompt).strip(), "Negative prompt": "\n".join(negative).strip()}
    for k, v in _PARAM_RE.findall(last):
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k.strip()] = v
    size = re.match(r"^(\d+)x(\d+)$", str(out.get("Size") or ""))
    if size:
        out["Size-1"], out["Size-2"] = size.group(1), size.group(2)
    return out


def read_image_info(data: bytes) -> dict:
    """Generation info of an image file: {"format", "width", "height", "infotext", "meta"}; meta uses
    the civitai keys (prompt, negativePrompt, steps, sampler, cfgScale, seed, Size, clipSkip, Model,
    resources = the LoRAs of the prompt). Raises ValueError when it is not an image."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("not a readable image file")
    text = None
    try:
        from modules import images as webui_images
        text, _ = webui_images.read_info_from_image(img)
    except Exception:
        text = img.info.get("parameters")
    text = text if isinstance(text, str) else None
    params = _parse_infotext(text) if text else {}
    meta = {}
    for key, src in (("prompt", "Prompt"), ("negativePrompt", "Negative prompt"), ("sampler", "Sampler"),
                     ("Model", "Model")):
        if params.get(src):
            meta[key] = str(params[src])
    for key, src in (("steps", "Steps"), ("cfgScale", "CFG scale"), ("seed", "Seed"), ("clipSkip", "Clip skip")):
        if params.get(src) not in (None, ""):
            meta[key] = _num(params[src])
    if params.get("Size-1") and params.get("Size-2"):
        meta["Size"] = f"{params['Size-1']}x{params['Size-2']}"
    loras = [{"type": "lora", "name": m.group(1).strip(), "weight": _num(m.group(2)) if m.group(2) else 1}
             for m in _LORA_IN_PROMPT_RE.finditer(meta.get("prompt") or "")]
    if loras:
        meta["resources"] = loras
    return {"format": _FORMAT_EXT.get(img.format or "", ""), "width": img.width, "height": img.height,
            "infotext": text or "", "meta": meta}


def models_in_image(model_type: str, meta: dict) -> list:
    """Local models an image was made with, as paths relative to the model folder: the LoRAs of
    its prompt (<lora:name:w>, matched by file name without extension) or its checkpoint (Model)."""
    if model_type == "lora":
        wanted = [r["name"] for r in meta.get("resources") or [] if r.get("type") == "lora"]
    elif model_type == "ckp":
        wanted = [meta["Model"]] if meta.get("Model") else []
    else:
        return []
    keys = {w.strip().casefold(): w for w in wanted if w and w.strip()}
    found = {}
    for folder, _root, path, filename in local_models.iter_models(model_type):
        stem = os.path.splitext(filename)[0].casefold()
        if stem in keys and stem not in found:
            found[stem] = util.get_relative_path(path, folder).replace("\\", "/")
    return [found[k] for k in keys if k in found]


def _apply_overrides(meta: dict, overrides: dict) -> dict:
    """Values the caller gave (prompt, negative_prompt, steps, ...) win over the image's own info."""
    meta = dict(meta)
    for key, src in (("prompt", "prompt"), ("negativePrompt", "negative_prompt"), ("sampler", "sampler"),
                     ("Model", "model")):
        if (overrides.get(src) or "").strip():
            meta[key] = overrides[src].strip()
    for key, src in (("steps", "steps"), ("cfgScale", "cfg_scale"), ("seed", "seed"), ("clipSkip", "clip_skip")):
        if overrides.get(src) is not None:
            meta[key] = _num(overrides[src])
    if overrides.get("prompt"):
        loras = [{"type": "lora", "name": m.group(1).strip(), "weight": _num(m.group(2)) if m.group(2) else 1}
                 for m in _LORA_IN_PROMPT_RE.finditer(meta["prompt"])]
        meta.pop("resources", None)
        if loras:
            meta["resources"] = loras
    return meta


def effective_meta(meta: dict, img: dict) -> dict:
    """The image's own info, with the caller's values for this image on top (img["prompt"] ...)
    and img["defaults"] (values given for several images at once) only where it has none."""
    meta = _apply_overrides(meta, img)
    for key, value in _apply_overrides({}, img.get("defaults") or {}).items():
        meta.setdefault(key, value)
    return meta


def add_local_examples(path: str, images: list, overwrite: bool = False) -> dict:
    """Save locally generated images as extra examples of the model at `path`.

    images: [{"data": bytes, "file_name": str, "nsfw": bool, and optional overrides prompt /
    negative_prompt / steps / sampler / cfg_scale / seed / clip_skip / model, plus "defaults" with
    the same keys that only fill what the image's own info lacks}]. The file is kept
    byte for byte (its generation info stays inside); the prompt and parameters read from it go
    into the sidecar like those of civitai user images. The same picture added again keeps its
    number (overwrite=True refreshes its prompt / flags)."""
    r = {"path": path, "status": "ok", "added": 0, "existed": 0, "failed": 0, "images": []}
    entries = load_user_examples(path)
    by_hash = {e.get("sha256"): e for e in entries if e.get("source") == LOCAL_SOURCE and e.get("sha256")}
    used = {e["index"] for e in entries} | {i for i in (example_index_of(p) for p in example_files(path)) if i}
    next_index = max([USER_BASE - 1] + [i for i in used if i >= USER_BASE]) + 1

    for img in images:
        name = os.path.basename(str(img.get("file_name") or "")) or None
        row = {"file_name": name}
        data = img.get("data") or b""
        if len(data) > LOCAL_MAX_BYTES:
            r["failed"] += 1
            r["images"].append({**row, "status": "failed", "error": f"larger than {LOCAL_MAX_BYTES // 2**20} MB"})
            continue
        try:
            got = read_image_info(data)
        except ValueError as e:
            r["failed"] += 1
            r["images"].append({**row, "status": "failed", "error": str(e)})
            continue
        if not got["format"]:
            r["failed"] += 1
            r["images"].append({**row, "status": "failed", "error": "only PNG, JPEG, WebP or GIF"})
            continue
        sha = hashlib.sha256(data).hexdigest()
        entry = by_hash.get(sha)
        meta = effective_meta(got["meta"], img)
        on_disk = entry is not None and any(example_index_of(p) == entry["index"] for p in example_files(path))
        if entry is not None and on_disk and not overwrite:
            r["existed"] += 1
            r["images"].append({**row, "index": entry["index"], "status": "existed"})
            continue
        is_new = entry is None
        if is_new:
            if next_index > USER_MAX:
                r["status"] = "full"
                break
            entry = {"index": next_index, "source": LOCAL_SOURCE, "sha256": sha,
                     "added_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        entry.update({
            "file_name": name or entry.get("file_name"), "width": got["width"], "height": got["height"],
            "nsfw": bool(img.get("nsfw")), "meta": meta, "infotext": got["infotext"][:8000],
        })
        if not on_disk or overwrite:
            for p in example_files(path):
                if example_index_of(p) == entry["index"]:
                    os.remove(p)
            target = f"{os.path.splitext(path)[0]}{EXAMPLE_TAG}{entry['index']:02d}.{got['format']}"
            tmp = target + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        if is_new:
            next_index += 1
            r["added"] += 1
            status = "added"
        else:
            r["existed"] += 1
            status = "updated" if overwrite else "restored"
        by_hash[sha] = entry
        r["images"].append({**row, "index": entry["index"], "status": status,
                            "has_prompt": bool(meta.get("prompt")), "loras": used_loras(meta)})
    keep = [e for e in entries if not (e.get("source") == LOCAL_SOURCE and e.get("sha256") in by_hash)]
    _save_user_examples(path, keep + list(by_hash.values()))
    if r["failed"] and not (r["added"] or r["existed"]) and r["status"] == "ok":
        r["status"] = "failed"
    r["total_local_examples"] = len(by_hash)
    return r


# ---------------------------------------------------------------------------
# the model's name inside saved prompts (after a rename) / moving added examples
# ---------------------------------------------------------------------------

_NAME_TAGS = {"lora": ("lora", "lyco"), "hyper": ("hypernet",)}
_CARD_TEXT_FIELDS = ("description", "notes", "activation text")


def rename_in_prompt(text: str, old: str, new: str, model_type: str = "lora") -> str:
    """'<lora:old:0.8>' -> '<lora:new:0.8>' (also <lyco:…>; <hypernet:…> for hypernetworks)."""
    tags = _NAME_TAGS.get(model_type)
    if not tags or not text or old == new:
        return text
    pattern = re.compile(r"<(%s):%s(?=[:>])" % ("|".join(tags), re.escape(old)), re.IGNORECASE)
    return pattern.sub(lambda m: f"<{m.group(1)}:{new}", text)


def rename_in_texts(path: str, old: str, new: str, model_type: str = "lora") -> int:
    """After renaming a model: its own name in the prompts of its added examples (sidecar) and in
    the card file's description / notes / activation text. Returns how many texts changed. The
    image files keep their embedded generation info as it was."""
    changed = 0
    entries = load_user_examples(path)
    for e in entries:
        meta = e.get("meta") or {}
        fixed = rename_in_prompt(meta.get("prompt") or "", old, new, model_type)
        if fixed != (meta.get("prompt") or ""):
            meta["prompt"] = fixed
            if model_type == "lora":
                meta.pop("resources", None)
                loras = [{"type": "lora", "name": m.group(1).strip(), "weight": _num(m.group(2)) if m.group(2) else 1}
                         for m in _LORA_IN_PROMPT_RE.finditer(fixed)]
                if loras:
                    meta["resources"] = loras
            e["meta"] = meta
            changed += 1
    if changed:
        _save_user_examples(path, entries)
    card = os.path.splitext(path)[0] + ".json"
    if os.path.isfile(card):
        try:
            with open(card, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
        if isinstance(data, dict):
            touched = False
            for key in _CARD_TEXT_FIELDS:
                if isinstance(data.get(key), str):
                    fixed = rename_in_prompt(data[key], old, new, model_type)
                    if fixed != data[key]:
                        data[key] = fixed
                        touched = True
                        changed += 1
            if touched:
                with open(card, "w", encoding="utf-8") as f:
                    f.write(json.dumps(data, indent=4, ensure_ascii=False))
    return changed


def move_added_examples(src: str, dst: str, model_type: str = "lora") -> int:
    """Copy the added examples (#101+) of the model at `src` to the model at `dst`, numbered after
    dst's own; images dst already has (same local file / same civitai image) are skipped. Prompts
    that named src now name dst. The src files are left for the caller to delete."""
    src_stem, dst_stem = os.path.splitext(os.path.basename(src))[0], os.path.splitext(os.path.basename(dst))[0]
    dst_entries = load_user_examples(dst)
    have_ids = {e.get("id") for e in dst_entries if e.get("id") is not None}
    have_hashes = {e.get("sha256") for e in dst_entries if e.get("sha256")}
    used = {e["index"] for e in dst_entries} | {i for i in (example_index_of(p) for p in example_files(dst)) if i}
    next_index = max([USER_BASE - 1] + [i for i in used if i >= USER_BASE]) + 1
    src_files = {example_index_of(p): p for p in example_files(src)}
    moved = 0
    for e in load_user_examples(src):
        if (e.get("id") is not None and e["id"] in have_ids) or (e.get("sha256") and e["sha256"] in have_hashes):
            continue
        f = src_files.get(e["index"])
        if not f or next_index > USER_MAX:
            continue
        ext = os.path.splitext(f)[1]
        shutil.copy2(f, f"{os.path.splitext(dst)[0]}{EXAMPLE_TAG}{next_index:02d}{ext}")
        meta = dict(e.get("meta") or {})
        if meta.get("prompt"):
            meta["prompt"] = rename_in_prompt(meta["prompt"], src_stem, dst_stem, model_type)
        dst_entries.append({**e, "index": next_index, "meta": meta})
        next_index += 1
        moved += 1
    if moved:
        _save_user_examples(dst, dst_entries)
    return moved


# ---------------------------------------------------------------------------
# feature 1: (re)download missing previews
# ---------------------------------------------------------------------------

def fetch_preview(path: str, max_size_preview: bool, skip_nsfw_preview: bool) -> str:
    return civitai.get_preview_image_by_model_path(path, max_size_preview, skip_nsfw_preview)


# ---------------------------------------------------------------------------
# feature 2: example images to disk
# ---------------------------------------------------------------------------

def download_examples(path: str, max_images: int = 0, skip_nsfw: bool = False,
                      overwrite: bool = False, max_size: bool = True) -> dict:
    """Save the example images of one model next to it.

    max_images: how many to keep (0 = all). Counting follows civitai's order,
    so `<model>.example_03.jpeg` is always the third image of the info file.
    """
    r = {"path": path, "status": "ok", "downloaded": 0, "existed": 0,
         "skipped_nsfw": 0, "failed": 0, "files": []}
    info = load_info(path)
    if info is None:
        r["status"] = "no_info"
        return r
    images = info_images(info)
    if not images:
        r["status"] = "no_images"
        return r

    kept = 0
    for index, img in images:
        if max_images and kept >= max_images:
            break
        if skip_nsfw and civitai.is_nsfw_image(img):
            r["skipped_nsfw"] += 1
            continue
        url = civitai.get_example_image_url(img, max_size)
        target = example_file_path(path, index, url)
        if os.path.isfile(target) and not overwrite:
            r["existed"] += 1
            kept += 1
            r["files"].append(target)
            continue
        if util.download_file(url, target):
            r["downloaded"] += 1
            kept += 1
            r["files"].append(target)
        else:
            r["failed"] += 1
    if r["failed"] and not (r["downloaded"] or r["existed"]):
        r["status"] = "failed"
    return r


def model_files(model_path: str) -> list:
    """Every file that belongs to one model: the model itself, its civitai info file,
    previews, the old-style info file, the WebUI card metadata (`<model>.json`),
    downloaded / user-added example images and the user-examples sidecar.
    Only files that exist; used by the card's delete button and the API."""
    base, _ = os.path.splitext(model_path)
    paths = [
        model_path,
        base + civitai.suffix + model.info_ext,
        base + ".png",
        base + ".preview.png",
        base + model.info_ext,
        base + ".json",
        *example_files(model_path),
        user_sidecar_path(model_path),
    ]
    return [q for q in dict.fromkeys(paths) if os.path.isfile(q)]


def remove_examples(path: str) -> int:
    n = 0
    sidecar = user_sidecar_path(path)
    for p in example_files(path) + ([sidecar] if os.path.isfile(sidecar) else []):
        try:
            os.remove(p)
            n += 1
        except OSError as e:
            util.printD(f"Can not remove {p}: {e}")
    return n


# ---------------------------------------------------------------------------
# feature 3: example prompt on the Extra Networks card
# ---------------------------------------------------------------------------

def _one_line(text, limit: int) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def _first_prompt_image(info: dict):
    for index, img in info_images(info):
        if (img.get("meta") or {}).get("prompt"):
            return index, img
    return None, None


def _params_line(v: dict) -> str:
    parts = []
    if v.get("steps") is not None:
        parts.append(f"Steps {v['steps']}")
    if v.get("sampler"):
        parts.append(str(v["sampler"]))
    if v.get("cfg_scale") is not None:
        parts.append(f"CFG {v['cfg_scale']}")
    if v.get("seed") is not None:
        parts.append(f"Seed {v['seed']}")
    if v.get("size"):
        parts.append(str(v["size"]))
    if v.get("clip_skip") is not None:
        parts.append(f"Clip skip {v['clip_skip']}")
    if v.get("model"):
        parts.append(f"Model {v['model']}")
    return " | ".join(parts)


def build_description(info: dict) -> str:
    """Short text for the card face (3 lines collapsed, full on hover)."""
    lines = []
    words = info.get("trainedWords") or []
    if words:
        lines.append("Trigger: " + _one_line(", ".join(w.strip().strip(",") for w in words if w), DESC_PROMPT_LIMIT))
    index, img = _first_prompt_image(info)
    if img:
        v = image_view(index, img)
        lines.append(f"Example #{index}: " + _one_line(v["prompt"], DESC_PROMPT_LIMIT))
        if v["negative_prompt"]:
            lines.append("Negative: " + _one_line(v["negative_prompt"], DESC_NEG_LIMIT))
        params = _params_line(v)
        if params:
            lines.append(params)
    return "\n".join(lines)


def build_notes(info: dict, path: str = None) -> str:
    """Every example prompt in full (civitai list, then user images), for the card's edit dialog."""
    m = info.get("model") or {}
    head = []
    if m.get("name"):
        head.append(f"{m['name']} / {info.get('name') or ''}".rstrip(" /"))
    if info.get("baseModel"):
        head.append(f"Base model: {info['baseModel']}")
    if info.get("modelId"):
        head.append("Civitai: " + civitai.url_dict["modelPage"] + str(info["modelId"]))
    words = info.get("trainedWords") or []
    if words:
        head.append("Trigger words: " + ", ".join(w.strip().strip(",") for w in words if w))
    blocks = ["\n".join(head)] if head else []
    for index, img in info_images(info):
        v = image_view(index, img)
        if not v["prompt"]:
            continue
        b = [f"--- Example #{index} ---", "Prompt: " + v["prompt"].strip()]
        if v["negative_prompt"]:
            b.append("Negative prompt: " + v["negative_prompt"].strip())
        params = _params_line(v)
        if params:
            b.append(params)
        blocks.append("\n".join(b))
    for entry in (load_user_examples(path) if path else []):
        v = user_image_view(entry)
        if not v["prompt"]:
            continue
        who = "local image" if v.get("source") == LOCAL_SOURCE else f"by {v.get('username') or '?'} on civitai"
        b = [f"--- Example #{v['index']} ({who}) ---", "Prompt: " + v["prompt"].strip()]
        if v["negative_prompt"]:
            b.append("Negative prompt: " + v["negative_prompt"].strip())
        params = _params_line(v)
        if params:
            b.append(params)
        blocks.append("\n".join(b))
    text = "\n\n".join(blocks)
    return text if len(text) <= NOTES_LIMIT else text[:NOTES_LIMIT].rstrip() + "\n…"


def write_card_info(path: str, overwrite: bool = False, set_sd_version: bool = True) -> dict:
    """Merge description / notes (/ sd version) into `<model>.json`.

    overwrite=False fills only empty fields, so hand-written notes survive.
    Other user fields (activation text, preferred weight, ...) are never touched.
    """
    base, _ = os.path.splitext(path)
    card_json = base + ".json"
    r = {"path": path, "file": card_json, "status": "unchanged", "fields": []}
    info = load_info(path)
    if info is None:
        r["status"] = "no_info"
        return r

    data = {}
    if os.path.isfile(card_json):
        try:
            with open(card_json, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception as e:
            util.printD(f"Card metadata file is not valid json, skipped: {card_json} ({e})")
            r["status"] = "bad_json"
            return r
        if not isinstance(data, dict):
            r["status"] = "bad_json"
            return r

    def put(key, value):
        if not value:
            return
        current = data.get(key)
        if current and not overwrite:
            return
        if current == value:
            return
        data[key] = value
        r["fields"].append(key)

    put("description", build_description(info))
    put("notes", build_notes(info, path))
    if set_sd_version:
        sd_version = sd_version_from_base_model(info.get("baseModel"))
        if sd_version and (data.get("sd version") in (None, "", "Unknown") or overwrite):
            if data.get("sd version") != sd_version:
                data["sd version"] = sd_version
                r["fields"].append("sd version")

    if not r["fields"]:
        return r
    with open(card_json, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=4, ensure_ascii=False))
    r["status"] = "written"
    return r


def remove_card_info(path: str) -> bool:
    base, _ = os.path.splitext(path)
    card_json = base + ".json"
    if os.path.isfile(card_json):
        os.remove(card_json)
        return True
    return False


# ---------------------------------------------------------------------------
# batch runners (shared by Gradio buttons and HTTP API)
# ---------------------------------------------------------------------------

def _targets(model_types=None, single=None):
    """Yield (model_type, folder, path). single = (model_type, path)."""
    if single:
        model_type, path = single
        yield model_type, model.folders[model_type], path
        return
    for model_type in model_types or []:
        for folder, _root, path, _filename in local_models.iter_models(model_type):
            yield model_type, folder, path


def run_fetch_previews(model_types=None, single=None, max_size_preview=True, skip_nsfw_preview=False) -> dict:
    counts = {"models": 0, "exists": 0, "downloaded": 0, "failed": 0, "no_info": 0, "empty_info": 0, "no_images": 0}
    failed = []
    started = time.time()
    for model_type, folder, path in _targets(model_types, single):
        counts["models"] += 1
        status = fetch_preview(path, max_size_preview, skip_nsfw_preview)
        counts[status] = counts.get(status, 0) + 1
        if status == "failed":
            failed.append(util.get_relative_path(path, folder).replace("\\", "/"))
    counts["failed_models"] = failed
    counts["elapsed"] = round(time.time() - started, 1)
    counts["message"] = (
        f"Done. {counts['models']} models: {counts['downloaded']} previews downloaded, "
        f"{counts['exists']} already had one, {counts['failed']} failed, "
        f"{counts['no_info'] + counts['empty_info']} without civitai info, {counts['no_images']} without images"
    )
    util.printD(counts["message"])
    return counts


def run_download_examples(model_types=None, single=None, max_images: int = 0, skip_nsfw: bool = False,
                          overwrite: bool = False, max_size: bool = True) -> dict:
    counts = {"models": 0, "with_info": 0, "downloaded": 0, "existed": 0, "skipped_nsfw": 0, "failed": 0}
    failed = []
    started = time.time()
    for model_type, folder, path in _targets(model_types, single):
        counts["models"] += 1
        r = download_examples(path, max_images, skip_nsfw, overwrite, max_size)
        if r["status"] == "no_info":
            continue
        counts["with_info"] += 1
        for k in ("downloaded", "existed", "skipped_nsfw", "failed"):
            counts[k] += r[k]
        if r["failed"]:
            failed.append(util.get_relative_path(path, folder).replace("\\", "/"))
    counts["models_with_failures"] = failed
    counts["elapsed"] = round(time.time() - started, 1)
    counts["message"] = (
        f"Done. {counts['models']} models ({counts['with_info']} with civitai info): "
        f"{counts['downloaded']} example images downloaded, {counts['existed']} already on disk, "
        f"{counts['skipped_nsfw']} NSFW skipped, {counts['failed']} failed"
    )
    util.printD(counts["message"])
    return counts


def run_write_card_info(model_types=None, single=None, overwrite: bool = False, set_sd_version: bool = True) -> dict:
    counts = {"models": 0, "written": 0, "unchanged": 0, "no_info": 0, "bad_json": 0}
    bad = []
    started = time.time()
    for model_type, folder, path in _targets(model_types, single):
        counts["models"] += 1
        r = write_card_info(path, overwrite, set_sd_version)
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] == "bad_json":
            bad.append(util.get_relative_path(path, folder).replace("\\", "/"))
    counts["bad_json_models"] = bad
    counts["elapsed"] = round(time.time() - started, 1)
    counts["message"] = (
        f"Done. {counts['models']} models: {counts['written']} card files written, "
        f"{counts['unchanged']} unchanged, {counts['no_info']} without civitai info, "
        f"{counts['bad_json']} skipped (existing .json is not valid). "
        "Click Refresh in the Extra Networks tab to see the new descriptions."
    )
    util.printD(counts["message"])
    return counts
