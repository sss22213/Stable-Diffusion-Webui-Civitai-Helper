# -*- coding: UTF-8 -*-
"""MCP tools of Civitai Helper: one tool per HTTP API route (see api.py).

Every tool calls the extension's own /civitai-helper/v1 routes in-process (httpx ASGITransport
on the WebUI's FastAPI app), so it behaves exactly like the REST API. Long-running work
(scan, download, previews, examples, card info) passes wait=true by default and sends
progress notifications while the task runs.

TOOLS: name, description, JSON-schema parameters, the HTTP method and path under PREFIX
({type} / {task_id} are filled from the arguments), default arguments and timeout.
"""
import asyncio
import json
import os
import re
import secrets
from typing import Any
from urllib.parse import quote

import httpx

from .mcp_server import Context, McpServer, ToolError

PREFIX = "/civitai-helper/v1"

TOOLS = json.loads(r"""
[
  {
    "name": "civitai_lora_inventory",
    "description": "Inventory of the LoRAs installed in the local Stable Diffusion WebUI. Each item has: name (file), prompt_tag (ready-to-use <lora:name:1>), base (base-model family: Illustrious, NoobAI, Pony, SDXL, SD 1.5, Flux, ... or unknown), category (civitai content category: character, celebrity, clothing, poses, action, style, concept, background, buildings, vehicle, objects, animal, tool, assets, other, or uncategorized), civitai info when known (model_name, version, trained_words = trigger words, nsfw, model_url) and training_tags read from the file itself. Filter with category and base to look in the right group first, and with q for file name, model name, trigger word or training tag. Every answer also counts the matching LoRAs per category and per base, so a call without category shows where to look. Use it for 'what LoRAs do I have', 'which LoRA fits X', or to pick a LoRA and its trigger words for a prompt. Results are paged: narrow with category / base / q, or use offset to see more.",
    "method": "GET",
    "path": "/loras",
    "parameters": {
      "type": "object",
      "properties": {
        "category": {
          "type": "string",
          "enum": [
            "character",
            "celebrity",
            "clothing",
            "poses",
            "action",
            "style",
            "concept",
            "background",
            "buildings",
            "vehicle",
            "objects",
            "animal",
            "tool",
            "assets",
            "other",
            "uncategorized"
          ],
          "description": "Optional content category: character (a specific character), celebrity (a real person), clothing (outfits, accessories), poses, action, style (art style / look), concept (situations, effects, body features), background (scenery), buildings, vehicle, objects, animal, tool (sliders, detail / quality helpers), assets; other = on civitai but none of these; uncategorized = not on civitai or category not fetched yet"
        },
        "base": {
          "type": "string",
          "description": "Optional base-model family the LoRA must be made for, e.g. Illustrious, NoobAI, Pony, SDXL, SD 1.5, Flux. Match the checkpoint in use (an Illustrious checkpoint -> Illustrious)."
        },
        "q": {
          "type": "string",
          "description": "Optional case-insensitive substring filter (file name, model name, trigger word or training tag)"
        },
        "limit": {
          "type": "integer",
          "description": "Max items to return (default 15; keep it small, entries are ~600 chars each)"
        },
        "offset": {
          "type": "integer",
          "description": "Skip this many items (for paging)"
        }
      }
    },
    "defaults": {
      "limit": 15,
      "metadata": true,
      "compact": true
    },
    "timeout": 180
  },
  {
    "name": "civitai_list_local_models",
    "description": "List models installed in the local Stable Diffusion WebUI of one type (lora, ckp = checkpoint, ti = textual-inversion embedding, hyper = hypernetwork), with civitai info when known: model name, base model and trained_words (the trigger words). Optional q filters by file name, model name or trigger word. For LoRAs prefer civitai_lora_inventory (richer); use this for checkpoints, embeddings and hypernetworks, or for a compact LoRA list.",
    "method": "GET",
    "path": "/models",
    "parameters": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "q": {
          "type": "string",
          "description": "Optional case-insensitive substring filter on file name, model name or trigger word"
        },
        "no_info_only": {
          "type": "boolean",
          "description": "Only models that have no civitai info file yet"
        },
        "limit": {
          "type": "integer",
          "description": "Max items to return (default 50)"
        }
      },
      "required": [
        "type"
      ]
    },
    "defaults": {
      "limit": 50
    }
  },
  {
    "name": "civitai_local_model_info",
    "description": "Civitai details stored for ONE installed model: model/version ids, model name, base model, trigger words, whether a preview exists, how many example images are saved locally (examples) and whether the card has description info (has_card_info). Give the file name (e.g. foo.safetensors) or the path relative to the model folder. For the example prompts / images themselves use civitai_model_examples.",
    "method": "GET",
    "path": "/models/{type}/info",
    "parameters": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type"
        },
        "name": {
          "type": "string",
          "description": "File name or relative path inside the model folder"
        }
      },
      "required": [
        "type",
        "name"
      ]
    }
  },
  {
    "name": "civitai_model_examples",
    "description": "The civitai EXAMPLE images of ONE installed model, each with its generation parameters: prompt, negative_prompt, steps, sampler, cfg_scale, seed, size, model (checkpoint used), nsfw flag, the civitai image url, and local_file / local_url when the image was saved locally with civitai_download_examples. Also returns trained_words, base_model, downloaded/total counts, has_preview and has_card_info. Use it when the user asks for example prompts, sample images, 'how do I use this LoRA', 'show me what it looks like', or wants a proven prompt to start from.",
    "method": "GET",
    "path": "/models/{type}/examples",
    "parameters": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "name": {
          "type": "string",
          "description": "File name (e.g. foo.safetensors) or path relative to the model folder"
        }
      },
      "required": [
        "type",
        "name"
      ]
    }
  },
  {
    "name": "civitai_lookup_model",
    "description": "Look up a model on civitai by its model id or model page URL (https://civitai.com/models/12345/...). Returns name, type, nsfw flag, creator, description, the target model folder with its existing subfolders, and every version (id, name, base model, trigger words, files). Versions are listed newest first. Use it before downloading, or when the user pastes a civitai link.",
    "method": "GET",
    "path": "/model-info",
    "parameters": {
      "type": "object",
      "properties": {
        "url_or_id": {
          "type": "string",
          "description": "civitai model id or model page URL"
        }
      },
      "required": [
        "url_or_id"
      ]
    }
  },
  {
    "name": "civitai_download_model",
    "description": "Download a civitai model version into the WebUI's model folder for its type (LoRA, checkpoint, embedding or hypernetwork), then save its civitai info file and preview image. Defaults to the newest version and the folder root. The call waits up to ~9 minutes; larger checkpoints may still be running afterwards.",
    "method": "POST",
    "path": "/download",
    "parameters": {
      "type": "object",
      "properties": {
        "url_or_id": {
          "type": "string",
          "description": "civitai model id or model page URL"
        },
        "version_id": {
          "type": "integer",
          "description": "Version id to download (default: newest)"
        },
        "version": {
          "type": "string",
          "description": "Version name, alternative to version_id"
        },
        "subfolder": {
          "type": "string",
          "description": "Subfolder under the model type folder, e.g. '/' (default) or '/characters'"
        },
        "create_subfolder": {
          "type": "boolean",
          "description": "Create the subfolder if it does not exist"
        },
        "dl_all": {
          "type": "boolean",
          "description": "Download every file of the version instead of only the primary one"
        }
      },
      "required": [
        "url_or_id"
      ]
    },
    "defaults": {
      "wait": true,
      "timeout": 540
    },
    "timeout": 580
  },
  {
    "name": "civitai_scan_models",
    "description": "Scan the local model folders of the given types and fetch the missing civitai info files and preview images (matches files by SHA256 against civitai). Use when the user wants trigger words / previews for models that lack them, or after copying models in manually. Can take minutes for many models.",
    "method": "POST",
    "path": "/scan",
    "parameters": {
      "type": "object",
      "properties": {
        "model_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "lora",
              "ckp",
              "ti",
              "hyper"
            ]
          },
          "description": "Model types to scan"
        }
      },
      "required": [
        "model_types"
      ]
    },
    "defaults": {
      "wait": true,
      "timeout": 540
    },
    "timeout": 580
  },
  {
    "name": "civitai_check_new_versions",
    "description": "Check which installed models of the given types have a newer version on civitai. Returns, per model, the new version name, description, download URL and preview URL. Can take minutes for many models.",
    "method": "POST",
    "path": "/check-new-version",
    "parameters": {
      "type": "object",
      "properties": {
        "model_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "lora",
              "ckp",
              "ti",
              "hyper"
            ]
          },
          "description": "Model types to check"
        }
      },
      "required": [
        "model_types"
      ]
    },
    "defaults": {
      "wait": true,
      "timeout": 540
    },
    "timeout": 580
  },
  {
    "name": "civitai_fetch_previews",
    "description": "Download the missing card preview image (.preview.png) for installed models that have none, using the example images already stored in their civitai info (tries every example image; no civitai API lookup, so it is fast and safe to run on all models). Batch mode: model_types. Single model: type + name. Use when cards show no picture, after civitai_scan_models reported failures, or when the user asks to fix / restore previews.",
    "method": "POST",
    "path": "/fetch-previews",
    "parameters": {
      "type": "object",
      "properties": {
        "model_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "lora",
              "ckp",
              "ti",
              "hyper"
            ]
          },
          "description": "Batch mode: model types to process (omit when giving type + name)"
        },
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "name": {
          "type": "string",
          "description": "Single model mode: file name (e.g. foo.safetensors) or path relative to the model folder"
        }
      }
    },
    "defaults": {
      "wait": true,
      "timeout": 540
    },
    "timeout": 580
  },
  {
    "name": "civitai_download_examples",
    "description": "Save the civitai example images of installed models next to the model file as <model>.example_NN.<ext> (NN = position in the civitai list, matching the prompts from civitai_model_examples). Batch mode: model_types (+ max_images per model, 0 = all). Single model: type + name. Honors the WebUI's 'Skip NSFW Preview Images' setting. Many models x ~10 images takes a long time and disk space, so for batch mode confirm with the user first and suggest max_images (e.g. 3).",
    "method": "POST",
    "path": "/download-examples",
    "parameters": {
      "type": "object",
      "properties": {
        "model_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "lora",
              "ckp",
              "ti",
              "hyper"
            ]
          },
          "description": "Batch mode: model types to process (omit when giving type + name)"
        },
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "name": {
          "type": "string",
          "description": "Single model mode: file name (e.g. foo.safetensors) or path relative to the model folder"
        },
        "max_images": {
          "type": "integer",
          "description": "Example images to keep per model (0 = all, default 0). Suggest 3-5 for batch runs."
        },
        "overwrite": {
          "type": "boolean",
          "description": "Re-download images that already exist (default false)"
        }
      }
    },
    "defaults": {
      "wait": true,
      "timeout": 540
    },
    "timeout": 580
  },
  {
    "name": "civitai_add_user_examples",
    "description": "Save images that civitai USERS posted as extra example images of ONE installed model, next to the model file as <model>.example_101.<ext>, <model>.example_102.<ext>, ... (numbers from 101 so they never clash with the civitai example list); their prompt, parameters, author and LoRAs are kept and shown by civitai_model_examples and written to the card by civitai_write_card_info. Two ways: image_ids = civitai image ids (e.g. picked from the civitai-api tool civitai_search_images), or username = take the images that user posted with THIS model (its own civitai version by default; all_versions for any version of the same model), up to max_images. Videos are skipped. NSFW images are included by default (username mode searches up to nsfw X); pass nsfw 'None' or skip_nsfw true only when the user wants SFW only.",
    "method": "POST",
    "path": "/add-user-examples",
    "parameters": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "name": {
          "type": "string",
          "description": "File name (e.g. foo.safetensors) or path relative to the model folder"
        },
        "image_ids": {
          "type": "array",
          "items": {
            "type": "integer"
          },
          "description": "civitai image ids to save (the image_id values from civitai_search_images)"
        },
        "username": {
          "type": "string",
          "description": "Instead of image_ids: civitai username whose images made with this model should be saved"
        },
        "model_version_id": {
          "type": "integer",
          "description": "Username mode: civitai version the images must use (default: the installed model's own version)"
        },
        "all_versions": {
          "type": "boolean",
          "description": "Username mode: accept any version of the same civitai model (default false; slower)"
        },
        "max_images": {
          "type": "integer",
          "description": "Username mode: how many images to save at most (default 10)"
        },
        "sort": {
          "type": "string",
          "enum": [
            "Most Reactions",
            "Most Comments",
            "Most Collected",
            "Newest",
            "Oldest"
          ],
          "description": "Username mode: which images come first"
        },
        "nsfw": {
          "type": "string",
          "enum": [
            "None",
            "Soft",
            "Mature",
            "X"
          ],
          "description": "Username mode: highest NSFW level to include (default X = everything; None = SFW only)"
        },
        "skip_nsfw": {
          "type": "boolean",
          "description": "Skip NSFW images (default false)"
        },
        "overwrite": {
          "type": "boolean",
          "description": "Re-download images that were already saved (default false)"
        }
      },
      "required": [
        "type",
        "name"
      ]
    },
    "defaults": {
      "wait": true,
      "timeout": 540
    },
    "timeout": 580
  },
  {
    "name": "civitai_remove_user_examples",
    "description": "Delete example images that were added from civitai users (numbers 101 and up) from ONE installed model: by civitai image id, by example number, or all of them. Does not touch the civitai example list (#1-#100).",
    "method": "POST",
    "path": "/remove-user-examples",
    "parameters": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "name": {
          "type": "string",
          "description": "File name (e.g. foo.safetensors) or path relative to the model folder"
        },
        "image_ids": {
          "type": "array",
          "items": {
            "type": "integer"
          },
          "description": "civitai image ids to remove"
        },
        "indexes": {
          "type": "array",
          "items": {
            "type": "integer"
          },
          "description": "Example numbers to remove (101 and up)"
        },
        "all": {
          "type": "boolean",
          "description": "Remove every user-added example of this model"
        }
      },
      "required": [
        "type",
        "name"
      ]
    }
  },
  {
    "name": "civitai_write_card_info",
    "description": "Write the trigger words and example prompts into the WebUI's Extra Networks card metadata (<model>.json): 'description' = trigger words + first example prompt / negative / steps / sampler / cfg / seed (shown on the card face), 'notes' = every example prompt in full (shown in the card's edit dialog), and 'sd version' (Forge preset: sd / xl / flux ...) derived from the civitai base model when unknown. By default only EMPTY fields are filled, so hand-written descriptions survive; overwrite=true replaces description / notes. Batch mode: model_types. Single model: type + name. Use when the user wants example prompts visible on the cards / in the WebUI, or asks to 'add descriptions to my LoRAs'.",
    "method": "POST",
    "path": "/write-card-info",
    "parameters": {
      "type": "object",
      "properties": {
        "model_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "lora",
              "ckp",
              "ti",
              "hyper"
            ]
          },
          "description": "Batch mode: model types to process (omit when giving type + name)"
        },
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "name": {
          "type": "string",
          "description": "Single model mode: file name (e.g. foo.safetensors) or path relative to the model folder"
        },
        "overwrite": {
          "type": "boolean",
          "description": "Replace existing description / notes (default false = fill empty fields only)"
        },
        "set_sd_version": {
          "type": "boolean",
          "description": "Also fill the card's 'sd version' from the civitai base model when unknown (default true)"
        }
      }
    },
    "defaults": {
      "wait": true,
      "timeout": 300
    },
    "timeout": 340
  },
  {
    "name": "civitai_rename_model",
    "description": "Rename ONE installed model (LoRA, checkpoint, embedding or hypernetwork) together with every file that belongs to it — civitai info, previews, card metadata (.json), example images — inside its own folder; it can also change the model file's extension (.safetensors / .ckpt / .pt / .bin). Changing the extension only renames the file, it does NOT convert it. Nothing is renamed when a target file already exists or another model of the same type already has the new name, and a failure half way is rolled back. Only call it when the user explicitly asks to rename a model or change its extension. The WebUI sees the new name only after its list is refreshed (refresh_loras / refresh_checkpoints / refresh_embeddings), and prompts that used the old <lora:old_name:...> tag must then use the new one.",
    "method": "POST",
    "path": "/rename-model",
    "parameters": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "lora",
            "ckp",
            "ti",
            "hyper"
          ],
          "description": "Model type: lora | ckp (checkpoint) | ti (embedding) | hyper (hypernetwork)"
        },
        "name": {
          "type": "string",
          "description": "Current file name (e.g. foo.safetensors) or, better, the path relative to the model folder (e.g. characters/foo.safetensors): a bare file name may match a model in another subfolder"
        },
        "new_name": {
          "type": "string",
          "description": "New file name. Without an extension (e.g. 'my_style_v2') the current extension is kept — use this form for every normal rename. Add .safetensors / .ckpt / .pt / .bin (e.g. 'my_style_v2.ckpt', or the same name with only another extension) only when the user explicitly asks to change the extension. The model stays in its folder. Not allowed: / \\ : * ? \" < > |, a leading or trailing space or dot, or a name ending in '.vae'"
        }
      },
      "required": [
        "type",
        "name",
        "new_name"
      ]
    }
  },
  {
    "name": "civitai_task_status",
    "description": "Poll a background Civitai Helper task (download / scan / check-new-version / fetch-previews / download-examples / write-card-info) by its id. Waits up to 60 s for it to finish before returning.",
    "method": "GET",
    "path": "/tasks/{task_id}",
    "parameters": {
      "type": "object",
      "properties": {
        "task_id": {
          "type": "string",
          "description": "Task id returned by a previous download/scan/check call"
        }
      },
      "required": [
        "task_id"
      ]
    },
    "defaults": {
      "wait": true,
      "timeout": 60
    },
    "timeout": 90
  }
]
""")

