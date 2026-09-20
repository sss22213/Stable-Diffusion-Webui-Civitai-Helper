# -*- coding: UTF-8 -*-
"""Inventory of the models this WebUI has on disk (API-only helper).

Walks a model folder (ti / hyper / ckp / lora), merges what Civitai Helper
knows about each file (the .civitai.info record: model name, version, base
model, trigger words, ...) and, optionally, the training metadata stored in
the safetensors header (kohya `ss_*` / modelspec keys: base model version,
top training tags, network dim ...). The result can be rendered as JSON, CSV
or a Markdown table.

Used by the HTTP API: GET /civitai-helper/v1/loras and /models.
"""
import csv
import io
import json
import os
import re
import struct
import time

from . import util
from . import model
from . import civitai


def _examples():
    from . import examples   # examples imports this module; import lazily
    return examples


PREVIEW_SUFFIXES = (".preview.png", ".png", ".preview.jpg", ".jpg", ".preview.webp", ".webp")
MAX_SAFETENSORS_HEADER = 100 * 1024 * 1024   # refuse absurd headers
TOP_TAGS = 15                                # training tags kept per model
_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------

def iter_models(model_type: str):
    """Yield (root_folder, dir, full_path, filename) for every model file of a type."""
    folder = model.folders[model_type]
    if not os.path.isdir(folder):
        return
    for root, dirs, files in os.walk(folder, followlinks=True):
        dirs.sort()
        for filename in sorted(files):
            base, ext = os.path.splitext(filename)
            if ext not in model.exts:
                continue
            if base.endswith(model.vae_suffix):
                continue
            yield folder, root, os.path.join(root, filename), filename


def find(model_type: str, name: str):
    """Find one model by file name or by path relative to the model folder.
    Returns (folder, dir, path, filename) or None."""
    wanted = (name or "").replace("\\", "/").strip().lstrip("/")
    if not wanted:
        return None
    for folder, root, path, filename in iter_models(model_type):
        rel = util.get_relative_path(path, folder).replace("\\", "/")
        if filename == wanted or rel == wanted:
            return folder, root, path, filename
    return None


# ---------------------------------------------------------------------------
# Civitai info (.civitai.info = a civitai *version* record)
# ---------------------------------------------------------------------------

def _strip_html(text, limit=300) -> str:
    if not text:
        return ""
    t = re.sub(r"\s+", " ", _TAG_RE.sub(" ", str(text))).strip()
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def info_summary(info):
    """Trimmed view of a .civitai.info file; None when the file has no civitai id."""
    if not info or not info.get("id"):
        return None
    m = info.get("model") or {}
    model_id = info.get("modelId")
    sha256 = None
    for f in info.get("files") or []:
        h = (f.get("hashes") or {}).get("SHA256")
        if h:
            sha256 = h
            break
    return {
        "model_id": model_id,
        "version_id": info.get("id"),
        "model_name": m.get("name"),
        "version_name": info.get("name"),
        "type": m.get("type"),
        "nsfw": m.get("nsfw"),
        "base_model": info.get("baseModel"),
        "trained_words": info.get("trainedWords") or [],
        "sha256": sha256,
        "description": _strip_html(info.get("description")),
        "model_url": (civitai.url_dict["modelPage"] + str(model_id)) if model_id else None,
    }


# ---------------------------------------------------------------------------
# safetensors training metadata
# ---------------------------------------------------------------------------

def read_safetensors_metadata(path: str) -> dict:
    """Return the `__metadata__` dict of a .safetensors file ({} if absent/unreadable)."""
    if not path.lower().endswith(".safetensors"):
        return {}
    try:
        with open(path, "rb") as f:
            raw = f.read(8)
            if len(raw) < 8:
                return {}
            n = struct.unpack("<Q", raw)[0]
            if n <= 0 or n > MAX_SAFETENSORS_HEADER:
                return {}
            header = json.loads(f.read(n).decode("utf-8", errors="ignore"))
        meta = header.get("__metadata__") if isinstance(header, dict) else None
        return meta if isinstance(meta, dict) else {}
    except Exception as e:
        util.printD(f"Can not read safetensors header of {path}: {e}")
        return {}


