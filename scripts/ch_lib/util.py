# -*- coding: UTF-8 -*-
import os
import io
import hashlib
import requests
import shutil


version = "1.13.0"

def_headers = {'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 12_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
               "Authorization": ""}


proxies = None

civitai_api_key = ""


# print for debugging
def printD(msg):
    print(f"Civitai Helper: {msg}")


def read_chunks(file, size=io.DEFAULT_BUFFER_SIZE):
    """Yield pieces of data from a file-like object until EOF."""
    while True:
        chunk = file.read(size)
        if not chunk:
            break
        yield chunk

# Now, hashing use the same way as pip's source code.
def gen_file_sha256(filname):
    printD("Use Memory Optimized SHA256")
    blocksize=1 << 20
    h = hashlib.sha256()
    length = 0
    with open(os.path.realpath(filname), 'rb') as f:
        for block in read_chunks(f, size=blocksize):
            length += len(block)
            h.update(block)

    hash_value =  h.hexdigest()
    printD("sha256: " + hash_value)
    printD("length: " + str(length))
    return hash_value



# download a file (preview / example image) to path.
# writes to a temp file first so a failed download never leaves a broken image behind.
# return: True on success, False on failure
def download_file(url, path, timeout=(15, 120)) -> bool:
    printD("Downloading file from: " + url)
    real_path = os.path.realpath(path)
    tmp_path = real_path + ".part"
    try:
        r = requests.get(url, stream=True, headers=def_headers, proxies=proxies, timeout=timeout)
    except requests.RequestException as e:
        printD(f"Download failed: {e}")
        return False

    if not r.ok:
        printD("Get error code: " + str(r.status_code))
        printD(r.text[:300])
        return False

    try:
        with open(tmp_path, 'wb') as f:
            r.raw.decode_content = True
            shutil.copyfileobj(r.raw, f)
        if os.path.getsize(tmp_path) == 0:
            raise IOError("empty response body")
        os.replace(tmp_path, real_path)
    except Exception as e:
        printD(f"Download failed while writing {path}: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False

    printD("File downloaded to: " + path)
    return True

# get subfolder list
def get_subfolders(folder:str) -> list:
    printD("Get subfolder for: " + folder)
    if not folder:
        printD("folder can not be None")
        return
    
    if not os.path.isdir(folder):
        printD("path is not a folder")
        return
    
    prefix_len = len(folder)
    subfolders = []
    for root, dirs, files in os.walk(folder, followlinks=True):
        for dir in dirs:
            full_dir_path = os.path.join(root, dir)
            # get subfolder path from it
            subfolder = full_dir_path[prefix_len:]
            subfolders.append(subfolder)

    return subfolders


# get relative path
def get_relative_path(item_path:str, parent_path:str) -> str:
    # printD("item_path:"+item_path)
    # printD("parent_path:"+parent_path)
    # item path must start with parent_path
    if not item_path:
        return ""
    if not parent_path:
        return ""
    if not item_path.startswith(parent_path):
        return item_path

    relative = item_path[len(parent_path):]
    if relative[:1] == "/" or relative[:1] == "\\":
        relative = relative[1:]

    # printD("relative:"+relative)
    return relative