MCP_PATH = "/civitai-helper/mcp"
VERSION = "1.0.0"
INSTRUCTIONS = (
    "Civitai Helper for the Stable Diffusion WebUI: the local models (checkpoints, LoRAs, embeddings, "
    "hypernetworks) with their civitai info — list and search them with trigger words and base models, look "
    "up and download models from civitai, scan for missing info / previews, example images with their prompts, "
    "card metadata, rename and delete; search civitai for models and for the images people posted. "
    "A LoRA is used in a prompt as <lora:FILE_NAME:weight>."
)
# Routes the generated table above does not cover
TOOLS += [
    {
        "name": "civitai_model_types",
        "description": "The supported model types (ti, hyper, ckp, lora) with their folders and whether each exists.",
        "method": "GET",
        "path": "/model-types",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "civitai_delete_model",
        "description": "Delete one local model for good together with every file that belongs to it (info files, previews, card metadata, example images), like the trash button on its card. Refresh the WebUI's model list afterwards. Only do this when the user explicitly asks to delete that model.",
        "method": "POST",
        "path": "/delete-model",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["ti", "hyper", "ckp", "lora"], "description": "Model type"},
                "name": {"type": "string", "description": "File name or path relative to the model folder (prefer the relative path)"},
            },
            "required": ["type", "name"],
        },
    },
]
# Searching civitai itself (GET /civitai/models, /civitai/images)
TOOLS += [
    {
        "name": "civitai_search_models",
        "description": "Search civitai.com for models (LoRA by default): civitai's own JSON with every match's versions (base model, trigger words, files), example images, stats and creator. Use it to find / recommend a LoRA or checkpoint on civitai; download the chosen one with civitai_download_model.",
        "method": "GET",
        "path": "/civitai/models",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text, e.g. 'school uniform' or a character name. Do not put the base model in the text; use base_model for that."},
                "base_model": {"type": "array", "items": {"type": "string"}, "description": "Only models that have a version for these base models. Civitai names: 'SD 1.5', 'SDXL 1.0', 'Pony', 'Illustrious', 'NoobAI', 'Flux.1 D', 'Flux.1 S', 'Flux.2 D', 'Flux.2 Klein 9B', 'Anima', 'Krea 2', 'ZImageTurbo', 'ZImageBase', 'Qwen', 'Chroma', 'Wan Video 2.2 T2V-A14B' ...; common short forms (sd1.5, sdxl, pony, illu, noob, flux, qwen, z image, wan) are understood."},
                "types": {"type": "string", "description": "Model type: LORA (default), Checkpoint, TextualInversion, LoCon, DoRA, Hypernetwork, Controlnet."},
                "limit": {"type": "integer", "description": "How many results (1-100, default 6)."},
                "sort": {"type": "string", "enum": ["Highest Rated", "Most Downloaded", "Newest"], "description": "Default is relevance."},
                "period": {"type": "string", "enum": ["AllTime", "Year", "Month", "Week", "Day"]},
                "username": {"type": "string", "description": "Only models by this creator."},
                "tag": {"type": "string", "description": "Only models with this civitai tag."},
                "nsfw": {"type": "boolean", "description": "Include NSFW models; omit to use civitai's default."},
                "cursor": {"type": "string", "description": "Continue from a previous result's metadata.nextCursor."},
            },
        },
        "defaults": {"types": "LORA", "limit": 6},
    },
    {
        "name": "civitai_search_images",
        "description": "Search images that people posted on civitai.com, with their generation data (prompt, negative prompt, sampler, steps, CFG, seed, checkpoint, LoRAs used). For: images a user made with a given LoRA / model (username + model), everything a user posted (username), or community images of a model (model). Each image has a civitai id; to keep some as example images of an installed model, pass those ids to civitai_add_user_examples.",
        "method": "GET",
        "path": "/civitai/images",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "civitai username of the person who posted the images (without @)."},
                "model": {"type": "string", "description": "The LoRA / model the images must use: a civitai model id or model page URL (a URL with ?modelVersionId= narrows to that version). For a local model, civitai_local_model_info gives its civitai ids."},
                "model_version_id": {"type": "integer", "description": "Alternative to model: one civitai model version id."},
                "sort": {"type": "string", "enum": ["Most Reactions", "Most Comments", "Most Collected", "Newest", "Oldest"]},
                "period": {"type": "string", "enum": ["AllTime", "Year", "Month", "Week", "Day"]},
                "nsfw": {"type": "string", "enum": ["None", "Soft", "Mature", "X"], "description": "Highest NSFW level to include. Default X = everything; None = SFW only."},
                "limit": {"type": "integer", "description": "How many images (1-200, default 12)."},
                "cursor": {"type": "string", "description": "Continue from a previous result's metadata.nextCursor."},
            },
        },
        "defaults": {"limit": 12, "nsfw": "X"},
    },
]
_DESTRUCTIVE = {"civitai_delete_model"}

