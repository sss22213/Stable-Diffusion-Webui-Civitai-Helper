# -*- coding: UTF-8 -*-
# This extension can help you manage your models from civitai. It can download preview, add trigger words, open model page and use the prompt from preview image
# repo: https://github.com/butaixianran/



import modules.scripts as scripts
import gradio as gr
import os
import webbrowser
import requests
import random
import hashlib
import json
import shutil
import re
import modules
from modules import script_callbacks
from modules import shared
from scripts.ch_lib import model
from scripts.ch_lib import js_action_civitai
from scripts.ch_lib import model_action_civitai
from scripts.ch_lib import civitai
from scripts.ch_lib import util
from scripts.ch_lib import ch_settings
from scripts.ch_lib import examples
from scripts.ch_lib import api as ch_api


# init

# root path
root_path = os.getcwd()

# extension path
extension_path = scripts.basedir()

model.get_custom_model_folder()


# Setting now can not be saved from extension tab
# All settings now must be saved from setting page.
def on_ui_settings():
    ch_section = ("civitai_helper", "Civitai Helper")
    # MCP token: generate it before the Settings page is built so it shows there
    from scripts.ch_lib import mcp_tools
    mcp_tools.ensure_token()
    # settings
    shared.opts.add_option("ch_max_size_preview", shared.OptionInfo(True, "Download Max Size Preview", gr.Checkbox, {"interactive": True}, section=ch_section))
    shared.opts.add_option("ch_skip_nsfw_preview", shared.OptionInfo(False, "Skip NSFW Preview Images", gr.Checkbox, {"interactive": True}, section=ch_section))
    shared.opts.add_option("ch_open_url_with_js", shared.OptionInfo(True, "Open Url At Client Side", gr.Checkbox, {"interactive": True}, section=ch_section))
    shared.opts.add_option("ch_check_new_ver_exist_in_all_folder", shared.OptionInfo(True, "When checking new model version, check new version existing in all model folders", gr.Checkbox, {"interactive": True}, section=ch_section))
    shared.opts.add_option("ch_proxy", shared.OptionInfo("", "Civitai Helper Proxy", gr.Textbox, {"interactive": True, "lines":1, "info":"format: socks5h://127.0.0.1:port"}, section=ch_section))
    shared.opts.add_option("ch_civiai_api_key", shared.OptionInfo("", "Civitai API Key", gr.Textbox, {"interactive": True, "lines":1, "info":"check doc:https://github.com/zixaphir/Stable-Diffusion-Webui-Civitai-Helper/tree/master#api-key"}, section=ch_section))
    shared.opts.add_option("ch_civitai_domain", shared.OptionInfo(civitai.DEFAULT_DOMAIN, "Civitai Domain", gr.Textbox, {"interactive": True, "lines": 1, "info": "Hostname of the civitai instance to call. Default civitai.com; mirrors like civitai.red use the same /api/v1 paths."}, section=ch_section))
    shared.opts.add_option("ch_mcp_enable", shared.OptionInfo(True, "Serve the MCP endpoint /civitai-helper/mcp (Model Context Protocol, for AI agents)", gr.Checkbox, {"interactive": True}, section=ch_section))
    shared.opts.add_option("ch_mcp_token", shared.OptionInfo("", "MCP access token", gr.Textbox, {"interactive": True, "lines": 1, "info": "Clients send Authorization: Bearer <token>. Generated at startup when empty; the env var CIVITAI_HELPER_MCP_TOKEN overrides it."}, section=ch_section))

