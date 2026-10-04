from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

from .core.artist_presets import PRESET_CHOICES, artist_for_preset
from .core.character_store import CharacterStore, CharacterStoreError
from .core.config_utils import config_value
from .core.draw_flow import DrawError, resolve_cost, resolve_model, resolve_size, resolve_steps
from .core.llm_prompt import compose_negative, detect_conditionals, merge_character, normalize_tags
from .core.nai_client import NaiClient
from .core.pricing import cost_for_size
from .core.prompt_builder import LAYOUTS, base_negative, build_page_prompt
from .core.queue import DrawQueue, QueueFullError
from .core.usage import UsageStore

# 同一条消息里最多出一次图（防止模型在一轮里反复调用工具烧额度）。
DRAW_STATE_KEY = "nai_cartoon_draw_state"


def _llm_tool():
    """注册会话 LLM 工具；旧版 AstrBot 没有 filter.llm_tool 时退化为空装饰器。

    退化后工具不会注册（聊天指令不受影响），插件本身不会因为缺 API 而加载失败。
    """
    factory = getattr(filter, "llm_tool", None)
    if factory is None:
        def _identity(func):
            return func

        return _identity
    return factory()


@register("astrbot_plugin_nai_cartoon", "elegy-snow", "NovelAI 同人漫画单页生成", "0.2.0")
class NaiDoujinPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        if config is None:
            super().__init__(context)
        else:
            super().__init__(context, config)
        self._plugin_config: Any = config if config is not None else {}
        self._queue: DrawQueue | None = None
        self._usage = UsageStore(self)

    def _config(self, key: str, default: Any) -> Any:
        value = config_value(self._plugin_config, key, None)
        if value is not None and value != "":
            return value
        try:
            fallback = config_value(self.context.get_config(), key, None)
        except Exception:
            fallback = None
        if fallback is not None and fallback != "":
            return fallback
        return default

    def _client(self) -> NaiClient:
        return NaiClient(str(self._config("station_base", "https://nai.sta1n.cn")))

    def _character_store(self) -> CharacterStore:
        return CharacterStore(self, self._config("character_cards", "{}"))

    async def initialize(self) -> None:
        self._queue = DrawQueue(
            timeout=float(self._config("queue_timeout", 300)),
            maxsize=int(self._config("max_queue", 20)),
        )
        await self._queue.start()
        logger.info(
            "NAI 配置已加载：密钥=%s，角色卡=%d 张，模型=%s，尺寸=%s，步数=%s，每日上限=%s，站点=%s",
            "已配置" if str(self._config("user_key", "") or "").strip() else "未配置",
            len(self._character_store().configured_cards),
            self._config("default_model", ""),
            self._config("default_size", ""),
            self._config("default_steps", ""),
            self._config("daily_quota", 0),
            self._client().base_url,
        )
        try:
            settings = await self._client().settings()
            logger.info("NAI station settings loaded (costPerImage=%s)", settings.get("costPerImage", "unknown"))
        except Exception as exc:
            logger.warning("NAI station probe failed: %s", exc)
        if getattr(filter, "llm_tool", None) is None:
            logger.warning("当前 AstrBot 版本没有 filter.llm_tool，会话 LLM 出图工具未注册（聊天指令不受影响）")
        elif not bool(self._config("enable_llm_tool", True)):
            logger.info("会话 LLM 出图工具已在设置页关闭（enable_llm_tool=false）")

    async def _get_token(self, user_id: str) -> str:
        configured_key = str(self._config("user_key", "") or "").strip()
        if configured_key:
            return configured_key
        token = await self.get_kv_data(f"token:{user_id}", "")
        return str(token or "")

    @filter.command("nai")
    async def nai(self, event: AstrMessageEvent):
        """显示 NAI 插件指令。"""
        yield event.plain_result("NAI 插件已就绪。使用 /nai key <密钥>、/nai key、/nai unkey、/nai quota、/nai cost 或 /nai draw <提示词>。")

    @filter.command("nai key")
    async def key(self, event: AstrMessageEvent, key: str = ""):
        """绑定密钥，或查询已绑定密钥的额度。"""
        user_id = str(event.get_sender_id())
        key = key.strip()
        if not key:
            token = await self._get_token(user_id)
            if not token:
                yield event.plain_result("设置页和 /nai key 均未配置密钥。")
                return
            if str(self._config("user_key", "") or "").strip():
                yield event.plain_result("设置页密钥已配置。使用 /nai quota 查询额度。")
                return
            try:
                account = await self._client().me(token)
            except Exception as exc:
                logger.warning("NAI account lookup failed: %s", exc)
                yield event.plain_result("KV 绑定密钥存在，但额度查询失败；请用 /nai quota 检查站点连接。")
                return
            balance = account.get("balance", account.get("anlas", "未知"))
            yield event.plain_result(f"密钥已配置，当前余额：{balance} 点。")
            return
        try:
            account = await self._client().me(key)
            if account.get("enabled") is False:
                yield event.plain_result("该密钥已停用，未保存。")
                return
            await self.put_kv_data(f"token:{user_id}", key)
            balance = account.get("balance", account.get("anlas", "未知"))
            yield event.plain_result(f"密钥绑定成功，当前余额：{balance} 点。")
        except Exception as exc:
            logger.warning("NAI key validation failed: %s", exc)
            yield event.plain_result("密钥验证失败，未保存；请确认密钥和站点地址。")

    @filter.command("nai unkey")
    async def unkey(self, event: AstrMessageEvent):
        """解绑自己的密钥。"""
        user_id = str(event.get_sender_id())
        await self.delete_kv_data(f"token:{user_id}")
        yield event.plain_result("已解绑个人密钥。")

    @filter.command("nai quota")
    async def quota(self, event: AstrMessageEvent):
        """查询站点额度与今日出图用量。"""
        user_id = str(event.get_sender_id())
        token = await self._get_token(user_id)
        if not token:
            yield event.plain_result("请先在设置页填写密钥，或使用 /nai key <密钥> 绑定。")
            return
        try:
            account = await self._client().me(token)
            balance = account.get("balance", account.get("anlas", "未知"))
        except Exception as exc:
            logger.warning("NAI quota lookup failed: %s", exc)
            yield event.plain_result("额度查询失败，请检查密钥和站点配置。")
            return
        lines = [f"当前余额：{balance} 点。"]
        daily_limit = int(self._config("daily_quota", 0) or 0)
        used = await self._usage.count(user_id)
        lines.append(
            f"今日已出图 {used} 张（上限 {daily_limit}）。" if daily_limit > 0 else f"今日已出图 {used} 张（未设上限）。"
        )
        yield event.plain_result("\n".join(lines))

    @filter.command("nai cost")
    async def cost(self, event: AstrMessageEvent):
        """显示默认设置下单张图片的费用。"""
        size = str(self._config("default_size", "竖图"))
        model = str(self._config("default_model", "nai-diffusion-4-5-full"))
        steps = int(self._config("default_steps", 28))
        try:
            amount = cost_for_size(size, model, steps)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(f"当前参数：{size} / {model} / {steps} 步，单张预计 {amount} 点。")

    def _artist(self, style: str = "") -> str:
        """解析要发给站点的画师串。

        `style` 为空时用设置页的默认画风预设。设置页存的只是预设名
        （fresh/doujin/...），必须换成站点前端里的真实画师串再发送。
        """
        choice = str(style or "").strip() or str(self._config("default_artist_preset", "doujin"))
        return artist_for_preset(choice, str(self._config("custom_artist", "") or ""))

    async def _start_draw(
        self,
        user_id: str,
        *,
        prompt: str,
        negative: str,
        size: str,
        model: str,
        steps: int,
        artist: str,
    ) -> tuple[Any, int]:
        """额度/成本预检后把任务提交到本机队列，返回 (future, 预计点数)。"""
        token = await self._get_token(user_id)
        if not token:
            raise DrawError("请先在设置页填写密钥，或使用 /nai key <密钥> 绑定。")
        daily_limit = int(self._config("daily_quota", 0) or 0)
        if daily_limit > 0:
            used = await self._usage.count(user_id)
            if used >= daily_limit:
                raise DrawError(
                    f"今日出图已达上限（{used}/{daily_limit}）。"
                    "可调高设置页的「每用户每日出图上限」或明日再试。"
                )
        cost = resolve_cost(size, model, steps)
        if bool(self._config("quota_precheck", True)):
            try:
                account = await self._client().me(token)
            except Exception as exc:
                logger.warning("NAI quota precheck failed: %s", exc)
                raise DrawError("额度预检失败，未提交任务。请检查密钥和站点状态。") from None
            balance = account.get("balance")
            if isinstance(balance, (int, float)) and balance < cost:
                raise DrawError(f"额度不足：本次需要 {cost} 点，当前余额 {balance} 点。")
        if self._queue is None:
            raise DrawError("生成队列尚未就绪，请稍后重试。")
        client = self._client()
        operation = lambda: client.generate(
            token,
            timeout=float(self._config("queue_timeout", 300)),
            tag=prompt,
            artist=artist,
            model=model,
            size=size,
            steps=steps,
            negative=negative,
        )
        try:
            return await self._queue.submit(operation), cost
        except QueueFullError:
            raise DrawError("排队已满，稍后再试。") from None

    async def _finish_draw(self, user_id: str, future: Any) -> bytes:
        """等待队列任务完成，成功后记录当日用量。"""
        _job, image_bytes = await future
        await self._usage.record(user_id)
        return image_bytes

    async def _generate_for_tool(
        self,
        user_id: str,
        *,
        prompt: str,
        negative: str,
        size: str,
        steps: int,
        style: str = "",
    ) -> tuple[str, bytes | None]:
        """会话 LLM 工具共用的出图路径，返回 (给模型的文本, 图片字节或 None)。"""
        try:
            future, cost = await self._start_draw(
                user_id,
                prompt=prompt,
                negative=negative,
                size=size,
                model=resolve_model(self._config("default_model", "nai-diffusion-4-5-full"), "nai-diffusion-4-5-full"),
                steps=steps,
                artist=self._artist(style),
            )
        except DrawError as exc:
            return f"生成失败：{exc}", None
        try:
            image_bytes = await self._finish_draw(user_id, future)
        except Exception as exc:
            logger.warning("NAI LLM tool draw failed: %s", exc)
            return f"生成失败：{str(exc)[:300]}", None
        reply = f"图片已生成并发送给用户（本次花费 {cost} 点），请根据本次请求继续回复。"
        if bool(self._config("send_preview", False)):
            reply += "\n本次使用的提示词：\n" + prompt
        return reply, image_bytes

    async def _draw_prompt(self, event: AstrMessageEvent, prompt: str, negative: str | None = None):
        prompt = prompt.strip()
        if not prompt:
            yield event.plain_result("请提供提示词。")
            return
        user_id = str(event.get_sender_id())
        if str(self._config("image_mode", "direct")) == "prompt_only":
            # 只出提示词不碰站点，因此不需要密钥。
            yield event.plain_result(prompt)
            return
        try:
            size = resolve_size(self._config("default_size", "竖图"), "竖图")
            model = resolve_model(self._config("default_model", "nai-diffusion-4-5-full"), "nai-diffusion-4-5-full")
            steps = resolve_steps(self._config("default_steps", 28), 28)
            future, cost = await self._start_draw(
                user_id,
                prompt=prompt,
                negative=negative if negative is not None else base_negative(bool(self._config("bw_default", True))),
                size=size,
                model=model,
                steps=steps,
                artist=self._artist(),
            )
        except DrawError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(f"任务已提交到本机队列，预计费用 {cost} 点。")
        try:
            image_bytes = await self._finish_draw(user_id, future)
        except Exception as exc:
            logger.warning("NAI draw failed: %s", exc)
            yield event.plain_result(f"生成失败：{str(exc)[:300]}")
            return
        if bool(self._config("send_preview", False)):
            yield event.plain_result(f"提示词：\n{prompt}")
        yield event.chain_result([Image.fromBytes(image_bytes)])

    @filter.command("nai draw")
    async def draw(self, event: AstrMessageEvent, prompt: str = ""):
        """直接按提示词生成单张图片。"""
        async for result in self._draw_prompt(event, prompt):
            yield result

    async def _build_page(
        self, user_id: str, payload: dict[str, Any], *, bw: bool | None = None
    ) -> tuple[str, str]:
        """按角色卡与分镜组装整页提示词；`bw` 留空时用设置页默认。"""
        name = str(payload.get("character", "")).strip()
        if not name:
            raise ValueError("请指定角色卡名")
        character = await self._character_store().get(user_id, name)
        if character is None:
            raise ValueError(f"未找到角色卡：{name}")
        panels = payload.get("panels")
        if not isinstance(panels, list):
            raise ValueError("panels 必须是分镜数组")
        return build_page_prompt(
            layout=str(payload.get("layout", self._config("default_layout", "四格"))),
            panels=panels,
            characters=[character],
            explicit=bool(payload.get("explicit", self._config("default_explicit", False))),
            behavior_tags=str(payload.get("behavior_tags", self._config("default_behavior_tags", ""))),
            bw=bool(self._config("bw_default", True)) if bw is None else bool(bw),
            no_sex=bool(payload.get("no_sex", self._config("no_sex_default", False))),
        )

    @filter.command("nai prompt")
    async def prompt(self, event: AstrMessageEvent, payload_text: str = ""):
        """根据角色卡和分镜 JSON 组装提示词，不出图。"""
        try:
            payload = json.loads(payload_text)
            if not isinstance(payload, dict):
                raise ValueError("参数必须是 JSON 对象")
            assembled, negative = await self._build_page(str(event.get_sender_id()), payload)
            yield event.plain_result(f"正向提示词：\n{assembled}\n\n负面提示词：\n{negative}")
        except (json.JSONDecodeError, ValueError) as exc:
            yield event.plain_result(f"参数错误：{exc}")
        except Exception as exc:
            logger.warning("NAI prompt build failed: %s", exc)
            yield event.plain_result("提示词组装失败，请检查角色卡和分镜数据。")

    @filter.command("nai page")
    async def page(self, event: AstrMessageEvent, payload_text: str = ""):
        """按角色卡和分镜 JSON 组装并生成漫画单页。"""
        try:
            payload = json.loads(payload_text)
            if not isinstance(payload, dict):
                raise ValueError("参数必须是 JSON 对象")
            assembled, negative = await self._build_page(str(event.get_sender_id()), payload)
        except (json.JSONDecodeError, ValueError) as exc:
            yield event.plain_result(f"参数错误：{exc}")
            return
        except Exception as exc:
            logger.warning("NAI page build failed: %s", exc)
            yield event.plain_result("分镜组装失败，请检查角色卡和分镜数据。")
            return
        async for result in self._draw_prompt(event, assembled, negative):
            yield result

    @filter.command("nai layout")
    async def layout(self, event: AstrMessageEvent):
        """列出提示词组装器支持的版式。"""
        yield event.plain_result("可用版式：" + "、".join(LAYOUTS))

    @filter.command("nai char")
    async def char(self, event: AstrMessageEvent, action: str = "list", name: str = "", card_json: str = ""):
        """角色卡管理：list/show/new/del；new 后附角色 JSON。"""
        user_id = str(event.get_sender_id())
        action = action.strip().lower() or "list"
        name = name.strip()
        try:
            if action == "list":
                names = await self._character_store().list(user_id)
                yield event.plain_result("角色卡：" + ("、".join(names) if names else "（暂无）"))
                return
            if action == "show":
                card = await self._character_store().get(user_id, name)
                if card is None:
                    yield event.plain_result(f"未找到角色卡：{name}")
                    return
                card.pop("slot", None)
                yield event.plain_result(json.dumps(card, ensure_ascii=False, indent=2))
                return
            if action == "new":
                if not name:
                    yield event.plain_result('格式：/nai char new <名称> <JSON>，例：{"ref":"the woman","look":"adult woman, ...","parts":{}}')
                    return
                card = json.loads(card_json)
                if not isinstance(card, dict):
                    raise CharacterStoreError("角色卡内容必须是 JSON 对象")
                await self._character_store().put(user_id, name, card)
                yield event.plain_result(f"角色卡「{name}」已保存。")
                return
            if action in {"del", "delete"}:
                if await self._character_store().delete(user_id, name):
                    yield event.plain_result(f"角色卡「{name}」已删除。")
                else:
                    yield event.plain_result(f"未找到角色卡：{name}")
                return
            yield event.plain_result("用法：/nai char list | show <名称> | new <名称> <JSON> | del <名称>")
        except (json.JSONDecodeError, CharacterStoreError) as exc:
            yield event.plain_result(f"角色卡数据错误：{exc}")
        except Exception as exc:
            logger.warning("NAI character operation failed: %s", exc)
            yield event.plain_result("角色卡操作失败。")

    # ------------------------------------------------------------------
    # 会话 LLM 工具：提示词由会话中的 LLM 细化，插件只管守卫、成本与队列
    # ------------------------------------------------------------------

    async def _character_hint(self, user_id: str) -> str:
        """工具找不到角色卡时给模型的纠错提示（一次就能自己改对）。"""
        names = await self._character_store().list(user_id)
        if not names:
            return (
                "当前没有任何角色卡。请改用 NAI_Generate_Image，把该角色的英文外貌标签直接写进 prompt；"
                "或让用户用 /nai char new <名称> <JSON> 建立角色卡。"
            )
        return "可用角色卡：" + "、".join(names) + "（也可以直接传角色卡的英文称呼 ref）。"

    @_llm_tool()
    async def NAI_Generate_Image(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        character: str = "",
        size: str = "",
        steps: int = 0,
        style: str = "",
        color: bool = False,
        no_sex: bool = False,
        extra_negative: str = "",
    ):
        """把细化好的 NovelAI 提示词画成 1 张图片并直接发给用户。

        用户给中文需求时，**提示词组装由你完成**：先按下面的规范把需求细化成英文标签
        提示词，再调用本工具；不要把中文原句直接传进来，也不要传【1】这类占位符
        （插件不替换占位符，模型会把它们当文字画出来）。

        写提示词规范（照 nai-doujin skill）：
        - 开头一行标签，英文逗号分隔，顺序：分级 → 行为 → 角色 → 风格 → 质量。
        - 只画成年人：角色描述必须写明成年特征（adult woman / mature 等）。插件会在负面里
          强制加回 loli, child, aged down, petite, flat chest，写幼态词没有用。
        - 露骨页开头写 nsfw, rating:explicit, 1boy, faceless male, hetero, adult, uncensored；
          全年龄页写 rating:general。露骨页不要用 girl，写 woman。
        - 黑白页写 monochrome, greyscale, comic, manga, screentone, halftone, lineart,
          speech bubble, sound effects, emphasis lines, dramatic shadows, detailed background；
          彩色页去掉 monochrome, greyscale, screentone, halftone，并把 color 设为 true。
        - 质量词放末尾：very aesthetic, masterpiece, best quality, absurdres。
        - 一页多格时按顺序写：标签行 → 一句英文版式句（几格、从右往左读、主格在哪）→
          `Panel 1 (位置): ...` 逐格描述。每格只写这一格发生的事，不要把整页内容复制进每格。
          四格位置依次是 top, main panel → bottom-right → bottom-middle → bottom-left。
          需要严格合规的多格页时改用 NAI_Generate_Comic_Page。
        - 中文台词画不出来：要对话框就写 `An empty white speech bubble with no text in it.`，
          之后由人贴字；拟声写日文（ずぶっ 这类），呻吟写日文 + ♡。
        - 角色外貌要么用 character 参数指定角色卡，要么把英文外貌标签直接写进 prompt。

        Args:
            prompt(string): 你细化好的英文提示词（标签行，或多格页的标签行 + 版式句 + Panel 描述）。
            character(string): 可选。角色卡名——先调 NAI_List_Characters，把它返回的 name 字段原样传进来
                （传角色卡的英文称呼 ref 也能命中）；插件会把该卡的称呼与英文外貌追加到提示词末尾，
                并把它容易被画错的部位加入负面。用户没提角色卡、或卡库里没有这个角色时留空，
                直接把英文外貌标签写进 prompt。
            size(string): 可选。竖图 / 横图 / 方图 / 2K竖图 / 2K横图 / 2K方图 / 4K竖图 / 4K横图 / 4K方图。
                留空用插件设置的默认尺寸。2K/4K 分别约 15/25 点，是普通图的 15–25 倍，用户没明确
                要求高清时不要用。
            steps(number): 可选。1-50，留空或 0 用插件设置的默认步数。
            style(string): 可选画风预设：fresh / comicDoujin / 2.5d / doujin / galgame / custom / none。
                留空用插件默认画风；none 表示不传画师串。
            color(boolean): 可选。true 表示这一张是彩色页（移除黑白守卫和 color 负面）。
            no_sex(boolean): 可选。true 表示这一页不画插入（只用手或道具），会加入对应负面。
            extra_negative(string): 可选。额外英文负面标签，逗号分隔，叠加在插件的成年守卫之后。
        """
        if not bool(self._config("enable_llm_tool", True)):
            yield "生图工具已在插件设置里关闭（enable_llm_tool），请让用户到插件设置页开启后再试。"
            return
        text = normalize_tags(prompt)
        if not text:
            yield "生成失败：prompt 为空。请先按规范把用户需求细化成英文提示词，再调用本工具。"
            return
        choice = str(style or "").strip()
        if choice and choice not in PRESET_CHOICES:
            yield f"未知画风：{choice}；可选：{'、'.join(PRESET_CHOICES)}"
            return
        try:
            size_value = resolve_size(size, str(self._config("default_size", "竖图")))
            steps_value = resolve_steps(steps, self._config("default_steps", 28))
        except DrawError as exc:
            yield f"生成失败：{exc}"
            return

        user_id = str(event.get_sender_id())
        character_negative = ""
        card_name = str(character or "").strip()
        if card_name:
            card = await self._character_store().get(user_id, card_name)
            if card is None:
                yield f"未找到角色卡：{card_name}。" + await self._character_hint(user_id)
                return
            text, character_negative = merge_character(text, card)
        extra = ", ".join(part for part in (character_negative, normalize_tags(extra_negative)) if part)
        flags = detect_conditionals(text)
        negative = compose_negative(
            extra,
            bw=bool(self._config("bw_default", True)) and not bool(color),
            no_sex=bool(no_sex),
            bubble=flags["bubble"],
            xray=flags["xray"],
            section=flags["section"],
        )

        if str(self._config("image_mode", "direct")) == "prompt_only":
            yield (
                "当前插件设置为只出提示词（image_mode=prompt_only），未提交出图。"
                "请把下面这段提示词原样发给用户：\n" + text
            )
            return
        state = event.get_extra(DRAW_STATE_KEY) if hasattr(event, "get_extra") else None
        if state in {"running", "finished"}:
            yield "本轮消息已经执行过一次图片生成，请勿重复调用本工具；图片成功时已由本工具直接发送。"
            return
        if hasattr(event, "set_extra"):
            event.set_extra(DRAW_STATE_KEY, "running")
        try:
            message, image_bytes = await self._generate_for_tool(
                user_id,
                prompt=text,
                negative=negative,
                size=size_value,
                steps=steps_value,
                style=choice,
            )
        finally:
            if hasattr(event, "set_extra"):
                event.set_extra(DRAW_STATE_KEY, "finished")
        if image_bytes is None:
            yield message
            return
        try:
            await event.send(MessageChain(chain=[Image.fromBytes(image_bytes)]))
        except Exception as exc:
            logger.warning("NAI LLM tool send failed: %s", exc)
            yield f"图片已生成，但发送失败：{str(exc)[:200]}"
            return
        yield message

    @_llm_tool()
    async def NAI_Generate_Comic_Page(
        self,
        event: AstrMessageEvent,
        layout: str = "",
        panels: str = "",
        character: str = "",
        size: str = "",
        steps: int = 0,
        style: str = "",
        color: bool = False,
        no_sex: bool = False,
        explicit: bool = False,
    ):
        """按角色卡与分镜组装一整页漫画并出图（版式句、位置词与负面由插件按 skill 规则生成）。

        你只负责把用户需求细化成**每格一句英文描述**；版式句、四格位置、空白气泡、
        X 光/剖面负面和成年守卫由插件补齐。想让提示词完全自由发挥时用 NAI_Generate_Image。

        Args:
            layout(string): 必填。版式：四格 / 三格主格下 / 两格上下 / 两格斜线 / 五格 / 六格 / 整页大格。
            panels(string): 必填。JSON 数组字符串，编号从 1 连续递增，例如
                [{"no":1,"action":"the rabbit-eared woman looks toward the viewer","shot":"close-up",
                  "sfx":"カランッ","moan":"んっ…","dialogue":true}]
                action 必须是你细化过的英文画面描述；dialogue=true 表示这一格要一个空白气泡
                （之后由人贴中文台词）。
            character(string): 必填。角色卡名——先调 NAI_List_Characters 取其 name 字段（英文称呼 ref 也能命中）。
                卡库里没有这个角色时不要硬填：改用 NAI_Generate_Image 并把外貌写进 prompt，
                或先让用户用 /nai char new 建卡。
            size(string): 可选。同 NAI_Generate_Image；留空用插件设置的默认尺寸。
            steps(number): 可选。1-50，留空或 0 用插件默认步数。
            style(string): 可选画风预设：fresh / comicDoujin / 2.5d / doujin / galgame / custom / none。
            color(boolean): 可选。true 画彩色页。
            no_sex(boolean): 可选。true 表示这一页不画插入（只用手或道具）。
            explicit(boolean): 可选。true 使用露骨标签（nsfw, rating:explicit, 1boy, faceless male）。
        """
        if not bool(self._config("enable_llm_tool", True)):
            yield "生图工具已在插件设置里关闭（enable_llm_tool），请让用户到插件设置页开启后再试。"
            return
        layout = str(layout or "").strip()
        if layout not in LAYOUTS:
            yield f"未知版式：{layout or '(空)'}；可选：{'、'.join(LAYOUTS)}"
            return
        try:
            parsed_panels = json.loads(panels) if str(panels or "").strip() else None
        except json.JSONDecodeError as exc:
            yield f"参数错误：panels 不是合法 JSON（{exc}）；请传 JSON 数组字符串。"
            return
        if not isinstance(parsed_panels, list) or not parsed_panels:
            yield '参数错误：panels 必须是非空 JSON 数组，例如 [{"no":1,"action":"..."}]。'
            return
        choice = str(style or "").strip()
        if choice and choice not in PRESET_CHOICES:
            yield f"未知画风：{choice}；可选：{'、'.join(PRESET_CHOICES)}"
            return
        try:
            size_value = resolve_size(size, str(self._config("default_size", "竖图")))
            steps_value = resolve_steps(steps, self._config("default_steps", 28))
        except DrawError as exc:
            yield f"生成失败：{exc}"
            return
        user_id = str(event.get_sender_id())
        card_name = str(character or "").strip()
        if not card_name:
            yield "参数错误：请指定 character（角色卡名）。" + await self._character_hint(user_id)
            return
        if await self._character_store().get(user_id, card_name) is None:
            yield f"未找到角色卡：{card_name}。" + await self._character_hint(user_id)
            return
        try:
            prompt, negative = await self._build_page(
                user_id,
                {
                    "character": character,
                    "layout": layout,
                    "panels": parsed_panels,
                    "explicit": bool(explicit),
                    "no_sex": bool(no_sex),
                },
                bw=bool(self._config("bw_default", True)) and not bool(color),
            )
        except ValueError as exc:
            yield f"参数错误：{exc}"
            return
        except Exception as exc:
            logger.warning("NAI LLM page build failed: %s", exc)
            yield "分镜组装失败，请检查角色卡和 panels 数据。"
            return

        if str(self._config("image_mode", "direct")) == "prompt_only":
            yield (
                "当前插件设置为只出提示词（image_mode=prompt_only），未提交出图。"
                "请把下面这段提示词原样发给用户：\n" + prompt
            )
            return
        state = event.get_extra(DRAW_STATE_KEY) if hasattr(event, "get_extra") else None
        if state in {"running", "finished"}:
            yield "本轮消息已经执行过一次图片生成，请勿重复调用本工具；图片成功时已由本工具直接发送。"
            return
        if hasattr(event, "set_extra"):
            event.set_extra(DRAW_STATE_KEY, "running")
        try:
            message, image_bytes = await self._generate_for_tool(
                user_id,
                prompt=prompt,
                negative=negative,
                size=size_value,
                steps=steps_value,
                style=choice,
            )
        finally:
            if hasattr(event, "set_extra"):
                event.set_extra(DRAW_STATE_KEY, "finished")
        if image_bytes is None:
            yield message
            return
        try:
            await event.send(MessageChain(chain=[Image.fromBytes(image_bytes)]))
        except Exception as exc:
            logger.warning("NAI LLM tool send failed: %s", exc)
            yield f"图片已生成，但发送失败：{str(exc)[:200]}"
            return
        yield message

    @_llm_tool()
    async def NAI_List_Characters(self, event: AstrMessageEvent):
        """列出当前可用的角色卡，供你挑选角色或核对英文外貌。

        用户提到某个角色、或需要把角色外貌写进提示词时先调用本工具。
        返回 JSON：characters（name 角色卡名 / ref 英文称呼 / look 英文外貌 / uc 易错特征 / parts 部件）、
        layouts（可用版式）、presets（可用画风预设）。
        调用 NAI_Generate_Image / NAI_Generate_Comic_Page 时，character 参数请传 characters[].name
        （传 ref 也能命中）；characters 为空表示卡库为空，此时不要传 character 参数。
        """
        user_id = str(event.get_sender_id())
        store = self._character_store()
        entries = []
        for name in await store.list(user_id):
            card = await store.get(user_id, name)
            if card is None:
                continue
            entries.append(
                {
                    "name": name,
                    "ref": str(card.get("ref", "")),
                    "look": str(card.get("look", "")),
                    "uc": str(card.get("uc", "")),
                    "parts": card.get("parts", {}),
                }
            )
        yield json.dumps(
            {"characters": entries, "layouts": list(LAYOUTS), "presets": list(PRESET_CHOICES)},
            ensure_ascii=False,
        )

    async def terminate(self) -> None:
        if self._queue:
            await self._queue.close()
