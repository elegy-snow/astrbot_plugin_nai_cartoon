# NAI 同人漫画插件

AstrBot 插件：通过 Nai2API 兼容站点生成漫画单页，支持用户密钥、角色卡、版式提示词和全局串行出图队列。当前覆盖阶段 1–5；WebUI 分镜工作台尚未实现。

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

站点地址、图像模式、默认模型/尺寸/步数、队列上限和超时可在 AstrBot 插件配置中设置。密钥保存在 AstrBot KV 中；KV 为明文存储，AstrBot 实例管理员可读取。不要在群聊中公开发送密钥。

出图成本依照站点前端公式计算，并在提交前进行额度预检。2K/4K 与步数加成会显著提高成本，出图前请核对 `/nai cost`。

## 限制

- 插件侧全局串行提交；站点任务状态通过轮询获取。
- 角色描述会内嵌到提示词。站点不支持角色框和局部重绘。
- 版式和提示词格式由插件组装，不保证模型一定正确呈现分镜内容。
- 站点是第三方服务，接口、价格和可用性可能变化。
