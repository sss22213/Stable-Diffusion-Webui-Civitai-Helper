# -*- coding: UTF-8 -*-
# handle msg between js and python side
import os
import time
import json
import re
import requests
from . import util
from . import model
from . import setting

suffix = ".civitai"

# Default domain. Overridden by Settings → Civitai Helper → "Civitai Domain"
# at startup via apply_domain() below; mirrors like civitai.red are
# wire-compatible with the same /api/v1/... paths.
DEFAULT_DOMAIN = "civitai.com"


def _build_url_dict(domain: str) -> dict:
    base = f"https://{domain}"
    return {
        "modelPage":      f"{base}/models/",
        "modelId":        f"{base}/api/v1/models/",
        "modelVersionId": f"{base}/api/v1/model-versions/",
        "hash":           f"{base}/api/v1/model-versions/by-hash/",
        "images":         f"{base}/api/v1/images",
        "imagePage":      f"{base}/images/",
    }


url_dict = _build_url_dict(DEFAULT_DOMAIN)


def apply_domain(domain: str):
    """Repoint all API URLs at the given domain. Falls back to the default
    on empty input so a blank Settings field doesn't yield bad URLs."""
    domain = (domain or DEFAULT_DOMAIN).strip().rstrip("/")
    # Strip an accidentally-pasted scheme: settings UI accepts both
    # "civitai.red" and "https://civitai.red".
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    url_dict.update(_build_url_dict(domain))

model_type_dict = {
    "Checkpoint": "ckp",
    "TextualInversion": "ti",
    "Hypernetwork": "hyper",
    "LORA": "lora",
    "LoCon": "lora",
}



# get image with full size
# width is in number, not string
# return: url str
def get_full_size_image_url(image_url, width):
    return re.sub(r'/width=\d+/', '/width=' + str(width) + '/', image_url)


# use this sha256 to get model info from civitai
# return: model info dict
def get_model_info_by_hash(hash:str):
    util.printD("Request model info from civitai")

    if not hash:
        util.printD("hash is empty")
        return

    r = requests.get(url_dict["hash"]+hash, headers=util.def_headers, proxies=util.proxies)
    if not r.ok:
        if r.status_code == 404:
            # this is not a civitai model
            util.printD("Civitai does not have this model")
            return {}
        else:
            util.printD("Get error code: " + str(r.status_code))
            util.printD(r.text)
            return

    # try to get content
    content = None
    try:
        content = r.json()
    except Exception as e:
        util.printD("Parse response json failed")
        util.printD(str(e))
        util.printD("response:")
        util.printD(r.text)
        return
    
    if not content:
        util.printD("error, content from civitai is None")
        return
    
    return content



def get_model_info_by_id(id:str) -> dict:
    util.printD("Request model info from civitai")

    if not id:
        util.printD("id is empty")
        return

    r = requests.get(url_dict["modelId"]+str(id), headers=util.def_headers, proxies=util.proxies)
    if not r.ok:
        if r.status_code == 404:
            # this is not a civitai model
            util.printD("Civitai does not have this model")
            return {}
        else:
            util.printD("Get error code: " + str(r.status_code))
            util.printD(r.text)
            return

    # try to get content
    content = None
    try:
        content = r.json()
    except Exception as e:
        util.printD("Parse response json failed")
        util.printD(str(e))
        util.printD("response:")
        util.printD(r.text)
        return
    
    if not content:
        util.printD("error, content from civitai is None")
        return
    
    return content


def get_version_info_by_version_id(id:str) -> dict:
    util.printD("Request version info from civitai")

    if not id:
        util.printD("id is empty")
        return

    r = requests.get(url_dict["modelVersionId"]+str(id), headers=util.def_headers, proxies=util.proxies)
    if not r.ok:
        if r.status_code == 404:
            # this is not a civitai model
            util.printD("Civitai does not have this model version")
            return {}
        else:
            util.printD("Get error code: " + str(r.status_code))
            util.printD(r.text)
            return

    # try to get content
    content = None
    try:
        content = r.json()
    except Exception as e:
        util.printD("Parse response json failed")
        util.printD(str(e))
        util.printD("response:")
        util.printD(r.text)
        return
    
    if not content:
        util.printD("error, content from civitai is None")
        return
    
    return content


