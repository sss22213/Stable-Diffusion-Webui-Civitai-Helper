import os
import shlex
import subprocess
import requests

from scripts.ch_lib import util

def dl(url, folder, filename, filepath):
    util.printD("Start downloading (wget) from: " + url)

    # ===== 決定目標路徑 =====
    if filepath:
        file_path = filepath
    else:
        if not folder or not os.path.isdir(folder):
            util.printD("Invalid folder")
            return None

        if filename:
            file_path = os.path.join(folder, filename)
        else:
            # 先用 requests 嘗試從 header 拿檔名（可有可無）
            rh = requests.get(url, stream=True, verify=False, headers=util.def_headers, proxies=util.proxies)
            cd = rh.headers.get("Content-Disposition", "")
            if cd:
                cd = cd.encode("latin1").decode("utf-8", errors="ignore")
                try:
                    filename = cd.split("filename=")[1].strip().strip('"')
                    file_path = os.path.join(folder, filename)
                except Exception:
                    file_path = ""
            else:
                file_path = ""

            # 如果還是拿不到檔名，就先留空，稍後用 Location 的 query 解析也行
            if not file_path:
                util.printD("Can not get filename from header, will still download to folder with a temp name.")
                file_path = os.path.join(folder, "civitai_model_download.bin")

    util.printD("Target file path: " + file_path)

    # 避免重名
    base, ext = os.path.splitext(file_path)
    count = 2
    final_path = file_path
    while os.path.exists(final_path):
        final_path = f"{base}_{count}{ext}"
        count += 1

    tmp_path = final_path + ".part"
    util.printD("Downloading to temp file: " + tmp_path)

    # ===== 第一步：向 civitai 取得 307 的 Location（必須帶 Bearer）=====
    headers = dict(util.def_headers or {})
    if util.civitai_api_key:
        headers["Authorization"] = f"Bearer {util.civitai_api_key}"

    r = requests.get(
        url,
        allow_redirects=False,   # ★ 不跟轉址，自己拿 Location
        verify=False,
        headers=headers,
        proxies=util.proxies
    )

    if r.status_code not in (301, 302, 303, 307, 308):
        util.printD(f"Unexpected status: {r.status_code}")
        util.printD(f"Body: {r.text[:300]}")
        util.printD("This model may require API key or the endpoint changed.")
        return None

    location = r.headers.get("Location")
    if not location:
        util.printD("No Location header found from civitai redirect response.")
        return None

    util.printD("Redirect location obtained (download URL).")

    # ===== 第二步：用 wget 下載 Location（★ 不帶 Authorization，避免 R2 400）=====
    cmd = [
        "wget",
        "-c",
        "--progress=bar:force",
        "--no-check-certificate",
        "-O", tmp_path,
        location
    ]

    # User-Agent
    ua = (util.def_headers or {}).get("User-Agent")
    if ua:
        cmd.extend(["--user-agent", ua])

    # Proxy：用環境變數給 wget
    env = os.environ.copy()
    if util.proxies:
        if "http" in util.proxies:
            env["http_proxy"] = util.proxies["http"]
        if "https" in util.proxies:
            env["https_proxy"] = util.proxies["https"]

    util.printD("Running command:")
    util.printD(" ".join(shlex.quote(c) for c in cmd))

    proc = subprocess.run(cmd, env=env)

    if proc.returncode != 0:
        util.printD("wget download failed")
        return None

    # 下載完成：rename
    os.rename(tmp_path, final_path)
    util.printD(f"File Downloaded to: {final_path}")
    return final_path