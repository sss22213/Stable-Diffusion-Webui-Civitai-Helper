# -*- coding: UTF-8 -*-
# handle msg between js and python side
import os
import time
import util
import civitai
import downloader
import model_action_civitai

DEFAULT_KEY_PATH = "api.key"

class civitai_third_part_api:
    def __init__(self, has_api_key = None):

        if has_api_key is not None:
            self.has_api_key = True
            util.civitai_api_key = self.get_api_key()

    def get_model_id_by_url(self, model_url):
        pass

    def get_model_versions_by_model_id(self, id):
        pass

    def get_model_info(self, savefile = None, save_preview_img = None):
        pass

    def download_model(self, download_url):
        pass

    # The key stores at file "key"
    def get_api_key(self):
        with open(DEFAULT_KEY_PATH, 'r', encoding='utf-8') as file:
            return file.read()

if __name__ == '__main__':

    ctap = civitai_third_part_api(True)
    #model_info = civitai.get_version_info_by_model_id(636297)
    #print(model_info["modelVersions"][0])

    #print(model_action_civitai.get_model_info_by_url("https://civitai.com/models/636297"))

    #modelid = civitai.get_model_info_by_id(636297)
    #print(modelid["modelVersions"][0])

    # downloader.dl("https://civitai.com/api/download/models/1927994?type=Model&format=SafeTensor", None, None, "a.safetensors")