def get_version_info_by_model_id(id:str) -> dict:

    model_info = get_model_info_by_id(id)
    if not model_info:
        util.printD(f"Failed to get model info by id: {id}")
        return
    
    # check content to get version id
    if "modelVersions" not in model_info.keys():
        util.printD("There is no modelVersions in this model_info")
        return
    
    if not model_info["modelVersions"]:
        util.printD("modelVersions is None")
        return
    
    if len(model_info["modelVersions"])==0:
        util.printD("modelVersions is Empty")
        return
    
    def_version = model_info["modelVersions"][0]
    if not def_version:
        util.printD("default version is None")
        return
    
    if "id" not in def_version.keys():
        util.printD("default version has no id")
        return
    
    version_id = def_version["id"]
    
    if not version_id:
        util.printD("default version's id is None")
        return

    # get version info
    version_info = get_version_info_by_version_id(str(version_id))
    if not version_info:
        util.printD(f"Failed to get version info by version_id: {version_id}")
        return

    return version_info




# get model info file's content by model type and search_term
# parameter: model_type, search_term
# return: model_info
def load_model_info_by_search_term(model_type, search_term):
    util.printD(f"Load model info of {search_term} in {model_type}")
    if model_type not in model.folders.keys():
        util.printD("unknow model type: " + model_type)
        return
    
    # with sd webui < 1.8.0
    # search_term = subfolderpath + model name + ext. And it always start with a / even there is no sub folder
    # with sd webui >= 1.8.0
    # search_term = model type + subfolderpath + model name + ext. And it always start with a / even there is no sub folder
    # this model type is based on sd webui's model folder name. ti is embeddings, ckp is Stable-diffusion, and so on
    base, ext = os.path.splitext(search_term)
    model_info_base = base

    if model_info_base[:1] == "/":
        model_info_base = model_info_base[1:]


    
    model_folder_name = "";
    if model_type == "ti":
        model_folder_name = "embeddings"
    elif model_type == "hyper":
        model_folder_name = "hypernetworks"
    elif model_type == "ckp":
        model_folder_name = "Stable-diffusion"
    else:
        model_folder_name = "Lora"

    # model folder path could be customized
    model_folder = model.folders[model_type]

    model_folder_name = os.path.basename(model_folder)



    # check if model folder is already in search_term
    if model_info_base.startswith(model_folder_name):
        # this is sd webui v1.8.0+'s search_term
        # need to remove this model_folder_name+"/" or "\\" from model_info_base
        model_info_base = model_info_base[len(model_folder_name):]

        # util.printD("cut model_info_base: " + model_info_base)

        if model_info_base.startswith("/") or model_info_base.startswith("\\"):
            model_info_base = model_info_base[1:]

        # util.printD("final model_info_base: " + model_info_base)


    
    model_info_filename = model_info_base + suffix + model.info_ext
    model_info_filepath = os.path.join(model_folder, model_info_filename)

    if not os.path.isfile(model_info_filepath):
        util.printD("Can not find model info file: " + model_info_filepath)
        return
    
    return model.load_model_info(model_info_filepath)


# get model info file's content by model path
# parameter: model_path
# return: model_info
def load_model_info_by_model_path(model_path):
    util.printD(f"Load model info of {model_path}")
    
    # search_term = subfolderpath + model name + ext. And it always start with a / even there is no sub folder
    base, ext = os.path.splitext(model_path)
    model_info_filepath = base + suffix + model.info_ext

    if not os.path.isfile(model_info_filepath):
        util.printD("Can not find model info file: " + model_info_filepath)
        return
    
    return model.load_model_info(model_info_filepath)




# get model file names by model type
# parameter: model_type - string
# parameter: filter - dict, which kind of model you need
# return: model name list
def get_model_names_by_type_and_filter(model_type:str, filter:dict) -> list:
    
    model_folder = model.folders[model_type]

    # set filter
    # only get models don't have a civitai info file
    no_info_only = False
    empty_info_only = False

    if filter:
        if "no_info_only" in filter.keys():
            no_info_only = filter["no_info_only"]
        if "empty_info_only" in filter.keys():
            empty_info_only = filter["empty_info_only"]



    # get information from filter
    # only get those model names don't have a civitai model info file
    model_names = []
    for root, dirs, files in os.walk(model_folder, followlinks=True):
        for filename in files:
            item = os.path.join(root, filename)
            # check extension
            base, ext = os.path.splitext(item)
            if ext in model.exts:
                # find a model

                # check filter
                if no_info_only:
                    # check model info file
                    info_file = base + suffix + model.info_ext
                    if os.path.isfile(info_file):
                        continue

                if empty_info_only:
                    # check model info file
                    info_file = base + suffix + model.info_ext
                    if os.path.isfile(info_file):
                        # load model info
                        model_info = model.load_model_info(info_file)
                        # check content
                        if model_info:
                            if "id" in model_info.keys():
                                # find a non-empty model info file
                                continue

                model_names.append(filename)


    return model_names

