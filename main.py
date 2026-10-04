from __future__ import annotations

import json
import os
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

from .core.character_store import CharacterStore, CharacterStoreError
from .core.nai_client import NaiClient
from .core.pricing import cost_for_size
from .core.prompt_builder import LAYOUTS, base_negative, build_page_prompt
from .core.queue import DrawQueue, QueueFullError


@register("astrbot_plugin_nai_cartoon", "elegy-snow", "NovelAI 同人漫画单页生成", "0.1.0")
class NaiDoujinPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._queue: DrawQueue | None = None

    def _config(self, key: str, default: Any) -> Any:
        try:
            return self.context.get_config().get(key, default)
        except Exception:
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
        try:
            settings = await self._client().settings()
            logger.info("NAI station settings loaded (costPerImage=%s)", settings.get("costPerImage", "unknown"))
        except Exception as exc:
            logger.warning("NAI station probe failed: %s", exc)

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
                yield event.plain_result("尚未绑定密钥。请使用 /nai key <密钥>。")
                return
            try:
                account = await self._client().me(token)
            except Exception as exc:
                logger.warning("NAI account lookup failed: %s", exc)
                yield event.plain_result("查询失败，请检查密钥和站点配置。")
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
        """查询站点额度。"""
        token = await self._get_token(str(event.get_sender_id()))
        if not token:
            yield event.plain_result("请先使用 /nai key <密钥> 绑定密钥。")
            return
        try:
            account = await self._client().me(token)
            balance = account.get("balance", account.get("anlas", "未知"))
            yield event.plain_result(f"当前余额：{balance} 点。")
        except Exception as exc:
            logger.warning("NAI quota lookup failed: %s", exc)
            yield event.plain_result("额度查询失败，请检查密钥和站点配置。")

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

    async def _draw_prompt(self, event: AstrMessageEvent, prompt: str):
        prompt = prompt.strip()
        if not prompt:
            yield event.plain_result("请提供提示词。")
            return
        token = await self._get_token(str(event.get_sender_id()))
        if not token:
            yield event.plain_result("请先使用 /nai key <密钥> 绑定密钥。")
            return
        if str(self._config("image_mode", "direct")) == "prompt_only":
            yield event.plain_result(prompt)
            return

        size = str(self._config("default_size", "竖图"))
        model = str(self._config("default_model", "nai-diffusion-4-5-full"))
        steps = int(self._config("default_steps", 28))
        artist = str(self._config("custom_artist", "") if self._config("default_artist_preset", "doujin") == "custom" else self._config("default_artist_preset", "doujin"))
        bw = bool(self._config("bw_default", True))
        negative = base_negative(bw)
        try:
            cost = cost_for_size(size, model, steps)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        if bool(self._config("quota_precheck", True)):
            try:
                account = await self._client().me(token)
                balance = account.get("balance")
                if isinstance(balance, (int, float)) and balance < cost:
                    yield event.plain_result(f"额度不足：本次需要 {cost} 点，当前余额 {balance} 点。")
                    return
            except Exception as exc:
                logger.warning("NAI quota precheck failed: %s", exc)
                yield event.plain_result("额度预检失败，未提交任务。请检查密钥和站点状态。")
                return

        if self._queue is None:
            yield event.plain_result("生成队列尚未就绪，请稍后重试。")
            return
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
            future = await self._queue.submit(operation)
            yield event.plain_result(f"任务已提交到本机队列，预计费用 {cost} 点。")
            job, filename = await future
            try:
                yield event.chain_result([Image.fromFileSystem(filename)])
            finally:
                try:
                    os.unlink(filename)
                except OSError:
                    pass
        except QueueFullError:
            yield event.plain_result("排队已满，稍后再试。")
        except Exception as exc:
            logger.warning("NAI draw failed: %s", exc)
            yield event.plain_result(f"生成失败：{str(exc)[:300]}")

    @filter.command("nai draw")
    async def draw(self, event: AstrMessageEvent, prompt: str = ""):
        """直接按提示词生成单张图片。"""
        async for result in self._draw_prompt(event, prompt):
            yield result

    async def _build_page(self, user_id: str, payload: dict[str, Any]) -> tuple[str, str]:
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
            bw=bool(self._config("bw_default", True)),
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
            assembled, _ = await self._build_page(str(event.get_sender_id()), payload)
        except (json.JSONDecodeError, ValueError) as exc:
            yield event.plain_result(f"参数错误：{exc}")
            return
        except Exception as exc:
            logger.warning("NAI page build failed: %s", exc)
            yield event.plain_result("分镜组装失败，请检查角色卡和分镜数据。")
            return
        async for result in self._draw_prompt(event, assembled):
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

    async def terminate(self) -> None:
        if self._queue:
            await self._queue.close()
