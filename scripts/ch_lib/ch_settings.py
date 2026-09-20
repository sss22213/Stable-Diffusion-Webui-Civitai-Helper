# -*- coding: UTF-8 -*-
"""Read Civitai Helper options from the WebUI Settings tab and apply the
side effects (API key header, proxy, civitai domain) to the ch_lib modules.

Shared by the Gradio tab (civitai_helper.py) and the HTTP API (api.py) so
both see the same values. Call load() right before doing work: it is cheap
and picks up changes made in Settings without a restart.
"""
from modules import shared

from . import util
from . import civitai


DEFAULTS = {
    "max_size_preview": True,
    "skip_nsfw_preview": False,
    "open_url_with_js": True,
    "check_new_ver_exist_in_all_folder": False,
    "proxy": "",
    "civitai_api_key": "",
    "civitai_domain": civitai.DEFAULT_DOMAIN,
}


def load(verbose: bool = False) -> dict:
    """Return the current settings as a dict and apply them globally."""
    opts = shared.opts.data
    s = {
        "max_size_preview": opts.get("ch_max_size_preview", DEFAULTS["max_size_preview"]),
        "skip_nsfw_preview": opts.get("ch_skip_nsfw_preview", DEFAULTS["skip_nsfw_preview"]),
        "open_url_with_js": opts.get("ch_open_url_with_js", DEFAULTS["open_url_with_js"]),
        "check_new_ver_exist_in_all_folder": opts.get(
            "ch_check_new_ver_exist_in_all_folder", DEFAULTS["check_new_ver_exist_in_all_folder"]
        ),
        "proxy": opts.get("ch_proxy", DEFAULTS["proxy"]) or "",
        "civitai_api_key": opts.get("ch_civiai_api_key", DEFAULTS["civitai_api_key"]) or "",
        "civitai_domain": opts.get("ch_civitai_domain", DEFAULTS["civitai_domain"]) or civitai.DEFAULT_DOMAIN,
    }

    # Repoint the API URL dict before any request goes out.
    civitai.apply_domain(s["civitai_domain"])

    # API key -> Authorization header used by every civitai request.
    util.civitai_api_key = s["civitai_api_key"]
    util.def_headers["Authorization"] = (
        f"Bearer {s['civitai_api_key']}" if s["civitai_api_key"] else ""
    )

    # Proxy for requests (wget gets it via env in downloader.py).
    util.proxies = {"http": s["proxy"], "https": s["proxy"]} if s["proxy"] else None

    if verbose:
        util.printD("Settings:")
        for k in ("max_size_preview", "skip_nsfw_preview", "open_url_with_js",
                  "check_new_ver_exist_in_all_folder", "proxy", "civitai_domain"):
            util.printD(f"{k}: {s[k]}")
        util.printD(f"use civitai api key: {bool(s['civitai_api_key'])}")

    return s


def public(s: dict) -> dict:
    """Settings safe to expose over HTTP (no API key, only whether one is set)."""
    out = {k: v for k, v in s.items() if k != "civitai_api_key"}
    out["has_api_key"] = bool(s.get("civitai_api_key"))
    return out
