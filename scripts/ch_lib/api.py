# -*- coding: UTF-8 -*-
"""HTTP API for Civitai Helper.

Registered from civitai_helper.py through script_callbacks.on_app_started,
so everything lives inside this extension and the WebUI core is untouched.

All routes sit under /civitai-helper/v1 (see PREFIX and the docstrings below;
the WebUI's /docs page lists them too once the app has started).

Read-only endpoints answer immediately. Long-running work (scan, download,
check-new-version) runs on one background worker thread, in order, and is
exposed as *tasks*: the POST returns the task record, GET /tasks/{id} polls
it, and passing `wait: true` on the POST blocks (up to `timeout` seconds) so
a single call can do the whole job.

Examples (see ch_lib/examples.py): GET /models/{type}/examples lists a
model's civitai example images with their prompts and which are on disk;
POST /fetch-previews, /download-examples and /write-card-info are tasks that
work on a list of model types or on one model (type + name).

If the WebUI was started with --api-auth, the same basic-auth credentials
are required for every route here.
"""
import os
import re
import time
import uuid
import queue
import secrets
import threading
import traceback
from collections import OrderedDict
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from modules import shared

from . import util
from . import model
from . import civitai
from . import ch_settings
from . import local_models
from . import examples
from . import model_action_civitai


PREFIX = "/civitai-helper/v1"
MAX_TASKS = 200      # finished tasks beyond this are dropped, oldest first
MAX_WAIT = 3600.0    # upper bound for `wait` blocking, seconds


# ---------------------------------------------------------------------------
# Task registry (single worker, FIFO)
# ---------------------------------------------------------------------------

class Task:
    def __init__(self, kind: str, params: dict):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.params = params
        self.status = "queued"      # queued | running | done | error
        self.result: Any = None
        self.error: Optional[str] = None
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.finished = threading.Event()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": round((self.finished_at or time.time()) - (self.started_at or self.created_at), 1),
        }


_tasks: "OrderedDict[str, Task]" = OrderedDict()
_tasks_lock = threading.Lock()
_queue: "queue.Queue" = queue.Queue()
_worker: Optional[threading.Thread] = None


def _worker_loop():
    while True:
        task, fn = _queue.get()
        task.status = "running"
        task.started_at = time.time()
        util.printD(f"API task {task.id} ({task.kind}) started")
        try:
            task.result = fn()
            task.status = "done"
        except HTTPException as e:
            task.status = "error"
            task.error = str(e.detail)
        except Exception as e:
            task.status = "error"
            task.error = str(e) or e.__class__.__name__
            util.printD(f"API task {task.id} ({task.kind}) failed: {task.error}")
            traceback.print_exc()
        finally:
            task.finished_at = time.time()
            task.finished.set()
            _queue.task_done()
            util.printD(f"API task {task.id} ({task.kind}) {task.status}")


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_worker_loop, name="civitai-helper-api", daemon=True)
        _worker.start()


def _submit(kind: str, params: dict, fn) -> Task:
    task = Task(kind, params)
    with _tasks_lock:
        _tasks[task.id] = task
        while len(_tasks) > MAX_TASKS:
            for tid, t in list(_tasks.items()):
                if t.finished.is_set():
                    del _tasks[tid]
                    break
            else:
                break
    _ensure_worker()
    _queue.put((task, fn))
    return task