def get_model_names_by_input(model_type, empty_info_only):
    return get_model_names_by_type_and_filter(model_type, {"empty_info_only":empty_info_only})
    

# get id from url
def get_model_id_from_url(url:str) -> str:
    util.printD("Run get_model_id_from_url")
    id = ""

    if not url:
        util.printD("url or model id can not be empty")
        return ""

    if url.isnumeric():
        # is already an id
        id = str(url)
        return id
    
    s = re.sub("\\?.+$", "", url).split("/")
    if len(s) < 2:
        util.printD("url is not valid")
        return ""
    
    if s[-2].isnumeric():
        id  = s[-2]
    elif s[-1].isnumeric():
        id  = s[-1]
    else:
        util.printD("There is no model id in this url")
        return ""
    
    return id


# is this civitai image entry NSFW?
# old API: "nsfw": "None" | "Soft" | "Mature" | "X" (or bool)
# new API: "nsfwLevel": 1 (PG) | 2 (PG13) | 4 (R) | 8 (X) | 16 (XXX)
def is_nsfw_image(img_dict: dict) -> bool:
    level = img_dict.get("nsfwLevel")
    if isinstance(level, int):
        return level > 1
    nsfw = img_dict.get("nsfw")
    if isinstance(nsfw, bool):
        return nsfw
    return bool(nsfw) and str(nsfw) != "None"


# url of an image at its largest size
def get_example_image_url(img_dict: dict, max_size: bool) -> str:
    img_url = img_dict.get("url") or ""
    if max_size and img_dict.get("width"):
        # "/width=NNN/" urls can be re-sized; "/original=true/" urls are already full size
        img_url = get_full_size_image_url(img_url, img_dict["width"])
    return img_url


# get preview image by model path
# image will be saved to file
# return: "exists" | "downloaded" | "no_info" | "empty_info" | "no_images" | "failed"
def get_preview_image_by_model_path(model_path:str, max_size_preview, skip_nsfw_preview) -> str:
    if not model_path:
        util.printD("model_path is empty")
        return "failed"

    if not os.path.isfile(model_path):
        util.printD("model_path is not a file: "+model_path)
        return "failed"

    base, ext = os.path.splitext(model_path)
    sec_preview = base+".preview.png"
    info_file = base + suffix + model.info_ext

    # check preview image
    if os.path.isfile(sec_preview):
        return "exists"

    # need to download preview image
    util.printD("Checking preview image for model: " + model_path)
    if not os.path.isfile(info_file):
        return "no_info"

    model_info = model.load_model_info(info_file)
    if not model_info:
        util.printD("Model Info is empty")
        return "empty_info"

    images = model_info.get("images") or []
    if not images:
        return "no_images"

    tried = 0
    for img_dict in images:
        if is_nsfw_image(img_dict):
            util.printD("This image is NSFW")
            if skip_nsfw_preview:
                util.printD("Skip NSFW image")
                continue

        preview_type = img_dict.get("type")
        if preview_type != "image":
            util.printD(f"Unsupported preview type: {preview_type}, ignore.")
            continue

        img_url = get_example_image_url(img_dict, max_size_preview)
        if not img_url:
            continue

        tried += 1
        # we only need 1 preview image; if this one fails (deleted on civitai, network error)
        # fall through to the next example image instead of giving up
        if util.download_file(img_url, sec_preview):
            return "downloaded"
        util.printD("Preview download failed, trying next example image")

    if tried:
        util.printD("All example images failed to download for: " + model_path)
    return "failed"