_READ_ONLY = {"civitai_lora_inventory", "civitai_list_local_models", "civitai_local_model_info",
              "civitai_model_examples", "civitai_lookup_model", "civitai_task_status", "civitai_model_types",
              "civitai_search_models", "civitai_search_images"}
_app: Any = None


def _basic_auth():
    """The WebUI's --api-auth credentials, so in-process calls pass the routes' auth check."""
    try:
        from modules import shared
    except ImportError:  # outside the WebUI (tests)
        return None
    pair = (getattr(shared.cmd_opts, "api_auth", None) or "").split(",")[0]
    user, sep, password = pair.partition(":")
    return httpx.BasicAuth(user, password) if sep else None


async def _api(method: str, path: str, *, params: Any = None, body: Any = None, timeout: float | None = 120) -> Any:
    if _app is None:
        raise ToolError("The WebUI has not finished starting")
    transport = httpx.ASGITransport(app=_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://webui.local", timeout=timeout,
                                 auth=_basic_auth()) as client:
        resp = await client.request(method, PREFIX + path, params=params, json=body)
    if resp.status_code >= 400:
        raise ToolError(f"{method} {path} failed: HTTP {resp.status_code} {resp.text[:800]}")
    if "json" in resp.headers.get("content-type", ""):
        return resp.json()
    return resp.text


