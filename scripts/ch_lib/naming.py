# -*- coding: UTF-8 -*-
"""File names of downloaded models compared with civitai's file names.

Downloading used to save a second copy of an already downloaded version as `name_2.safetensors`,
and a new version whose file has the same name as the old one also became `name_2`. Its info
file, previews and example images then follow `name_2`, while civitai and the LoRA's own alias
still say `name`. This module:

* existing_copies()    local models that already are a given civitai version (a download of it
                       is skipped)
* same_file()          whether a file on disk is a civitai file (SHA256), for a file without info
* pick_download_name() where a download goes: civitai's file name, or when another model already
                       has that name, the name plus the version ("name_v2.0"), never a silent _2
* scan()               models whose name is civitai's name plus a _2 / _3 suffix, and several
                       copies of the same version
* fix()                rename those back and merge the copies into one (added example images
                       move along, prompts are updated)
"""
import json
import os
import re
import shutil

from . import civitai
from . import examples
from . import local_models
from . import model
from . import util

SUFFIX_RE = re.compile(r"^(?P<base>.+)_(?P<n>\d{1,3})$")
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\s]+')


def _rel(path: str, folder: str) -> str:
    return util.get_relative_path(path, folder).replace("\\", "/")


def _stem(name: str) -> str:
    return os.path.splitext(os.path.basename(name))[0]


def _info_file(path: str) -> str:
    return os.path.splitext(path)[0] + civitai.suffix + model.info_ext