# search local model by version id in 1 folder, no subfolder
# return - model_info
def search_local_model_info_by_version_id(folder:str, version_id:int, walk:bool=False) -> dict:
    util.printD("Searching local model by version id")
    util.printD("folder: " + folder)
    util.printD("version_id: " + str(version_id))
    util.printD("walk: " + str(walk))

    if not folder:
        util.printD("folder is none")
        return

    if not os.path.isdir(folder):
        util.printD("folder is not a dir")
        return
    
    if not version_id:
        util.printD("version_id is none")
        return
    
    # search civitai model info file
    # walk from top
    if walk:
        util.printD(f"Searching model version id {version_id} by walking in: {folder}")
        for root, dirs, files in os.walk(folder, followlinks=True):
            for filename in files:
                # check ext
                base, ext = os.path.splitext(filename)
                if ext == model.info_ext:
                    # find info file
                    if len(base) < 9:
                        # not a civitai info file
                        continue

                    if base[-8:] == suffix:
                        # find a civitai info file
                        path = os.path.join(root, filename)
                        model_info = model.load_model_info(path)
                        if not model_info:
                            continue

                        if "id" not in model_info.keys():
                            continue

                        id = model_info["id"]
                        if not id:
                            continue

                        # util.printD(f"Compare version id, src: {id}, target:{version_id}")
                        if str(id) == str(version_id):
                            # find the one
                            util.printD(f"Found model: {path}")
                            return model_info

    # only check current path
    else:
        util.printD(f"Searching model version id {version_id} under: {folder}")
        for filename in os.listdir(folder):
            # check ext
            base, ext = os.path.splitext(filename)
            if ext == model.info_ext:
                # find info file
                if len(base) < 9:
                    # not a civitai info file
                    continue

                if base[-8:] == suffix:
                    # find a civitai info file
                    path = os.path.join(folder, filename)
                    model_info = model.load_model_info(path)
                    if not model_info:
                        continue

                    if "id" not in model_info.keys():
                        continue

                    id = model_info["id"]
                    if not id:
                        continue

                    # util.printD(f"Compare version id, src: {id}, target:{version_id}")
                    if str(id) == str(version_id):
                        # find the one
                        return model_info
                        

    return





# check new version for a model by model path
# return (model_path, model_id, model_name, new_verion_id, new_version_name, description, download_url, img_url)
def check_model_new_version_by_path(model_path:str, delay:float=1) -> tuple:
    if not model_path:
        util.printD("model_path is empty")
        return

    if not os.path.isfile(model_path):
        util.printD("model_path is not a file: "+model_path)
        return
    
    # get model info file name
    base, ext = os.path.splitext(model_path)
    info_file = base + suffix + model.info_ext
    
    if not os.path.isfile(info_file):
        return
    
    # get model info
    model_info_file = model.load_model_info(info_file)
    if not model_info_file:
        return

    if "id" not in model_info_file.keys():
        return
    
    local_version_id = model_info_file["id"]
    if not local_version_id:
        return

    if "modelId" not in model_info_file.keys():
        return
    
    model_id = model_info_file["modelId"]
    if not model_id:
        return
    
    # get model info by id from civitai
    model_info = get_model_info_by_id(model_id)
    # delay before next request, to prevent to be treat as DDoS 
    util.printD(f"delay:{delay} second")
    time.sleep(delay)

    if not model_info:
        return
    
    if "modelVersions" not in model_info.keys():
        return
    
    modelVersions = model_info["modelVersions"]
    if not modelVersions:
        return
    
    if not len(modelVersions):
        return
    
    current_version = modelVersions[0]
    if not current_version:
        return
    
    if "id" not in current_version.keys():
        return
    
    current_version_id = current_version["id"]
    if not current_version_id:
        return

    util.printD(f"Compare version id, local: {local_version_id}, remote: {current_version_id} ")
    if current_version_id == local_version_id:
        return

    model_name = ""
    if "name" in model_info.keys():
        model_name = model_info["name"]
    
    if not model_name:
        model_name = ""


    new_version_name = ""
    if "name" in current_version.keys():
        new_version_name = current_version["name"]
    
    if not new_version_name:
        new_version_name = ""

    description = ""
    if "description" in current_version.keys():
        description = current_version["description"]
    
    if not description:
        description = ""

    downloadUrl = ""
    if "downloadUrl" in current_version.keys():
        downloadUrl = current_version["downloadUrl"]
    
    if not downloadUrl:
        downloadUrl = ""

    # get 1 preview image
    img_url = ""
    if "images" in current_version.keys():
        if current_version["images"]:
            if current_version["images"][0]:
                if "url" in current_version["images"][0].keys():
                    img_url = current_version["images"][0]["url"]
                    if not img_url:
                        img_url = ""


    
    return (model_path, model_id, model_name, current_version_id, new_version_name, description, downloadUrl, img_url)