def on_ui_tabs():
    # init
    # init_py_msg = {
    #     # relative extension path
    #     "extension_path": util.get_relative_path(extension_path, root_path),
    # }
    # init_py_msg_str = json.dumps(init_py_msg)


    # get prompt textarea
    # check modules/ui.py, search for txt2img_paste_fields
    # Negative prompt is the second element
    txt2img_prompt = modules.ui.txt2img_paste_fields[0][0]
    txt2img_neg_prompt = modules.ui.txt2img_paste_fields[1][0]
    img2img_prompt = modules.ui.img2img_paste_fields[0][0]
    img2img_neg_prompt = modules.ui.img2img_paste_fields[1][0]


    # get settings (shared with the HTTP API, see ch_lib/ch_settings.py);
    # this also applies the api key / proxy / civitai domain globally.
    ch = ch_settings.load(verbose=True)
    max_size_preview = ch["max_size_preview"]
    skip_nsfw_preview = ch["skip_nsfw_preview"]
    open_url_with_js = ch["open_url_with_js"]
    check_new_ver_exist_in_all_folder = ch["check_new_ver_exist_in_all_folder"]


    # ====Event's function====
    def scan_model(scan_model_types):
        return model_action_civitai.scan_model(scan_model_types, max_size_preview, skip_nsfw_preview)
    
    def get_model_info_by_input(model_type_drop, model_name_drop, model_url_or_id_txtbox):
        return model_action_civitai.get_model_info_by_input(model_type_drop, model_name_drop, model_url_or_id_txtbox, max_size_preview, skip_nsfw_preview)

    def dl_model_by_input(dl_model_info, dl_model_type_txtbox, dl_subfolder_drop, dl_version_drop, dl_all_ckb):
        return model_action_civitai.dl_model_by_input(dl_model_info, dl_model_type_txtbox, dl_subfolder_drop, dl_version_drop, dl_all_ckb, max_size_preview, skip_nsfw_preview)

    def check_models_new_version_to_md(model_types):
        return model_action_civitai.check_models_new_version_to_md(model_types, check_new_ver_exist_in_all_folder)

    def open_model_url(js_msg_txtbox):
        return js_action_civitai.open_model_url(js_msg_txtbox, open_url_with_js)

    def dl_model_new_version(js_msg_txtbox, max_size_preview):
        return js_action_civitai.dl_model_new_version(js_msg_txtbox, max_size_preview, skip_nsfw_preview)

    # ---- examples / card info (same helpers as the HTTP API, see ch_lib/examples.py) ----
    def _md_report(r):
        msg = r.get("message", "")
        extra = []
        for key, label in (("failed_models", "Failed"), ("models_with_failures", "Models with failed images"),
                           ("bad_json_models", "Skipped (invalid .json)")):
            items = r.get(key) or []
            if items:
                shown = "<br>".join(items[:30]) + ("<br>…" if len(items) > 30 else "")
                extra.append(f"**{label} ({len(items)}):**<br>{shown}")
        return msg + ("<br><br>" + "<br><br>".join(extra) if extra else "")

    def fetch_previews(model_types):
        if not model_types:
            return "Model Types is empty"
        s = ch_settings.load()
        return _md_report(examples.run_fetch_previews(model_types, None, s["max_size_preview"], s["skip_nsfw_preview"]))

    def write_card_info(model_types, overwrite):
        if not model_types:
            return "Model Types is empty"
        return _md_report(examples.run_write_card_info(model_types, None, overwrite, True))

    def download_examples(model_types, max_images, overwrite):
        if not model_types:
            return "Model Types is empty"
        s = ch_settings.load()
        return _md_report(examples.run_download_examples(
            model_types, None, int(max_images or 0), s["skip_nsfw_preview"], overwrite, s["max_size_preview"]))


    # ---- local images as examples (same helper as POST /add-local-examples) ----
    def _local_model_choices(model_type):
        from scripts.ch_lib import local_models
        return [util.get_relative_path(path, folder).replace("\\", "/")
                for folder, _root, path, _filename in local_models.iter_models(model_type)]

    def local_example_type_changed(model_type):
        return gr.update(choices=_local_model_choices(model_type), value=[])

    def local_example_files_changed(files, model_type, current):
        """Pick the models the uploaded images were made with (LoRAs in the prompt / the checkpoint)."""
        picked = list(current or [])
        info = []
        for f in files or []:
            fp = f if isinstance(f, str) else getattr(f, "name", "")
            try:
                with open(fp, "rb") as fh:
                    meta = examples.read_image_info(fh.read())["meta"]
            except (OSError, ValueError) as e:
                info.append(f"{os.path.basename(fp)}: {e}")
                continue
            names = examples.models_in_image(model_type, meta)
            picked += [n for n in names if n not in picked]
            info.append(f"{os.path.basename(fp)}: " + ("prompt found" if meta.get("prompt") else "no generation info")
                        + (f", models: {', '.join(names)}" if names else ""))
        return gr.update(value=picked), "<br>".join(info)

    def add_local_examples(files, model_type, model_names, nsfw):
        from scripts.ch_lib import local_models
        if not files:
            return "Add one or more images first"
        if not model_names:
            return "Pick the model(s) the images should be examples of"
        images = []
        for f in files:
            fp = f if isinstance(f, str) else getattr(f, "name", "")
            with open(fp, "rb") as fh:
                images.append({"data": fh.read(), "file_name": os.path.basename(fp), "nsfw": bool(nsfw)})
        lines = []
        for name in model_names:
            found = local_models.find(model_type, name)
            if not found:
                lines.append(f"**{name}**: not found")
                continue
            r = examples.add_local_examples(found[2], images)
            numbers = ", ".join(f"#{i['index']}" for i in r["images"] if i.get("index"))
            errors = "; ".join(f"{i.get('file_name')}: {i.get('error')}" for i in r["images"] if i.get("status") == "failed")
            lines.append(f"**{name}**: {r['added']} added, {r['existed']} already there"
                         + (f" ({numbers})" if numbers else "") + (f", failed: {errors}" if errors else ""))
        return "<br>".join(lines) + "<br>Open the model's examples (or run Write Example Prompt to Cards) to see them."

    # ---- file names vs civitai (same helpers as GET /check-names, POST /fix-names) ----
    _ACTION_LABEL = {"rename": "rename to", "merge": "merge into", "conflict": "check by hand"}

    def check_names(model_types):
        from scripts.ch_lib import naming
        if not model_types:
            return "Model Types is empty"
        r = naming.scan(model_types)
        if not r["items"]:
            return "All file names match civitai's (no _2 / _3 copies, no duplicate versions)."
        rows = ["| Model | Civitai file | Action | Target | Why |", "|---|---|---|---|---|"]
        for it in r["items"]:
            rows.append(f"| {it['type']}: `{it['name']}` | `{it['civitai_file']}` | {_ACTION_LABEL[it['action']]} | "
                        f"`{it['target']}` | {it['reason']}" + (f"; {it['added_examples']} added examples" if it["added_examples"] else "") + " |")
        c = r["counts"]
        return (f"{c['rename']} to rename, {c['merge']} duplicate copies to merge, {c['conflict']} to check by hand.\n\n"
                + "\n".join(rows))

    def fix_names(model_types, merge):
        from scripts.ch_lib import naming
        if not model_types:
            return "Model Types is empty"
        r = naming.fix(model_types, None, bool(merge), rename_model=lambda t, path, new: ch_api.rename_model(t, path, new))
        lines = [f"**{len(r['results'])} fixed**, {len(r['skipped'])} skipped."]
        for it in r["results"]:
            lines.append(f"- {it['type']}: `{it['name']}` → {_ACTION_LABEL[it['action']]} `{it['target']}`")
        for it in r["skipped"]:
            lines.append(f"- skipped `{it['name']}`: {it['reason']}")
        if r["mapping"]:
            lines.append("Prompts that use the old names need the new ones: "
                         + ", ".join(f"`{a}` → `{b}`" for a, b in r["mapping"].items())
                         + ". Click Refresh in the Extra Networks tab.")
        return "<br>".join(lines)

    def get_model_names_by_input(model_type, empty_info_only):
        names = civitai.get_model_names_by_input(model_type, empty_info_only)
        # Component.update(...) in Gradio 4.x returns the component itself
        # (whose internal state holds a _thread.lock), and gradio's queue
        # deep-copies the handler response — boom. gr.update returns a
        # plain dict instead.
        return gr.update(choices=names)

    def get_model_info_by_url(url):
        r = model_action_civitai.get_model_info_by_url(url)

        model_info = {}
        model_name = ""
        model_type = ""
        subfolders = []
        version_strs = []
        if r:
            model_info, model_name, model_type, subfolders, version_strs = r

        return [model_info, model_name, model_type, gr.update(choices=subfolders), gr.update(choices=version_strs)]

    # ====UI====
    with gr.Blocks(analytics_enabled=False) as civitai_helper:

        model_types = list(model.folders.keys())
        no_info_model_names = civitai.get_model_names_by_input("ckp", False)

        # session data
        dl_model_info = gr.State({})



        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Scan Models for Civitai")
                with gr.Row():
                    scan_model_types_ckbg = gr.CheckboxGroup(choices=model_types, label="Model Types", value=model_types)

                # with gr.Row():
                scan_model_civitai_btn = gr.Button(value="Scan", variant="primary", elem_id="ch_scan_model_civitai_btn")
                # with gr.Row():
                scan_model_log_md = gr.Markdown(value="Scanning takes time, just wait. Check console log for detail", elem_id="ch_scan_model_log_md")

        
        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Get Model Info from Civitai by URL")
                gr.Markdown("Use this when scanning can not find a local model on civitai")
                with gr.Row():
                    model_type_drop = gr.Dropdown(choices=model_types, label="Model Type", value="ckp", multiselect=False)
                    empty_info_only_ckb = gr.Checkbox(label="Only Show Models have no Info", value=False, elem_id="ch_empty_info_only_ckb", elem_classes="ch_vpadding")
                    model_name_drop = gr.Dropdown(choices=no_info_model_names, label="Model", value="ckp", multiselect=False)

                model_url_or_id_txtbox = gr.Textbox(label="Civitai URL", lines=1, value="")
                get_civitai_model_info_by_id_btn = gr.Button(value="Get Model Info from Civitai", variant="primary")
                get_model_by_id_log_md = gr.Markdown("")

        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Download Model")
                with gr.Row():
                    dl_model_url_or_id_txtbox = gr.Textbox(label="Civitai URL", lines=1, value="")
                    dl_model_info_btn = gr.Button(value="1. Get Model Info by Civitai Url", variant="primary")

                gr.Markdown(value="2. Pick Subfolder and Model Version")
                with gr.Row():
                    dl_model_name_txtbox = gr.Textbox(label="Model Name", interactive=False, lines=1, value="")
                    dl_model_type_txtbox = gr.Textbox(label="Model Type", interactive=False, lines=1, value="")
                    dl_subfolder_drop = gr.Dropdown(choices=[], label="Sub-folder", value="", interactive=True, multiselect=False)
                    dl_version_drop = gr.Dropdown(choices=[], label="Model Version", value="", interactive=True, multiselect=False)
                    dl_all_ckb = gr.Checkbox(label="Download All files", value=False, elem_id="ch_dl_all_ckb", elem_classes="ch_vpadding")
                
                dl_civitai_model_by_id_btn = gr.Button(value="3. Download Model", variant="primary")
                dl_log_md = gr.Markdown(value="Check Console log for Downloading Status")

        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Check models' new version")
                with gr.Row():
                    model_types_ckbg = gr.CheckboxGroup(choices=model_types, label="Model Types", value=["lora"])
                    check_models_new_version_btn = gr.Button(value="Check New Version from Civitai", variant="primary")

                check_models_new_version_log_md = gr.HTML("It takes time, just wait. Check console log for detail")

        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Example Images & Card Info")
                gr.Markdown("Uses the example images / prompts already stored in each model's `.civitai.info` (run Scan first). Everything here is also available as HTTP API under `/civitai-helper/v1` (see `/docs`).")
                with gr.Row():
                    ex_model_types_ckbg = gr.CheckboxGroup(choices=model_types, label="Model Types", value=["lora"])

                with gr.Row():
                    ex_fetch_previews_btn = gr.Button(value="Fetch Missing Previews", variant="primary")
                    gr.Markdown("Re-downloads `.preview.png` for models that have none (tries every example image, no civitai API call).", elem_classes="ch_vpadding")
                ex_fetch_previews_log_md = gr.Markdown("")

                with gr.Row():
                    ex_card_overwrite_ckb = gr.Checkbox(label="Overwrite existing description / notes", value=False, elem_classes="ch_vpadding")
                    ex_write_card_info_btn = gr.Button(value="Write Example Prompt to Cards", variant="primary")
                gr.Markdown("Writes trigger words + first example prompt into the card's `description`, all example prompts into `notes` (`<model>.json`). Click Refresh in the Extra Networks tab afterwards.")
                ex_write_card_info_log_md = gr.Markdown("")

                with gr.Row():
                    ex_max_images_num = gr.Number(label="Max images per model (0 = all)", value=0, precision=0, minimum=0, maximum=100)
                    ex_dl_overwrite_ckb = gr.Checkbox(label="Re-download existing", value=False, elem_classes="ch_vpadding")
                    ex_download_examples_btn = gr.Button(value="Download Example Images", variant="primary")
                gr.Markdown("Saves example images next to the model as `<model>.example_NN.<ext>`; NN matches the order on civitai so each file maps to its prompt. Honors the 'Skip NSFW Preview Images' setting. Takes a while for many models; check console log.")
                ex_download_examples_log_md = gr.Markdown("")

        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Add Local Images as Examples")
                gr.Markdown("Keep pictures you generated as example images of a model (`<model>.example_101+.<ext>`, the file is kept as it is). The prompt and parameters are read from the image's generation info; the LoRAs in its prompt are picked automatically. Also available as `POST /civitai-helper/v1/add-local-examples` and the MCP tool `civitai_add_local_examples`.")
                with gr.Row():
                    lx_files = gr.File(label="Images (PNG / JPEG / WebP)", file_count="multiple", file_types=["image"], type="filepath")
                    with gr.Column():
                        lx_type_drop = gr.Dropdown(choices=model_types, label="Model Type", value="lora", multiselect=False)
                        lx_models_drop = gr.Dropdown(choices=_local_model_choices("lora"), label="Models", value=[], multiselect=True, interactive=True)
                        lx_nsfw_ckb = gr.Checkbox(label="Mark as NSFW", value=False)
                lx_info_md = gr.Markdown("")
                lx_add_btn = gr.Button(value="Add as Example Images", variant="primary")
                lx_log_md = gr.Markdown("")

        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Check Model File Names")
                gr.Markdown("Finds models whose file name is civitai's name plus `_2` / `_3` (a version downloaded twice, or a new version saved next to the old one) and duplicate copies of the same version. Fixing renames them back (every file moves along, the model's name in its example prompts is updated) and merges duplicates into one copy (added example images move over, the other copy is deleted). Also `GET /check-names`, `POST /fix-names`.")
                with gr.Row():
                    nm_types_ckbg = gr.CheckboxGroup(choices=model_types, label="Model Types", value=["lora"])
                    nm_merge_ckb = gr.Checkbox(label="Also merge duplicate copies (deletes the extra copy)", value=True, elem_classes="ch_vpadding")
                with gr.Row():
                    nm_check_btn = gr.Button(value="Check File Names", variant="primary")
                    nm_fix_btn = gr.Button(value="Fix File Names")
                nm_log_md = gr.Markdown("")

        with gr.Box(elem_classes="ch_box"):
            with gr.Column():
                gr.Markdown("### Other")
                # save_setting_btn = gr.Button(value="Save Setting")
                gr.Markdown(value="Settings are moved into Settings Tab->Civitai Helper section")


        # ====Footer====
        gr.Markdown(f"<center>version:{util.version}</center>")

        # ====hidden component for js, not in any tab====
        js_msg_txtbox = gr.Textbox(label="Request Msg From Js", visible=False, lines=1, value="", elem_id="ch_js_msg_txtbox")
        py_msg_txtbox = gr.Textbox(label="Response Msg From Python", visible=False, lines=1, value="", elem_id="ch_py_msg_txtbox")

        js_open_url_btn = gr.Button(value="Open Model Url", visible=False, elem_id="ch_js_open_url_btn")
        js_add_trigger_words_btn = gr.Button(value="Add Trigger Words", visible=False, elem_id="ch_js_add_trigger_words_btn")
        js_use_preview_prompt_btn = gr.Button(value="Use Prompt from Preview Image", visible=False, elem_id="ch_js_use_preview_prompt_btn")
        js_dl_model_new_version_btn = gr.Button(value="Download Model's new version", visible=False, elem_id="ch_js_dl_model_new_version_btn")
        js_remove_card_btn = gr.Button(value="Remove Card", visible=False, elem_id="ch_js_remove_card_btn")

        # ====events====
        # Scan Models for Civitai
        scan_model_civitai_btn.click(scan_model, inputs=[scan_model_types_ckbg], outputs=scan_model_log_md)

        # Get Civitai Model Info by Model Page URL
        model_type_drop.change(get_model_names_by_input, inputs=[model_type_drop, empty_info_only_ckb], outputs=model_name_drop)
        empty_info_only_ckb.change(get_model_names_by_input, inputs=[model_type_drop, empty_info_only_ckb], outputs=model_name_drop)

        get_civitai_model_info_by_id_btn.click(get_model_info_by_input, inputs=[model_type_drop, model_name_drop, model_url_or_id_txtbox], outputs=get_model_by_id_log_md)

        # Download Model
        dl_model_info_btn.click(get_model_info_by_url, inputs=dl_model_url_or_id_txtbox, outputs=[dl_model_info, dl_model_name_txtbox, dl_model_type_txtbox, dl_subfolder_drop, dl_version_drop])
        dl_civitai_model_by_id_btn.click(dl_model_by_input, inputs=[dl_model_info, dl_model_type_txtbox, dl_subfolder_drop, dl_version_drop, dl_all_ckb], outputs=dl_log_md)

        # Check models' new version
        check_models_new_version_btn.click(check_models_new_version_to_md, inputs=model_types_ckbg, outputs=check_models_new_version_log_md)

        # Example images & card info
        ex_fetch_previews_btn.click(fetch_previews, inputs=[ex_model_types_ckbg], outputs=ex_fetch_previews_log_md)
        ex_write_card_info_btn.click(write_card_info, inputs=[ex_model_types_ckbg, ex_card_overwrite_ckb], outputs=ex_write_card_info_log_md)
        ex_download_examples_btn.click(download_examples, inputs=[ex_model_types_ckbg, ex_max_images_num, ex_dl_overwrite_ckb], outputs=ex_download_examples_log_md)

        # File names
        nm_check_btn.click(check_names, inputs=[nm_types_ckbg], outputs=nm_log_md)
        nm_fix_btn.click(fix_names, inputs=[nm_types_ckbg, nm_merge_ckb], outputs=nm_log_md)

        # Local images as examples
        lx_type_drop.change(local_example_type_changed, inputs=[lx_type_drop], outputs=lx_models_drop)
        lx_files.change(local_example_files_changed, inputs=[lx_files, lx_type_drop, lx_models_drop], outputs=[lx_models_drop, lx_info_md])
        lx_add_btn.click(add_local_examples, inputs=[lx_files, lx_type_drop, lx_models_drop, lx_nsfw_ckb], outputs=lx_log_md)

        # js action
        js_open_url_btn.click(open_model_url, inputs=[js_msg_txtbox], outputs=py_msg_txtbox)
        js_add_trigger_words_btn.click(js_action_civitai.add_trigger_words, inputs=[js_msg_txtbox], outputs=[txt2img_prompt, img2img_prompt])
        js_use_preview_prompt_btn.click(js_action_civitai.use_preview_image_prompt, inputs=[js_msg_txtbox], outputs=[txt2img_prompt, txt2img_neg_prompt, img2img_prompt, img2img_neg_prompt])
        js_dl_model_new_version_btn.click(dl_model_new_version, inputs=[js_msg_txtbox], outputs=dl_log_md)
        js_remove_card_btn.click(js_action_civitai.remove_model_by_path, inputs=[js_msg_txtbox], outputs=py_msg_txtbox)

    # the third parameter is the element id on html, with a "tab_" as prefix
    return (civitai_helper , "Civitai Helper", "civitai_helper"),





script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_ui_tabs(on_ui_tabs)
# HTTP API under /civitai-helper/v1 (extension-only, see ch_lib/api.py)
script_callbacks.on_app_started(ch_api.on_app_started)