def _query(args: dict) -> dict:
    return {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in args.items() if v is not None}


def _handler(spec: dict):
    async def run(args: dict, ctx: Context) -> Any:
        args = {**(spec.get("defaults") or {}), **{k: v for k, v in args.items() if v is not None}}
        path = spec["path"]
        for key in re.findall(r"{(\w+)}", path):
            if args.get(key) in (None, ""):
                raise ToolError(f"Missing argument: {key}")
            path = path.replace("{%s}" % key, quote(str(args.pop(key)), safe=""))
        method = spec["method"].upper()
        if method in ("GET", "DELETE"):
            params, body = _query(args), None
        else:
            in_query = set(spec.get("query_params") or [])
            params = _query({k: args.pop(k) for k in list(args) if k in in_query}) or None
            body = args
        call = asyncio.ensure_future(_api(method, path, params=params, body=body,
                                          timeout=float(spec.get("timeout") or 120)))
        waited = 0
        while True:  # long tasks: keep the client informed (and the connection alive)
            done, _ = await asyncio.wait({call}, timeout=5)
            if done:
                return call.result()
            waited += 5
            await ctx.progress(waited, None, f"{spec['name']}: running for {waited}s")
    return run


def _token() -> str:
    from modules import shared

    return os.environ.get("CIVITAI_HELPER_MCP_TOKEN") or shared.opts.data.get("ch_mcp_token") or ""


def _enabled() -> bool:
    from modules import shared

    return bool(shared.opts.data.get("ch_mcp_enable", True))


def ensure_token() -> None:
    """Generate the token on first start (called before the Settings page is built, so it shows there)."""
    from modules import shared

    if not os.environ.get("CIVITAI_HELPER_MCP_TOKEN") and not shared.opts.data.get("ch_mcp_token"):
        shared.opts.data["ch_mcp_token"] = secrets.token_urlsafe(24)
        shared.opts.save(shared.config_filename)


def mount(app: Any, log=print) -> McpServer:
    """Serve the tools at MCP_PATH on the WebUI app."""
    global _app

    _app = app
    ensure_token()  # also without the UI (--nowebui)
    server = McpServer("civitai-helper", VERSION, instructions=INSTRUCTIONS, get_token=_token,
                       is_enabled=_enabled, log=log)
    for spec in TOOLS:
        hints = ({"readOnlyHint": True} if spec["name"] in _READ_ONLY
                 else {"destructiveHint": True} if spec["name"] in _DESTRUCTIVE else None)
        server.add_tool(spec["name"], spec["description"], spec["parameters"], _handler(spec), hints)
    server.mount(app, MCP_PATH)
    return server
