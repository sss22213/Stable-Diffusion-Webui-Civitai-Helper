## 关于Civitai Helper2: Model Info Helper
近况请参考：[about_version2](about_version2.md) 


# 关于本分支
本分支 (sss22213) 让 Civitai Helper 1.x 能在 **SD WebUI Forge (Classic / Neo)** 与 Gradio 4 上运行，并新增：

* **范例图与卡片信息**（扩展页面中的 "Example Images & Card Info" 区块）：补齐缺失的预览图、把 civitai 上的全部范例图存到模型旁边、把触发词和范例关键词写到 Extra Networks 卡片上。详见[范例图与卡片信息](#范例图与卡片信息)。
* **用户范例图**：把其他 civitai 用户用某个模型生成的图片（按图片 ID 挑选，或「用户 X 用这个 LoRA 生成的全部图片」）连同关键词存为额外范例，和 civitai 范例放在一起。仅限 HTTP API，详见[范例图与卡片信息](#范例图与卡片信息)。
* **HTTP API**：位于 `/civitai-helper/v1`，让其他工具（聊天助手、脚本）可以列出已安装模型与触发词、查询 / 下载 civitai 模型、执行扫描。详见 [HTTP API](#http-api)。
* **Civitai Domain** 设置：可改用 `civitai.red` 之类的镜像站。
* 下载器修正：改用 wget 支持断点续传，处理相对跳转，模型需要 API Key 时给出明确提示。

# Civitai Helper
Stable Diffusion Webui 扩展Civitai助手，用于更轻松的管理和使用Civitai模型。

[Civitai Url](https://civitai.com/models/16768/civitai-helper-sd-webui-civitai-extension)  

# 注意
**本插件需要最新版SD webui，使用前请更新你的SD webui版本。安装本插件后，需要重启SD webui，而不光是重新加载UI，如果碰到问题，先看[常见问题](#常见问题)，并检查命令行窗口的详情。**   

# 功能
[中文介绍视频(非官方)](https://youtu.be/x4tPWPmeAgM?t=373)  

* 扫描所有模型，从Civitai下载模型信息和预览图 
* 通过civitai模型页面url，连接本地模型和civitai模型信息
* 通过Civitai模型页面url，下载模型(含信息和预览图)到SD目录或子目录。
* 下载支持断点续传
* 批量检查本地模型，在civitai上的新版本
* 直接下载新版本模型到SD模型目录内(含信息和预览图)
* 修改了内置的"Extra Network"模型卡片，每个卡片增加了如下功能按钮:
  - 🌐: 在新标签页打开这个模型的Civitai页面
  - 💡: 一键添加这个模型的触发词到关键词输入框
  - 🏷: 一键使用这个模型预览图所使用的关键词
* 范例图与卡片信息（本分支，见下文）：补齐预览图、把范例关键词写到卡片、下载全部范例图，并可通过 API 把其他 civitai 用户发布的图片加为额外范例。
* HTTP API（本分支，见下文），供脚本与助手使用。


# 安装
下载本项目为zip文件，解压到`你的SD webui目录/extensions`下即可。

不管是安装还是升级本插件，都要整个关闭SD Webui，重新启动它。只是Reload UI不起作用。  

(如果用SD webui的插件界面安装，请先给git配置代理。它不是通过浏览器下载，是通过git下载。)  


# 使用方法

## 更新你的SD webui
请更新SD webui到最新版  

## 扫描模型
前往扩展页面"Civitai Helper"，有个按钮叫："Scan Model"  

![](img/extension_tab.jpg)  

点击，就会扫描所有模型，生成SHA256码，用于从civitai获取模型信息和预览图。**扫描需要很久，耐心等待**。

每个模型，本扩展都会创建一个json文件，用来保存从civitai得到的模型信息。这个文件会保存在模型同目录下，名称为："模型名字.civitai.info"。  

![](img/model_info_file.jpg)  

如果模型信息文件已经存在，扫描时就会跳过这个模型。如果模型不是civitai的，就会创建个空信息文件，以避免以后重复扫描。

### 添加新模型
当你下载了新模型之后，只要再次点击扫描按钮即可。已经扫描过的文件不会重复扫描，会自动得到新模型的信息和预览图。无须重启SD webui。 

## 模型卡片
**(先完成扫描，再使用卡片功能)**  
打开SD webui's 内置的 "Extra Network" 页面，显示模型卡片  

![](img/extra_network.jpg)  


移动鼠标到模型卡片顶部，就会显示3个额外的按钮：
  - 🌐: 在新标签页打开这个模型的Civitai页面
  - 💡: 一键添加这个模型的触发词到关键词输入框
  - 🏷: 一键使用这个模型预览图所使用的关键词
  
![](img/model_card.jpg)  

如果你没有看到这些额外的按钮，只要点击`Refresh Civitai Helper`，他们就会被重新添加到卡片上。  

![](img/refresh_ch.jpg)  

每次当Extra Network刷新，他都会删除掉额外的修改，我们的按钮就会消失。这时你就需要点击`Refresh Civitai Helper`把这些功能添加回去。

## 下载 
**(单任务，下载完一个再下另一个)**  
通过Civitai模型页面Url下载模型，要3个步骤：
* 填入url，点击按钮获取模型信息
* 扩展会自动填入模型名称和类型，你需要选择下载的子目录和模型版本。
* 点击下载  
![](img/download_model.jpg)

下载过程会显示在命令行界面带个进度条。  
支持断点续传，无畏大文件。  


## 批量检查模型新版本
你可以按照模型类型，批量检查你的本地模型，在civitai上的新版本。你可以选择多个模型类型。  
![](img/check_model_new_version.jpg)  

检查新版本的时候，每检查完一个模型，都会有一个1秒的延迟，所以速度有点慢。

这是为了保护Civitai避免因为本插件而短暂陷入类似DDos的局面。有些云服务商，有类似“免费用户每秒API请求不能超过1次”的保护机制。Civitai还没有这种设置。但我们还是得自觉保护它。因为如果它挂了，对大家都没有好处。    

**检查完毕之后**，就会如下图，在UI上显示所有找到的新版本的信息。  

每个模型新版本，都有3个链接。
* 第一个是这个模型的网页。
* 第二个是这个新版本的下载地址。   
* 第三个是个按钮，在python端，直接下载新版本到模型目录内。  
这种方式下载，下载详情显示在"Download Model"的区域和命令行窗口中。一次一个任务，不支持多任务。  
![](img/check_model_new_version_output.jpg)



## 根据URL获取模型信息
如果无法在civitai上找到你的模型的SHA256，但你还是希望能把你的模型连接到一个civitai模型，你可以在本扩展页面，从列表中选择你的模型，并提供一个civitai模型页面的url。

点击按钮之后，扩展就会下载那个civitai模型的信息，作为你这个本地模型的信息使用。  

![](img/get_one_model_info.jpg)  

## 范例图与卡片信息
每个模型信息文件（`模型名.civitai.info`）本来就保存了该版本在 civitai 上的范例图，多数带有完整生成参数（关键词、负面关键词、步数、采样器、CFG、种子、所用大模型）。原版扩展只拿第一张当预览图，其余没有用到。扩展页面的 "Example Images & Card Info" 区块把它们利用起来。先勾选模型类型，下面每个按钮都会处理这些类型的全部模型。请先执行 "Scan"，信息文件才会存在。

### Fetch Missing Previews（补齐预览图）
为没有预览图的模型下载 `模型名.preview.png`。会按顺序尝试每一张范例图，取第一张能下载的，所以第一张已在 civitai 上被删除的模型也能拿到预览图。不调用 civitai API，只下载图片，对整个模型库执行也很快。所有范例图都已被删除的模型会列在日志中，需要预览图的话自己放一张 `模型名.png` 在旁边即可。

### Write Example Prompt to Cards（把范例关键词写到卡片）
写入 WebUI 自己的卡片信息文件 `模型名.json`：

* `description`：触发词、第一个范例的关键词、负面关键词与参数。卡片显示前 3 行，鼠标移上去展开（需开启 WebUI 设置 "Show description on cards"，默认开启）。
* `notes`：全部范例关键词的完整内容。打开卡片的编辑（铅笔）对话框可查看。
* `sd version`：由 civitai 的 base model 推导出的 Forge 预设（`sd`、`xl`、`flux` 等），仅在卡片尚未设置时填入。

默认只填空白字段，自己写过的描述和备注会保留；勾选 "Overwrite existing description / notes" 才会覆盖。`activation text`、`preferred weight`、`negative text` 这些用户字段永远不会改动。写完后在 Extra Networks 页面点 "Refresh" 才会看到新描述。

### Download Example Images（下载范例图）
把范例图存为 `模型名.example_01.jpeg`、`模型名.example_02.jpeg` ……编号就是 civitai 列表中的顺序，因此每张图都能对应回信息文件中的关键词（也对应 API 的 `/models/{type}/examples` 输出）。"Max images per model" 限制每个模型保存几张（0 = 全部，通常每个模型最多 10 张），"Re-download existing" 会重新下载已存在的文件。遵守 "Skip NSFW Preview Images" 设置。模型库很大时文件很多、耗时较长，进度看命令行日志。WebUI 会忽略这些文件（既不是预览图也不是模型）。用卡片上的 🗑 删除模型时会一并删除。

其他 civitai 用户发布的图片（例如用户 X 用这个 LoRA 生成的全部图片）可以通过 HTTP API（`/add-user-examples`）加为额外范例，有两种方式：

* **按图片 ID**（`image_ids`）：你挑选的图片，例如从 civitai 图片搜索中选出的。civitai 查不到的 ID 会列在 `not_found`。
* **按用户名**（`username`）：该用户用这个模型本身的版本生成的静态图片，版本取自模型的信息文件（请先执行 "Scan"，或传入 `model_version_id`）。`all_versions` 会同时接受同一个 civitai 模型的其他版本。`max_images`（默认 10，最多 200）限制张数；`sort` 与 `period` 的含义和 civitai 相同。

编号从 101 开始（`模型名.example_101.jpeg`），不会和 civitai 列表冲突；关键词、生成参数、用到的 LoRA 与作者记录在 `模型名.examples.json`。重复加入同一张图会保留原编号（`overwrite` 会重新下载）。已经在模型 civitai 范例列表中的图片不会重复加入，视频会被跳过。默认包含 NSFW 图片，可用 `skip_nsfw` 排除（或用 `nsfw` 限制搜索等级）；"Skip NSFW Preview Images" 设置对此不生效。

`/models/{type}/examples` 会把它们列在 civitai 范例之后，并带有 `"source": "user"`、用户名、civitai 图片页面与用到的 LoRA。"Write Example Prompt to Cards" 会把它们的关键词加到卡片备注中，标题为 "Example #101 (by X on civitai)"。`/remove-user-examples` 可按图片 ID、编号或全部删除；用卡片上的 🗑 删除模型时也会一并删除。

## HTTP API
扩展在 WebUI 上注册了一组 REST API，路径为 `/civitai-helper/v1`。启动完成后可在 WebUI 的 Swagger 页面（`/docs`）看到；若启动时带了 `--api-auth`，这些接口使用同一组账号密码。不需要 `--api` 参数。

只读接口立即返回；耗时操作在一个后台线程上依次执行，以 *task* 形式暴露：POST 返回任务记录，`GET /tasks/{id}` 轮询，或在 POST 请求体中带 `"wait": true`（最多等待 `timeout` 秒）让一次调用完成整件事。任务状态为 `queued`、`running`、`done`、`error`。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/version` | 扩展版本、生效的设置、模型目录 |
| GET | `/model-types` | 支持的类型（`ti`、`hyper`、`ckp`、`lora`）及其目录 |
| GET | `/loras?q=&limit=&offset=&metadata=&compact=&format=json\|csv\|md` | 每个 LoRA：文件名、`prompt_tag`、civitai 名称 / 版本 / base model / 触发词、safetensors 头部的训练信息、已下载范例图数量 |
| GET | `/models?type=&q=&no_info_only=&empty_info_only=&metadata=&limit=&offset=&format=` | 任意类型的同样清单 |
| GET | `/models/{type}/info?name=&full=` | 单个模型保存的 civitai 信息（`full=true` 附带原始信息文件与 safetensors 元数据） |
| GET | `/models/{type}/examples?name=` | 单个模型的范例图：关键词、负面关键词、步数、采样器、CFG、种子、大模型、NSFW 标记、civitai 网址，以及已下载时的本地文件 / 网址 |
| GET | `/model-info?url_or_id=` | 用 id 或页面网址查询 civitai 模型：版本（最新在前）、触发词、文件、目标目录及子目录 |
| POST | `/scan` | 请求体 `{"model_types": ["lora"]}`；补齐缺失的信息文件与预览图（SHA256 匹配） |
| POST | `/download` | 请求体 `{"url_or_id", "version_id"?, "version"?, "subfolder": "/", "create_subfolder"?, "dl_all"?}`；下载版本并保存信息文件与预览图 |
| POST | `/check-new-version` | 请求体 `{"model_types": [...]}`；列出有新版本的本地模型 |
| POST | `/fetch-previews` | 请求体 `{"model_types": [...]}` 或 `{"type", "name"}`；等同 "Fetch Missing Previews" 按钮 |
| POST | `/download-examples` | 请求体 `{"model_types": [...]}` 或 `{"type", "name"}`，另有 `max_images`（0 = 全部）与 `overwrite` |
| POST | `/write-card-info` | 请求体 `{"model_types": [...]}` 或 `{"type", "name"}`，另有 `overwrite` 与 `set_sd_version` |
| POST | `/add-user-examples` | 把 civitai 用户发布的图片存为单个模型的额外范例（`模型名.example_101.jpeg`……）：请求体 `{"type", "name"}`，加上 `image_ids`（例如从 civitai 图片搜索挑出的）或 `username`（该用户用这个模型版本生成的图；可选 `all_versions`、`max_images`、`sort`、`period`）；默认包含 NSFW 图片（`nsfw` 为搜索的最高等级，`"None"` 仅 SFW；`skip_nsfw` 可排除 NSFW），视频会被跳过 |
| POST | `/remove-user-examples` | 请求体 `{"type", "name"}`，加上 `image_ids`、`indexes` 或 `all: true` |
| GET | `/tasks`、`/tasks/{id}?wait=&timeout=` | 最近的任务 / 单个任务 |

所有任务接口都接受请求体中的 `"wait": true` 与 `"timeout": <秒>`。示例：

```bash
# 下载某模型的最新版本到 LoRA 根目录并等待完成
curl -X POST http://127.0.0.1:7860/civitai-helper/v1/download \
  -H 'Content-Type: application/json' \
  -d '{"url_or_id": "https://civitai.com/models/1676478", "wait": true, "timeout": 600}'

# 单个 LoRA 的范例关键词
curl 'http://127.0.0.1:7860/civitai-helper/v1/models/lora/examples?name=Maha-10.safetensors'

# 为全部 LoRA 写卡片描述
curl -X POST http://127.0.0.1:7860/civitai-helper/v1/write-card-info \
  -H 'Content-Type: application/json' -d '{"model_types": ["lora"], "wait": true}'

# 把用户 "someone" 用某个 LoRA 生成的最新 10 张图加为范例 #101 起
curl -X POST http://127.0.0.1:7860/civitai-helper/v1/add-user-examples \
  -H 'Content-Type: application/json' \
  -d '{"type": "lora", "name": "Maha-10.safetensors", "username": "someone", "sort": "Newest", "max_images": 10, "wait": true}'
```

## 设置
现在所有设置被移动到 Setting 页面->Civitai Helper区域中。

### 代理
代理输入框也在其中。
有些sock5代理, 需要使用socks5h开头的形式"socks5h://xxxxx"才能生效。   
![](img/other_setting.jpg) 

### Civitai API Key
有些模型现在要登录civitai网站才能下载。要通过Civitai API做到这一点，你需要在你的civitai帐号设置中，创建一个API Key，然后填写到本扩展的设置中来。

zixaphir写了一个详细的教程: [wiki](https://github.com/zixaphir/Stable-Diffusion-Webui-Civitai-Helper/wiki/Civitai-API-Key).

这里是比较简单的教程:
* 登录 civitai.com
* 前往 [你的帐号的Account Setting页面](https://civitai.com/user/account)
* 在页面底部，找到"API Keys"部分.
* 点击"Add API Key"按钮, 起个名字.
* 复制生成的api key字符串，粘贴到本扩展设置页面  -> Civitai API Key 部分.
* 保存设置，并重启SD webui


### Civitai Domain
要调用的 civitai 站点域名，默认 `civitai.com`。主站被封锁或缓慢时，可以填写提供相同 `/api/v1` 路径的镜像站（例如 `civitai.red`）。卡片上打开的模型页面链接也使用该域名；图片始终从 civitai 的图片 CDN 下载。

### Civitai网站上的内容可见设置
在你的Civitai帐号设置页面，有一个环节叫做："Content Controls" 和 "Content Moderation".  

如果你在那里设置了隐藏一些内容，那么本扩展就无法获取对应种类的模型。  

要能够获取所有内容，你需要：  

* 像前文提到的，设置Civitai API Key

* 并且在Civitai中，开启显示所有内容，类似如下截图:  

![](img/civitai_content_control_01.jpg)  

![](img/civitai_content_control_02.jpg)  




## 预览图
Extra Network支持两种预览图命名：`model_name.png` 和 `model_name.preview.png`。其中，`model_name.png`优先级较高。

当优先级较高的预览图不存在，他就会自动使用`model_name.preview.png`。

这样，你自己创建的预览图 和 网络下载的预览图，能够同时存在，并优先使用你自己创建的。

## 关键词
卡片上，添加关键词按钮，是添加从civitai预览图中得到的关键词，而不是你自己创建的图片的关键词。

civitai不是每个图片都有关键词，一个模型中，也不是所有预览图关键词都一样。所以这里是遍历所有civitai预览图信息，加载第一个有关键词的。


## SHA256
为了创建文件的SHA256，插件需要读取整个文件。对于大尺寸文件，就会很慢。

有两种情况，这个SHA256无法从civitai找到对应模型：
* 太老的模型，civitai没有存储SHA256.
* 模型作者，静静的换掉了模型文件，但没有修改描述和版本。所以，虽然网页上看不出来，但实际上civitai上的 和你本地的模型文件，已经不是同一个文件了。  

这些情况下，你可以在插件上，通过提供模型页面的url，来获取模型信息文件。



## 新特性
从v1.5开始，v1.x不再接受任何新特性。所有新特性进入2.x。

2.x专注于自定义模型信息，并可能改名为"Model Info Helper"。因为不再是专注Civitai了。 

从v1.5开始。v1.x进入维护阶段。  


Enjoy!


## 常见问题
### 4个卡片按钮不显示
#### 使用了云端汉化功能
如果是秋叶启动器，就关闭启动器“云端汉化”功能。如果是专门的云端汉化插件，就换用普通汉化插件。  

#### 其他情况
首先，确保你点过了"Refresh Civitai Helper"刷新按钮。  

然后，如果还有这个问题，那么唯一原因，是你没有使用最新版SD webui。  

如果你修改过SD webui的文件， 你的更新操作可能会失败。你需要检查git命令行的输出信息，来确定你更新成功了。   

git在很多时候，会拒绝升级，并告诉你有些冲突需要你手动先解决。如果你不看命令行输出，你就会以为你已经更新成功了，但其实并没有。   

 
### Request model info from civitai
意思就是正在连接civitai，如果没有后面的信息，就是连不上，请挂代理。

### 扫描或获取模型信息失败
这个插件现在很稳定，所以，这个问题的原因，基本是是因为Civitai拒绝了你的连接请求。  

Civitai不像那些大网站那么稳定。他网站会挂，会拒绝API连接，还会把API请求转到真人验证页面，来挡住。  

Civitai还有连接池的设定。基本上，就是同时能允许的最大连接数。一旦达到这个数字，接下来的API连接请求，都会被拒绝。  

所以，这种时候你只能等一下再试。  

另外，对于国内用户，还有代理问题。现在国内都要用代理才能连上。   


### 扫描之后得到了错误的预览图和模型信息
坏消息是，有些模型在civitai数据库中，保存的sha256完全是错的。查看下面的issue了解详情：    
[https://github.com/civitai/civitai/issues/426](https://github.com/civitai/civitai/issues/426)  

对于这种模型，那这个插件自然就无法获得正确的模型信息和预览图。  

这种情况下，请删除扫描得到的模型信息和预览图，在插件界面提供正确的模型url来获取。   

另外，civitai官方有个页面，专门用于回报带有错误sha256的模型：   
[https://discord.com/channels/1037799583784370196/1096271712959615100/1096271712959615100](https://discord.com/channels/1037799583784370196/1096271712959615100/1096271712959615100)  

请把这类模型反馈给civitai，好让他们进行修复。  




### 使用colab时扫描失败
首先，在google中搜索你看到的错误信息。更有可能是，你碰到的是个colab的问题。  

然后，如果colab连接了google drive，会有一次性访问文件数量的限制，而导致扫描失败。这是google drive的限制，请自行google搜索了解详情。