def _task_response(task: Task, wait: bool, timeout: float) -> dict:
    if wait:
        task.finished.wait(timeout=max(0.0, min(float(timeout), MAX_WAIT)))
    return task.to_dict()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: Optional[str], limit: int = 1500) -> str:
    if not text:
        return ""
    t = _TAG_RE.sub(" ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def _check_model_type(model_type: str):
    if model_type not in model.folders:
        raise HTTPException(
            400, f"unknown model type '{model_type}'; expected one of {list(model.folders.keys())}"
        )


def _normalize_types(types) -> List[str]:
    if isinstance(types, str):
        types = [types]
    if not types:
        raise HTTPException(400, "model_types is required")
    for t in types:
        _check_model_type(t)
    return list(types)


def _find_local_model(model_type: str, name: str):
    """Match by file name or by path relative to the model folder (404 if absent)."""
    if not (name or "").strip():
        raise HTTPException(400, "name is required")
    found = local_models.find(model_type, name)
    if not found:
        raise HTTPException(404, f"no {model_type} model named '{name}'")
    return found


def _resolve_targets(req: "TargetRequest"):
    """(model_types, single) for the examples.run_* helpers, plus params for the task record."""
    if req.type or req.name:
        if not (req.type and req.name):
            raise HTTPException(400, "single model mode needs both type and name")
        _check_model_type(req.type)
        _folder, _root, path, _filename = _find_local_model(req.type, req.name)
        return None, (req.type, path), {"type": req.type, "name": req.name}
    types = _normalize_types(req.model_types)
    return types, None, {"model_types": types}


def _fetch_remote_model(url_or_id: str) -> dict:
    """Resolve a civitai model id / page URL to its model record."""
    model_id = civitai.get_model_id_from_url(str(url_or_id or "").strip())
    if not model_id:
        raise HTTPException(400, "url_or_id must be a civitai model id or a civitai model page URL")
    info = civitai.get_model_info_by_id(model_id)
    if info is None:
        raise HTTPException(502, "civitai API request failed; check the WebUI console log")
    if not info:
        raise HTTPException(404, f"civitai has no model with id {model_id}")
    ctype = info.get("type")
    model_type = civitai.model_type_dict.get(ctype)
    if not model_type:
        raise HTTPException(
            400, f"civitai model type '{ctype}' is not supported; supported: {list(civitai.model_type_dict.keys())}"
        )
    if not info.get("modelVersions"):
        raise HTTPException(404, f"civitai model {model_id} has no versions")
    return {"id": model_id, "info": info, "model_type": model_type}


def _version_view(v: dict) -> dict:
    files = []
    for f in v.get("files") or []:
        files.append({
            "name": f.get("name"),
            "size_kb": f.get("sizeKB"),
            "type": f.get("type"),
            "format": (f.get("metadata") or {}).get("format"),
            "primary": bool(f.get("primary", False)),
            "download_url": f.get("downloadUrl"),
        })
    return {
        "id": v.get("id"),
        "name": v.get("name"),
        "version_str": f"{v.get('name')}_{v.get('id')}",
        "base_model": v.get("baseModel"),
        "trained_words": v.get("trainedWords") or [],
        "published_at": v.get("publishedAt"),
        "download_url": v.get("downloadUrl"),
        "files": files,
    }


def _pick_version(versions: list, version_id: Optional[int], version: Optional[str]) -> dict:
    if version_id is not None:
        for v in versions:
            if str(v.get("id")) == str(version_id):
                return v
        raise HTTPException(404, f"version id {version_id} not found; available: {[v.get('id') for v in versions]}")
    if version:
        for v in versions:
            if version in (f"{v.get('name')}_{v.get('id')}", v.get("name"), str(v.get("id"))):
                return v
        raise HTTPException(404, f"version '{version}' not found; available: {[v.get('name') for v in versions]}")
    return versions[0]  # civitai lists the newest version first


def _resolve_subfolder(model_type: str, subfolder: Optional[str], create: bool) -> str:
    """Validate a subfolder string and return it in the '/sub/dir' form that
    dl_model_by_input expects ('/' means the model root folder)."""
    sub = (subfolder or "/").replace("\\", "/").strip()
    if not sub.startswith("/"):
        sub = "/" + sub
    rel = sub.strip("/")
    if any(part in ("..", "") for part in rel.split("/")) and rel:
        raise HTTPException(400, f"invalid subfolder '{subfolder}'")
    root = model.folders[model_type]
    target = os.path.join(root, rel) if rel else root
    if not os.path.isdir(target):
        if not create:
            raise HTTPException(
                400, f"subfolder '{sub}' does not exist under {root}; pass create_subfolder=true to create it"
            )
        os.makedirs(target, exist_ok=True)
    return sub


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class TaskOptions(BaseModel):
    wait: bool = Field(False, description="Block until the task finishes (or `timeout` elapses).")
    timeout: float = Field(300, ge=0, le=MAX_WAIT, description="Seconds to wait when wait=true.")


class ScanRequest(TaskOptions):
    model_types: List[str] = Field(..., description="Any of: ti, hyper, ckp, lora")


class CheckNewVersionRequest(TaskOptions):
    model_types: List[str] = Field(..., description="Any of: ti, hyper, ckp, lora")


class TargetRequest(TaskOptions):
    """Either a list of model types, or one model given by type + name."""
    model_types: Optional[List[str]] = Field(None, description="Any of: ti, hyper, ckp, lora (batch mode)")
    type: Optional[str] = Field(None, description="Single model mode: ti | hyper | ckp | lora")
    name: Optional[str] = Field(None, description="Single model mode: file name or path relative to the model folder")


class FetchPreviewsRequest(TargetRequest):
    pass


class DownloadExamplesRequest(TargetRequest):
    max_images: int = Field(0, ge=0, le=100, description="Example images to keep per model (0 = all)")
    overwrite: bool = Field(False, description="Re-download example images that already exist")


class WriteCardInfoRequest(TargetRequest):
    overwrite: bool = Field(False, description="Replace existing description / notes instead of only filling empty ones")
    set_sd_version: bool = Field(True, description="Also fill the card's 'sd version' (Forge preset) from the civitai base model when it is unknown")


class DownloadRequest(TaskOptions):
    url_or_id: str = Field(..., description="civitai model id or model page URL")
    version_id: Optional[int] = Field(None, description="Version id to download (default: newest)")
    version: Optional[str] = Field(None, description="Version name or 'name_id' string (alternative to version_id)")
    subfolder: str = Field("/", description="Subfolder under the model type's folder, e.g. '/' or '/characters'")
    create_subfolder: bool = False
    dl_all: bool = Field(False, description="Download every file of the version, not only the primary one")


# ---------------------------------------------------------------------------
# Auth (mirrors --api-auth of the main API, without touching it)
# ---------------------------------------------------------------------------

def _auth_dependency():
    api_auth = getattr(shared.cmd_opts, "api_auth", None)
    if not api_auth:
        return None
    creds = {}
    for pair in api_auth.split(","):
        user, _, password = pair.partition(":")
        creds[user] = password
    security = HTTPBasic()

    def auth(credentials: HTTPBasicCredentials = Depends(security)):
        expected = creds.get(credentials.username)
        if expected is not None and secrets.compare_digest(credentials.password, expected):
            return credentials.username
        raise HTTPException(status_code=401, detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Basic"})

    return auth


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def build_router() -> APIRouter:
    auth = _auth_dependency()
    router = APIRouter(prefix=PREFIX, tags=["civitai-helper"],
                       dependencies=[Depends(auth)] if auth else [])

    @router.get("/version", summary="Extension version, effective settings and model folders")
    def get_version():
        s = ch_settings.load()
        return {
            "version": util.version,
            "settings": ch_settings.public(s),
            "model_folders": dict(model.folders),
        }

    @router.get("/model-types", summary="Supported model types and their folders")
    def get_model_types():
        return [
            {"type": t, "folder": f, "exists": os.path.isdir(f)}
            for t, f in model.folders.items()
        ]

    def _inventory(model_type, q, no_info_only, empty_info_only, metadata, limit, offset, fmt, compact=False):
        _check_model_type(model_type)
        items = local_models.collect(model_type, metadata, q, no_info_only, empty_info_only)
        page = items[offset:offset + limit]
        fmt = (fmt or "json").lower()
        if fmt == "json":
            if compact:
                page = [local_models.compact_entry(e) for e in page]
            return {
                "type": model_type,
                "folder": model.folders[model_type],
                "total": len(items),
                "offset": offset,
                "count": len(page),
                "items": page,
            }
        try:
            text, mime, _filename = local_models.render(page, fmt, model_type, total=len(items), offset=offset)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return Response(content=text, media_type=mime)

    @router.get("/models", summary="List local models of one type (with civitai info when known)")
    def list_models(
        type: str = Query(..., description="ti | hyper | ckp | lora"),
        q: Optional[str] = Query(None, description="Case-insensitive substring filter on file name / path / model name / trigger words / training tags"),
        no_info_only: bool = Query(False, description="Only models without a .civitai.info file"),
        empty_info_only: bool = Query(False, description="Only models whose info file has no civitai id (not found on civitai)"),
        metadata: bool = Query(False, description="Also read training metadata from the safetensors header (base model version, top training tags, network dim)"),
        limit: int = Query(200, ge=1, le=5000),
        offset: int = Query(0, ge=0),
        format: str = Query("json", description="json | csv | md"),
        compact: bool = Query(False, description="JSON only: short entries (name, prompt_tag, model name/version/base model, trigger words, top training tags)"),
    ):
        return _inventory(type, q, no_info_only, empty_info_only, metadata, limit, offset, format, compact)

    @router.get("/loras", summary="Every LoRA this WebUI has: file, prompt tag, civitai name/version/base model/trigger words, training metadata")
    def list_loras(
        q: Optional[str] = Query(None, description="Case-insensitive substring filter on file name / model name / trigger words / training tags"),
        metadata: bool = Query(True, description="Read training metadata from the safetensors header (base model version, top training tags)"),
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
        format: str = Query("json", description="json | csv | md"),
        compact: bool = Query(False, description="JSON only: short entries (name, prompt_tag, model name/version/base model, trigger words, top training tags)"),
    ):
        return _inventory("lora", q, False, False, metadata, limit, offset, format, compact)

    @router.get("/models/{type}/info", summary="Civitai info and training metadata stored for one local model")
    def get_local_model_info(
        type: str,
        name: str = Query(..., description="File name (e.g. foo.safetensors) or path relative to the model folder"),
        full: bool = Query(False, description="Also return the raw .civitai.info JSON and the raw safetensors metadata"),
    ):
        _check_model_type(type)
        folder, root, path, filename = _find_local_model(type, name)
        entry = local_models.entry(type, folder, root, path, filename, with_metadata=True)
        if full:
            base, _ = os.path.splitext(path)
            info_file = base + civitai.suffix + model.info_ext
            entry["info"] = model.load_model_info(info_file) if os.path.isfile(info_file) else None
            entry["safetensors_metadata"] = local_models.read_safetensors_metadata(path)
        return entry

    @router.get("/models/{type}/examples", summary="Civitai example images of one local model, with prompts and local copies")
    def get_local_model_examples(
        type: str,
        name: str = Query(..., description="File name (e.g. foo.safetensors) or path relative to the model folder"),
    ):
        _check_model_type(type)
        folder, _root, path, _filename = _find_local_model(type, name)
        return examples.list_examples(type, folder, path)

    @router.post("/fetch-previews", summary="Download the missing .preview.png of local models (no civitai API calls)")
    def post_fetch_previews(req: FetchPreviewsRequest):
        types, single, params = _resolve_targets(req)

        def run():
            s = ch_settings.load()
            return examples.run_fetch_previews(types, single, s["max_size_preview"], s["skip_nsfw_preview"])

        task = _submit("fetch_previews", params, run)
        return _task_response(task, req.wait, req.timeout)

    @router.post("/download-examples", summary="Save civitai example images next to local models as <model>.example_NN.<ext>")
    def post_download_examples(req: DownloadExamplesRequest):
        types, single, params = _resolve_targets(req)
        params.update({"max_images": req.max_images, "overwrite": req.overwrite})

        def run():
            s = ch_settings.load()
            return examples.run_download_examples(
                types, single, req.max_images, s["skip_nsfw_preview"], req.overwrite, s["max_size_preview"]
            )

        task = _submit("download_examples", params, run)
        return _task_response(task, req.wait, req.timeout)

    @router.post("/write-card-info", summary="Write trigger words and example prompts into the Extra Networks card metadata (<model>.json)")
    def post_write_card_info(req: WriteCardInfoRequest):
        types, single, params = _resolve_targets(req)
        params.update({"overwrite": req.overwrite, "set_sd_version": req.set_sd_version})

        def run():
            return examples.run_write_card_info(types, single, req.overwrite, req.set_sd_version)

        task = _submit("write_card_info", params, run)
        return _task_response(task, req.wait, req.timeout)

    @router.get("/model-info", summary="Look up a model on civitai by id or page URL")
    def get_remote_model_info(url_or_id: str = Query(..., description="civitai model id or model page URL")):
        ch_settings.load()
        r = _fetch_remote_model(url_or_id)
        info = r["info"]
        creator = info.get("creator") or {}
        stats = info.get("stats") or {}
        subfolders = util.get_subfolders(model.folders[r["model_type"]]) or []
        return {
            "model_id": int(r["id"]),
            "name": info.get("name"),
            "civitai_type": info.get("type"),
            "model_type": r["model_type"],
            "nsfw": info.get("nsfw"),
            "creator": creator.get("username"),
            "tags": info.get("tags") or [],
            "downloads": stats.get("downloadCount"),
            "rating": stats.get("rating"),
            "description": _strip_html(info.get("description")),
            "model_url": civitai.url_dict["modelPage"] + str(r["id"]),
            "target_folder": model.folders[r["model_type"]],
            "subfolders": ["/"] + sorted("/" + s.replace("\\", "/").lstrip("/") for s in subfolders),
            "versions": [_version_view(v) for v in info["modelVersions"]],
        }

    @router.post("/scan", summary="Scan local models: fetch missing civitai info files and previews")
    def post_scan(req: ScanRequest):
        types = _normalize_types(req.model_types)

        def run():
            s = ch_settings.load()
            output = model_action_civitai.scan_model(types, s["max_size_preview"], s["skip_nsfw_preview"])
            if not str(output).startswith("Done"):
                raise RuntimeError(str(output))
            m = re.search(r"Scanned (\d+) models, checked (\d+) images", output)
            return {
                "message": output,
                "models": int(m.group(1)) if m else None,
                "images": int(m.group(2)) if m else None,
            }

        task = _submit("scan", {"model_types": types}, run)
        return _task_response(task, req.wait, req.timeout)

    @router.post("/download", summary="Download a model version from civitai into the matching model folder")
    def post_download(req: DownloadRequest):
        ch_settings.load()
        r = _fetch_remote_model(req.url_or_id)
        info, model_type = r["info"], r["model_type"]
        version = _pick_version(info["modelVersions"], req.version_id, req.version)
        version_str = f"{version.get('name')}_{version.get('id')}"
        subfolder = _resolve_subfolder(model_type, req.subfolder, req.create_subfolder)
        params = {
            "model_id": int(r["id"]),
            "model_name": info.get("name"),
            "model_type": model_type,
            "version_id": version.get("id"),
            "version_name": version.get("name"),
            "subfolder": subfolder,
            "dl_all": req.dl_all,
        }

        def run():
            s = ch_settings.load()
            output = model_action_civitai.dl_model_by_input(
                info, model_type, subfolder, version_str, req.dl_all,
                s["max_size_preview"], s["skip_nsfw_preview"],
            )
            output = str(output or "")
            already = "already existed" in output
            ok = output.startswith("Done") or output.startswith("Model downloaded")
            if not (ok or already):
                raise RuntimeError(output or "download failed; check the WebUI console log")
            file_path = None
            for marker in ("Model downloaded to: ", "Model saved to: "):
                if marker in output:
                    file_path = output.split(marker, 1)[1].strip()
                    break
            return {
                **params,
                "ok": ok,
                "already_exists": already,
                "file": file_path,
                "trained_words": version.get("trainedWords") or [],
                "message": output,
            }

        task = _submit("download", params, run)
        return _task_response(task, req.wait, req.timeout)

    @router.post("/check-new-version", summary="Find local models that have a newer version on civitai")
    def post_check_new_version(req: CheckNewVersionRequest):
        types = _normalize_types(req.model_types)

        def run():
            s = ch_settings.load()
            rows = civitai.check_models_new_version_by_model_types(
                types, 1, s["check_new_ver_exist_in_all_folder"]
            ) or []
            out = []
            for row in rows:
                model_path, model_id, model_name, new_version_id, new_version_name, description, download_url, img_url = row
                out.append({
                    "model_path": model_path,
                    "model_id": model_id,
                    "model_name": model_name,
                    "model_url": (civitai.url_dict["modelPage"] + str(model_id)) if model_id else None,
                    "new_version_id": new_version_id,
                    "new_version_name": new_version_name,
                    "description": _strip_html(description),
                    "download_url": download_url,
                    "preview_url": img_url,
                })
            return out

        task = _submit("check_new_version", {"model_types": types}, run)
        return _task_response(task, req.wait, req.timeout)

    @router.get("/tasks", summary="Recent tasks, newest first")
    def list_tasks(limit: int = Query(50, ge=1, le=MAX_TASKS)):
        with _tasks_lock:
            tasks = list(_tasks.values())
        return [t.to_dict() for t in reversed(tasks)][:limit]

    @router.get("/tasks/{task_id}", summary="One task (poll until status is done or error)")
    def get_task(task_id: str, wait: bool = Query(False), timeout: float = Query(60, ge=0, le=MAX_WAIT)):
        with _tasks_lock:
            task = _tasks.get(task_id)
        if not task:
            raise HTTPException(404, f"no task {task_id}")
        return _task_response(task, wait, timeout)

    return router


def on_app_started(demo, app: FastAPI):
    """script_callbacks.on_app_started hook: mount the router on the WebUI app."""
    try:
        app.include_router(build_router())
        util.printD(f"API mounted at {PREFIX} (see /docs)")
    except Exception as e:
        util.printD(f"Failed to mount API: {e}")
        traceback.print_exc()