def _top_tags(tag_frequency, limit=TOP_TAGS) -> list:
    """ss_tag_frequency is a JSON string {dataset: {tag: count}}; merge and rank."""
    if not tag_frequency:
        return []
    try:
        data = json.loads(tag_frequency) if isinstance(tag_frequency, str) else tag_frequency
    except Exception:
        return []
    counts = {}
    if isinstance(data, dict):
        for _dataset, tags in data.items():
            if not isinstance(tags, dict):
                continue
            for tag, n in tags.items():
                try:
                    key = str(tag).strip()
                    counts[key] = counts.get(key, 0) + int(n)
                except (TypeError, ValueError):
                    continue
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in ranked[:limit] if t]


def training_summary(meta: dict):
    """The useful bits of kohya / modelspec metadata; None when there is none."""
    if not meta:
        return None
    out = {
        "title": meta.get("modelspec.title"),
        "architecture": meta.get("modelspec.architecture"),
        "base_model_version": meta.get("ss_base_model_version"),
        "sd_model_name": meta.get("ss_sd_model_name"),
        "output_name": meta.get("ss_output_name"),
        "network_dim": meta.get("ss_network_dim"),
        "network_alpha": meta.get("ss_network_alpha"),
        "resolution": meta.get("ss_resolution"),
        "trigger_phrase": meta.get("modelspec.trigger_phrase"),
        "training_comment": (meta.get("ss_training_comment") or "")[:200] or None,
        "top_tags": _top_tags(meta.get("ss_tag_frequency")),
    }
    out = {k: v for k, v in out.items() if v not in (None, "", [], "None")}
    return out or None


_meta_cache = {}   # path -> ((size, mtime), training summary)