def read_info(path: str):
    """The .civitai.info of a model (None when missing / unreadable / not a civitai record)."""
    try:
        with open(_info_file(path), "r", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("id") else None


def model_file_of_version(ver_info: dict, size_bytes: int = None):
    """The model file of a civitai version: the one with this size when given, else the primary one."""
    files = [f for f in (ver_info or {}).get("files") or []
             if isinstance(f, dict) and os.path.splitext(f.get("name") or "")[1].lower() in model.exts]
    if size_bytes is not None:
        for f in files:
            kb = f.get("sizeKB")
            if isinstance(kb, (int, float)) and abs(kb * 1024 - size_bytes) < 4096:
                return f
    return next((f for f in files if f.get("primary")), files[0] if files else None)


def existing_copies(model_type: str, version_id) -> list:
    """(folder, root, path, filename) of every local model whose civitai info is this version."""
    if not version_id:
        return []
    out = []
    for folder, root, path, filename in local_models.iter_models(model_type):
        info = read_info(path)
        if info and str(info.get("id")) == str(version_id):
            out.append((folder, root, path, filename))
    return out


def same_file(path: str, file_info: dict) -> bool:
    """True when `path` exists and its SHA256 is the one civitai lists for this file."""
    want = ((file_info or {}).get("hashes") or {}).get("SHA256")
    if not want or not os.path.isfile(path):
        return False
    try:
        return util.gen_file_sha256(path).lower() == want.lower()
    except OSError:
        return False


def name_taken(model_type: str, folder: str, filename: str, ignore=()) -> bool:
    """A file with this name is in `folder`, or (LoRAs, embeddings, hypernetworks: the WebUI looks
    them up by name alone) another model of the type anywhere has the same name."""
    ignore = {os.path.abspath(p) for p in ignore}
    stem = _stem(filename)
    if os.path.lexists(os.path.join(folder, filename)) and os.path.abspath(os.path.join(folder, filename)) not in ignore:
        return True
    for _folder, root, path, name in local_models.iter_models(model_type):
        if os.path.abspath(path) in ignore or _stem(name) != stem:
            continue
        if model_type != "ckp" or os.path.abspath(root) == os.path.abspath(folder):
            return True
    return False


def pick_download_name(model_type: str, folder: str, ver_info: dict, file_info: dict):
    """(file name, renamed) for downloading this version's file into `folder`: civitai's own name when
    it is free; else the name plus the version name, then plus the version id. None when all are taken
    (the downloader then adds _2 itself)."""
    name = (file_info or {}).get("name") or ""
    if not name:
        return None
    if not name_taken(model_type, folder, name):
        return name, False
    stem, ext = os.path.splitext(name)
    version = _UNSAFE_RE.sub("_", str(ver_info.get("name") or "")).strip("._")
    for alt in (f"{stem}_{version}" if version else "", f"{stem}_v{ver_info.get('id')}"):
        if alt and alt != stem and not name_taken(model_type, folder, alt + ext):
            return alt + ext, True
    return None


# ---------------------------------------------------------------------------
# scan: _2 suffixes and duplicate copies
# ---------------------------------------------------------------------------

def _added_count(path: str) -> int:
    return len(examples.load_user_examples(path))


def _keeper(group: list, civitai_stem: str):
    """The copy to keep: the one named like civitai, then the one with most added examples / a card
    file, then the oldest."""
    def score(m):
        path = m["path"]
        return (m["stem"] == civitai_stem, _added_count(path),
                os.path.isfile(os.path.splitext(path)[0] + ".json"), -os.path.getmtime(path))
    return max(group, key=score)


def scan(model_types) -> dict:
    """Problems with model file names. Each item: {type, name (path relative to the model folder),
    stem, civitai_file, version_id, model_name, action, target, reason}. action:
    - rename:   the name is civitai's name plus a _N suffix and civitai's name is free -> rename to target
    - merge:    another copy of the same version (same file size) is kept (target) -> move this one's
                added examples there and delete this copy
    - conflict: needs a decision (civitai's name is used by another model, or the copies differ)"""
    items = []
    for model_type in model_types:
        models = []
        for folder, root, path, filename in local_models.iter_models(model_type):
            info = read_info(path)
            if not info:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            f = model_file_of_version(info, size)
            models.append({"type": model_type, "folder": folder, "root": root, "path": path,
                           "name": _rel(path, folder), "stem": _stem(filename), "ext": os.path.splitext(filename)[1],
                           "size": size, "info": info, "civitai_file": (f or {}).get("name") or ""})

        by_version = {}
        for m in models:
            by_version.setdefault(str(m["info"]["id"]), []).append(m)
        leaving = set()   # copies that will be merged away
        for group in by_version.values():
            if len(group) < 2:
                continue
            civitai_stem = _stem(group[0]["civitai_file"])
            same_size = len({m["size"] for m in group}) == 1
            keep = _keeper(group, civitai_stem)
            for m in group:
                if m is keep:
                    continue
                if same_size:
                    items.append(_item(m, "merge", keep["name"],
                                       f"same civitai version and file size as {keep['name']}"))
                    leaving.add(os.path.abspath(m["path"]))
                else:
                    items.append(_item(m, "conflict", keep["name"],
                                       f"same civitai version as {keep['name']} but a different file size"))

        for m in models:
            if os.path.abspath(m["path"]) in leaving:
                continue
            civitai_stem = _stem(m["civitai_file"])
            hit = SUFFIX_RE.match(m["stem"])
            if not civitai_stem or m["stem"] == civitai_stem or not hit or hit.group("base") != civitai_stem:
                continue   # named like civitai, or renamed on purpose (not just a _N suffix)
            ignore = [m["path"], *leaving]
            if name_taken(m["type"], m["root"], civitai_stem + m["ext"], ignore=ignore):
                items.append(_item(m, "conflict", civitai_stem,
                                   f"civitai's name '{civitai_stem}' is used by another model"))
            else:
                items.append(_item(m, "rename", civitai_stem, f"civitai's file name is '{m['civitai_file']}'"))
    counts = {a: sum(1 for i in items if i["action"] == a) for a in ("rename", "merge", "conflict")}
    return {"items": items, "counts": counts}


def _item(m: dict, action: str, target: str, reason: str) -> dict:
    return {
        "type": m["type"], "name": m["name"], "stem": m["stem"], "civitai_file": m["civitai_file"],
        "version_id": m["info"].get("id"), "model_name": (m["info"].get("model") or {}).get("name"),
        "added_examples": _added_count(m["path"]), "action": action, "target": target, "reason": reason,
    }


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------

def merge_into(model_type: str, src: str, dst: str) -> dict:
    """Move what only the copy at `src` has into the copy at `dst` (same civitai version), then
    delete `src` with all its files: added examples (#101+, renumbered after dst's own, prompts
    renamed), civitai example images dst lacks, the card file and the preview when dst has none."""
    src_stem, dst_stem = _stem(src), _stem(dst)
    src_base, dst_base = os.path.splitext(src)[0], os.path.splitext(dst)[0]
    moved = examples.move_added_examples(src, dst, model_type)
    copied = []
    dst_indexes = {examples.example_index_of(p) for p in examples.example_files(dst)}
    for p in examples.example_files(src):
        index = examples.example_index_of(p)
        if index is not None and index < examples.USER_BASE and index not in dst_indexes:
            target = dst_base + os.path.basename(p)[len(src_stem):]
            shutil.copy2(p, target)
            copied.append(os.path.basename(target))
    if os.path.isfile(src_base + ".json") and not os.path.isfile(dst_base + ".json"):
        shutil.copy2(src_base + ".json", dst_base + ".json")
        examples.rename_in_texts(dst, src_stem, dst_stem, model_type)
        copied.append(os.path.basename(dst_base + ".json"))
    if not any(os.path.isfile(dst_base + s) for s in local_models.PREVIEW_SUFFIXES):
        for s in local_models.PREVIEW_SUFFIXES:
            if os.path.isfile(src_base + s):
                shutil.copy2(src_base + s, dst_base + s)
                copied.append(os.path.basename(dst_base + s))
                break
    removed, failed = [], []
    for p in examples.model_files(src) + [src_base + s for s in local_models.PREVIEW_SUFFIXES]:
        if not os.path.isfile(p):
            continue
        try:
            os.remove(p)
            removed.append(os.path.basename(p))
        except OSError as e:
            failed.append({"file": os.path.basename(p), "error": str(e)})
    return {"moved_examples": moved, "copied": copied, "removed": removed, "failed": failed}


def fix(model_types, names=None, merge: bool = True, rename_model=None) -> dict:
    """Apply what scan() suggests (only for `names` when given): rename and (merge=True) merge.
    rename_model(model_type, path, new_stem) -> result dict does the renaming (api.rename_model).
    Returns {"results": [...], "mapping": {old name: new name}, "skipped": [...]}: mapping is what
    prompts should use instead (for the WebUI's saved prompts)."""
    report = scan(model_types)
    wanted = {n.replace("\\", "/").strip("/") for n in names} if names else None
    results, skipped, mapping = [], [], {}
    # merges first: the copy that stays may be the one a rename would have collided with
    order = sorted(report["items"], key=lambda i: {"merge": 0, "rename": 1}.get(i["action"], 2))
    for it in order:
        if wanted is not None and it["name"] not in wanted and it["stem"] not in wanted:
            continue
        if it["action"] == "conflict" or (it["action"] == "merge" and not merge):
            skipped.append(it)
            continue
        found = local_models.find(it["type"], it["name"])
        if not found:
            skipped.append({**it, "reason": "not found any more"})
            continue
        folder, _root, path, _filename = found
        try:
            if it["action"] == "merge":
                keep = local_models.find(it["type"], it["target"])
                if not keep:
                    skipped.append({**it, "reason": f"{it['target']} not found any more"})
                    continue
                r = merge_into(it["type"], path, keep[2])
                mapping[it["stem"]] = _stem(keep[2])
            else:
                r = rename_model(it["type"], path, it["target"])
                mapping[it["stem"]] = it["target"]
        except Exception as e:  # noqa: BLE001 — report and go on with the others
            detail = getattr(e, "detail", None) or str(e)
            skipped.append({**it, "reason": f"failed: {detail}"})
            continue
        results.append({**it, "result": r})
    # chains (a_2 merged into a, a renamed to b) end at the final name
    for old in list(mapping):
        seen = {old}
        while mapping[old] in mapping and mapping[old] not in seen:
            seen.add(mapping[old])
            mapping[old] = mapping[mapping[old]]
    return {"results": results, "skipped": skipped, "mapping": mapping}
