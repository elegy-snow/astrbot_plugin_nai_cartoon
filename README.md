# NAI 同人漫画插件

AstrBot 插件：通过 Nai2API 兼容站点生成漫画单页。**提示词由会话中的 LLM 细化**（你提需求，模型写成提示词并出图），也保留 `/nai` 指令手动出图；支持用户密钥、角色卡、版式组装和全局串行出图队列。WebUI 分镜工作台尚未实现。

## 会话里直接出图（推荐用法）

直接在聊天里提需求即可，例如「画一张兔耳娘在雨里回头看的黑白四格」。会话中的模型会：

1. 按 `nai-doujin` 规则把你的需求细化成英文标签提示词（标签行 + 版式句 + 逐格描述）；
2. 调用插件注册的出图工具，图片由插件直接发到当前会话。

三个 LLM 工具（提示词写法规范写在工具说明里，模型调用时能读到）：

| 工具 | 用途 |
|---|---|
| `NAI_Generate_Image` | 单张 / 自由发挥：模型给完整提示词，插件负责成年守卫、条件负面、画师串、成本与队列 |
| `NAI_Generate_Comic_Page` | 多格页：模型只写每格的英文描述，版式句、位置词、空白气泡与负面由插件按 skill 规则补齐 |
| `NAI_List_Characters` | 列出角色卡（称呼 / 外貌 / 易错特征）与可用版式、画风预设 |

约束与行为：

- **同一轮消息最多出一次图**。模型在一轮里重复调用时，第二次会收到「本轮已经出过一次」，避免额度被连续烧掉。
- **成年守卫不可绕过**：无论模型写什么，负面里始终带 `loli, child, aged down, petite, flat chest`（`NEG_BASE`）。`lolita25d` 画风预设含幼态权重，本插件故意不提供。
- 插件的**提示词部分由模型的工具说明约束**，插件不替换 `【1】` 这类占位符：模型必须自己把角色外貌写成英文标签，或用 `character` 参数指定角色卡。`character` 可以传角色卡名，也可以传英文称呼 `ref` / 标签 `tag`（忽略大小写与开头的 the/a/an）；卡库为空或角色不存在时，工具会直接把可选项和替代做法回给模型，让它当轮自行改对。
- `image_mode = prompt_only` 时工具不出图，只把组装好的提示词回给模型（此模式不需要密钥）；`send_preview` 开启时出图回执里附带本次提示词。
- 设置页 **`enable_llm_tool`** 关掉后工具不再出图，只能用下面的指令。旧版 AstrBot 若没有 `filter.llm_tool`，插件仍会加载，只是工具不注册（启动日志会 warning）。

## 指令

- `/nai key <密钥>`：验证并绑定个人密钥
- `/nai key`、`/nai quota`：查询密钥状态或额度
- `/nai unkey`：解绑个人密钥
- `/nai cost`：查询默认尺寸、模型和步数的单张成本
- `/nai draw <提示词>`：直接提交提示词出图
- `/nai char list`：列出个人角色卡
- `/nai char show <名称>`：查看角色卡
- `/nai char new <名称> <JSON>`：创建或更新角色卡
- `/nai char del <名称>`：删除角色卡
- `/nai layout`：列出支持的版式
- `/nai prompt <JSON>`：组装提示词，不提交出图
- `/nai page <JSON>`：组装提示词并提交出图

角色卡 JSON 示例：

```json
{"ref":"the rabbit-eared woman","look":"monochrome, greyscale, adult woman, rabbit ears, long dark hair","uc":"fox ears, fox tail","parts":{"ears":"her long rabbit ears"}}
```

`look` 必须明确写成年特征。角色卡按用户分别存入 AstrBot KV。

`/nai prompt` 和 `/nai page` 使用同一 JSON 参数结构：

```json
{"character":"兔耳娘","layout":"四格","explicit":false,"behavior_tags":"","no_sex":false,"panels":[{"no":1,"action":"adult woman looking toward the viewer","shot":"close-up","dialogue":true},{"no":2,"action":"adult woman standing","sfx":"カランッ"},{"no":3,"action":"adult woman sitting","moan":"んっ…"},{"no":4,"action":"adult woman smiling"}]}
```

`no_sex` 是**单页请求字段**，默认 `false`，不是全局配置。只在当前页明确不画性行为（例如只画手或道具）时设为 `true`；该设置会把 `penis, sex, vaginal, penis in pussy` 加入当前页负面提示词。黑白/彩色默认模式由全局 `bw_default` 控制。

## 配置与密钥

站点地址、图像模式、是否允许会话 LLM 出图、个人站点密钥、角色卡 JSON、默认模型/尺寸/步数、队列上限、每日出图上限和提示词回执均可在 AstrBot 插件设置页配置。密钥由此插件安装者本人配置；也可用 `/nai key` 存入个人 KV。KV 为明文存储，运行 AstrBot 的本机管理员可读取。不要在群聊中公开发送密钥。设置页角色卡和命令创建的角色卡均可使用，命令保存的同名角色卡优先。

保存设置后若聊天窗口中的指令未读到新值，请在插件管理中重载本插件（配置在插件实例化时注入）。

`daily_quota` 限制每天成功出图的张数（0 表示不限制），用量按用户和日期记录在 KV，可用 `/nai quota` 查看。`send_preview` 开启后，出图成功会额外回复本页使用的提示词。

**画风预设与画师串**：设置页的 `fresh / comicDoujin / 2.5d / doujin / galgame` 只是预设名，插件会把它们换成站点前端里的真实画师串再发送（`core/artist_presets.py`）；`none` 表示不传画师串，`custom` 用 `custom_artist` 的值。此前把预设名当画师串发送的写法已修正。

出图成本依照站点前端公式计算，并在提交前进行额度预检。2K/4K 与步数加成会显著提高成本，出图前请核对 `/nai cost`。

## 限制

- 插件侧全局串行提交；站点任务状态通过轮询获取。
- 角色描述会内嵌到提示词。站点不支持角色框和局部重绘。
- 版式与提示词质量取决于会话模型的细化能力；插件只保证成年守卫、条件负面与成本正确。
- 站点是第三方服务，接口、价格和可用性可能变化。

## 测试

```powershell
py -3.14 -m unittest discover -s tests -v
```

共 64 项，其中 `tests/test_llm_flow.py` 用 `tests/_astrbot_stub.py`（本机没有 astrbot 包时的最小桩）真实执行了会话 LLM 工具的流程：画师串映射、角色卡按名字/英文称呼匹配、负面守卫、一轮一次出图、找不到角色卡时的纠错提示、`prompt_only` 与参数校验。

另有一条静态守卫 `LlmToolSchemaTests`：工具注释里的 `名字(类型):` **只能是 `string / number / object / array / boolean`**——AstrBot 注册工具时遇到别的类型会直接抛 `ValueError`（`astrbot/core/provider/func_tool_manager.py: SUPPORTED_TYPES`），导致插件安装失败。曾经把 `steps` 写成 `integer` 就是这样炸的。

装好后可用真实运行环境复核工具注册（把 `<实例>\core` 加入 `sys.path`）：

```powershell
python -c "import sys, importlib; sys.path.insert(0, r'<实例>\core'); sys.path.insert(0, r'E:\dsh_work'); importlib.import_module('astrbot_plugin_nai_cartoon.main'); import astrbot.core.provider.register as reg; print([t.name for t in reg.llm_tools.func_list])"
```