def training_summary_cached(path: str):
    """training_summary() for a file, cached on (size, mtime) so repeated
    inventory calls do not re-read every safetensors header."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_size, st.st_mtime)
    hit = _meta_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    summary = training_summary(read_safetensors_metadata(path))
    _meta_cache[path] = (key, summary)
    return summary


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def entry(model_type: str, folder: str, root: str, path: str, filename: str, with_metadata: bool = False) -> dict:
    base, _ = os.path.splitext(path)
    stem = os.path.splitext(filename)[0]
    info_file = base + civitai.suffix + model.info_ext
    has_info = os.path.isfile(info_file)
    info = model.load_model_info(info_file) if has_info else None
    rel_dir = util.get_relative_path(root, folder).replace("\\", "/")
    try:
        st = os.stat(path)
        size_mb = round(st.st_size / 1048576, 1)
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
    except OSError:
        size_mb, mtime = None, None
    e = {
        "name": filename,
        "stem": stem,
        "path": util.get_relative_path(path, folder).replace("\\", "/"),
        "subfolder": ("/" + rel_dir) if rel_dir else "/",
        "size_mb": size_mb,
        "modified": mtime,
        "has_info": has_info,
        "has_preview": any(os.path.isfile(base + s) for s in PREVIEW_SUFFIXES),
        "has_card_info": os.path.isfile(base + ".json"),
        "examples": len(_examples().example_files(path)),
        "civitai": info_summary(info),
        "training": training_summary_cached(path) if with_metadata else None,
    }
    if model_type == "lora":
        e["prompt_tag"] = f"<lora:{stem}:1>"
    elif model_type == "hyper":
        e["prompt_tag"] = f"<hypernet:{stem}:1>"
    elif model_type == "ti":
        e["prompt_tag"] = stem
    return e


def compact_entry(e: dict) -> dict:
    """Short view of an entry (what a chat assistant needs); empty fields dropped."""
    c = e.get("civitai") or {}
    t = e.get("training") or {}
    out = {
        "name": e["name"],
        "prompt_tag": e.get("prompt_tag"),
        "subfolder": e["subfolder"],
        "model_name": c.get("model_name"),
        "version": c.get("version_name"),
        "base_model": c.get("base_model") or t.get("base_model_version"),
        "trained_words": c.get("trained_words") or [],
        "nsfw": c.get("nsfw"),
        "model_url": c.get("model_url"),
        "training_tags": (t.get("top_tags") or [])[:8],
        "has_preview": e["has_preview"],
        "examples": e.get("examples") or 0,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def collect(model_type: str, with_metadata: bool = False, q: str = None,
            no_info_only: bool = False, empty_info_only: bool = False) -> list:
    """All models of one type as entries, optionally filtered."""
    needle = (q or "").lower().strip()
    items = []
    for folder, root, path, filename in iter_models(model_type):
        e = entry(model_type, folder, root, path, filename, with_metadata)
        if no_info_only and e["has_info"]:
            continue
        if empty_info_only and e["civitai"]:
            continue
        if needle:
            c = e["civitai"] or {}
            t = e.get("training") or {}
            hay = " ".join([e["name"], e["path"], c.get("model_name") or "",
                            " ".join(c.get("trained_words") or []),
                            " ".join(t.get("top_tags") or [])]).lower()
            if needle not in hay:
                continue
        items.append(e)
    return items


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "name", "subfolder", "prompt_tag", "size_mb", "modified", "has_info", "has_preview", "has_card_info", "examples",
    "model_name", "version_name", "base_model", "trained_words", "nsfw",
    "model_id", "version_id", "model_url", "sha256",
    "training_base_model", "training_top_tags",
]


def _flat(e: dict) -> dict:
    c = e.get("civitai") or {}
    t = e.get("training") or {}
    return {
        "name": e["name"],
        "subfolder": e["subfolder"],
        "prompt_tag": e.get("prompt_tag", ""),
        "size_mb": e.get("size_mb"),
        "modified": e.get("modified"),
        "has_info": e["has_info"],
        "has_preview": e["has_preview"],
        "has_card_info": e.get("has_card_info", False),
        "examples": e.get("examples") or 0,
        "model_name": c.get("model_name") or "",
        "version_name": c.get("version_name") or "",
        "base_model": c.get("base_model") or t.get("base_model_version") or "",
        "trained_words": " | ".join(c.get("trained_words") or []),
        "nsfw": c.get("nsfw", ""),
        "model_id": c.get("model_id") or "",
        "version_id": c.get("version_id") or "",
        "model_url": c.get("model_url") or "",
        "sha256": c.get("sha256") or "",
        "training_base_model": t.get("base_model_version") or t.get("architecture") or "",
        "training_top_tags": ", ".join(t.get("top_tags") or []),
    }


def to_json(entries: list, model_type: str, total: int = None, offset: int = 0) -> str:
    return json.dumps(
        {
            "type": model_type,
            "folder": model.folders.get(model_type),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(entries) if total is None else total,
            "offset": offset,
            "count": len(entries),
            "items": entries,
        },
        ensure_ascii=False, indent=2,
    )


def to_csv(entries: list) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for e in entries:
        w.writerow(_flat(e))
    return buf.getvalue()


def _md_cell(v) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def to_markdown(entries: list, model_type: str) -> str:
    with_meta = any(e.get("training") for e in entries)
    lines = [
        f"### {model_type}: {len(entries)} models in `{model.folders.get(model_type)}`",
        "",
        "| # | File | Subfolder | Civitai model | Version | Base model | Trigger words | NSFW | Preview | Link |"
        + (" Training tags |" if with_meta else ""),
        "|---|---|---|---|---|---|---|---|---|---|" + ("---|" if with_meta else ""),
    ]
    for i, e in enumerate(entries, 1):
        f = _flat(e)
        link = f"[{f['model_id']}]({f['model_url']})" if f["model_url"] else ""
        row = [
            str(i), f["name"], f["subfolder"], f["model_name"], f["version_name"], f["base_model"],
            f["trained_words"], "yes" if f["nsfw"] is True else ("no" if f["nsfw"] is False else ""),
            "yes" if e["has_preview"] else "no", link,
        ]
        if with_meta:
            row.append(f["training_top_tags"])
        lines.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
    return "\n".join(lines)


FORMATS = {
    "json": ("application/json", "json"),
    "csv": ("text/csv", "csv"),
    "md": ("text/markdown", "md"),
    "markdown": ("text/markdown", "md"),
}


def render(entries: list, fmt: str, model_type: str, total: int = None, offset: int = 0):
    """Return (text, mime, filename) for a format name (json | csv | md | markdown)."""
    fmt = (fmt or "json").lower()
    if fmt not in FORMATS:
        raise ValueError(f"unknown format '{fmt}', expected one of {sorted(FORMATS)}")
    mime, ext = FORMATS[fmt]
    if fmt == "json":
        text = to_json(entries, model_type, total, offset)
    elif fmt == "csv":
        text = to_csv(entries)
    else:
        text = to_markdown(entries, model_type)
    filename = f"{model_type}_models_{time.strftime('%Y%m%d_%H%M%S')}.{ext}"
    return text, mime, filename