# check model's new version
# parameter: delay - float, how many seconds to delay between each request to civitai
# return: new_versions - a list for all new versions, each one is (model_path, model_id, model_name, new_verion_id, new_version_name, description, download_url, img_url)
def check_models_new_version_by_model_types(model_types:list, delay:float=1, check_new_ver_exist_in_all_folder:bool=False) -> list:
    util.printD("Checking models' new version")

    if not model_types:
        return []

    # check model types, which cloud be a string as 1 type
    mts = []
    if type(model_types) == str:
        mts.append(model_types)
    elif type(model_types) == list:
        mts = model_types
    else:
        util.printD("Unknow model types:")
        util.printD(model_types)
        return []

    # output is a markdown document string to show a list of new versions on UI
    output = ""
    # new version list
    new_versions = []

    # walk all models
    for model_type, model_folder in model.folders.items():
        if model_type not in mts:
            continue

        util.printD("Scanning path: " + model_folder)
        for root, dirs, files in os.walk(model_folder, followlinks=True):
            for filename in files:
                # check ext
                item = os.path.join(root, filename)
                base, ext = os.path.splitext(item)
                if ext in model.exts:
                    # find a model
                    r = check_model_new_version_by_path(item, delay)

                    if not r:
                        continue

                    model_path, model_id, model_name, current_version_id, new_version_name, description, downloadUrl, img_url = r
                    # check exist
                    if not current_version_id:
                        continue

                    # check this version id in list
                    is_already_in_list = False
                    for new_version in new_versions:
                        if current_version_id == new_version[3]:
                            # already in list
                            is_already_in_list = True
                            break

                    if is_already_in_list:
                        util.printD("New version is already in list")
                        continue

                    # search this new version id to check if this model is already downloaded
                    if check_new_ver_exist_in_all_folder:
                        # walk from top folder for this model type
                        target_model_info = search_local_model_info_by_version_id(model_folder, current_version_id, check_new_ver_exist_in_all_folder)
                    else:
                        # only check current folder
                        target_model_info = search_local_model_info_by_version_id(root, current_version_id, check_new_ver_exist_in_all_folder)
                    if target_model_info:
                        util.printD("New version is already existed")
                        continue

                    # add to list
                    new_versions.append(r)




    return new_versions


# ---------------------------------------------------------------------------
# images posted by civitai users (GET /api/v1/images)
# ---------------------------------------------------------------------------
# The REST API only returns the generation data (`meta`: prompt, sampler, ...)
# when withMeta=true is passed, and only SFW images unless `nsfw` is given
# ("None" | "Soft" | "Mature" | "X" = highest level to include).
# An imageId query ignores withMeta, so single images are looked up via their
# post (see get_images_by_ids).

IMAGE_SORTS = ("Most Reactions", "Most Comments", "Most Collected", "Newest", "Oldest")


def search_images(params: dict, timeout=(15, 60)):
    """One page of GET /api/v1/images. Returns the response dict, or raises
    RuntimeError with civitai's message (so API callers can report it)."""
    q = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    q.setdefault("withMeta", "true")
    try:
        r = requests.get(url_dict["images"], params=q, headers=util.def_headers,
                         proxies=util.proxies, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"civitai images request failed: {e}")
    if not r.ok:
        raise RuntimeError(f"civitai images request failed: HTTP {r.status_code} {r.text[:300]}")
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError("civitai images response is not JSON")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RuntimeError(f"unexpected civitai images response: {str(data)[:300]}")
    return data


def iter_images(params: dict, max_images: int, page_size: int = 100):
    """Yield image records page by page (cursor paging) until max_images."""
    q = dict(params or {})
    q["limit"] = max(1, min(page_size, 200, max_images))
    seen = 0
    cursor = None
    while seen < max_images:
        if cursor:
            q["cursor"] = cursor
        data = search_images(q)
        items = data["items"]
        for img in items:
            if seen >= max_images:
                return
            seen += 1
            yield img
        cursor = (data.get("metadata") or {}).get("nextCursor")
        if not items or not cursor:
            return


def get_images_by_ids(image_ids, nsfw: str = "X") -> dict:
    """{image id: record with meta} for the given civitai image ids.

    Missing / deleted ids are simply absent from the result.
    """
    out = {}
    posts = {}
    for image_id in image_ids:
        try:
            iid = int(image_id)
        except (TypeError, ValueError):
            continue
        if iid in out:
            continue
        try:  # civitai answers HTTP 500 for ids that never existed
            found = search_images({"imageId": iid, "nsfw": nsfw})["items"]
        except RuntimeError as e:
            util.printD(f"image {iid}: {e}")
            continue
        if not found:
            continue
        base = found[0]
        post_id = base.get("postId")
        if post_id and post_id not in posts:
            try:
                posts[post_id] = {img.get("id"): img for img in
                                  search_images({"postId": post_id, "nsfw": nsfw, "limit": 200})["items"]}
            except RuntimeError as e:
                util.printD(f"post {post_id}: {e}")
                posts[post_id] = {}
        full = posts.get(post_id, {}).get(iid)
        out[iid] = full if full else base
    return out

