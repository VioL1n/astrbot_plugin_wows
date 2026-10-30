"""World of Warships stats queries for AstrBot, powered by Hikari-core v2.

Command routing follows kokomibot's KOOK adapter (app/platforms/kook.py).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

# Imported eagerly so a missing dependency fails plugin loading, which is what
# makes AstrBot install requirements.txt.
import hikari_core  # noqa: E402, F401

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

PLUGIN_NAME = "astrbot_plugin_wows"
VERSION = "0.3.0"

BOUND_ACCOUNT_HINT = "此查询需要你的默认账号。请先发送：/wws bind 游戏昵称"
MISSING_TOKEN_HINT = "插件未配置 yuyuko API Token，请管理员在 AstrBot 插件配置中填写。"
BIND_USAGE = (
    "请发送 /wws bind 游戏昵称（默认服务器：{server}；也可写 /wws bind 欧服 游戏昵称，"
    "昵称含空格时用半角括号括起）。"
)
QUERY_FAILED = "查询失败，请稍后重试。"
TOKEN_ERROR_MARKERS = ("需要token", "token错误")
TOKEN_ERROR_HINT = (
    "查询失败：机器人的查询凭据无效或已过期，请联系机器人管理员更新插件配置。"
)
BOUND_ACCOUNT_COMMANDS = {
    "me",
    "ship",
    "单船",
    "recent",
    "近期",
    "recent_random",
    "近期随机",
    "recent_rank",
    "近期排位",
    "recents",
    "单场近期",
    "roll",
    "随机",
}
BOUND_ACCOUNT_DEFAULT_COMMANDS = {
    "sx",
    "扫雪",
    "ban",
    "封号记录",
    "box",
    "sd",
    "圣诞船池",
}
# Hikari does not extract a server slot for these (country words overlap with
# server keywords), so they always run against the bound account.
SERVER_SLOTLESS_COMMANDS = {"roll", "随机"}
RECENT_QUERY_TYPES = {"recent", "recent_random", "recent_rank"}
SELF_TARGETS = {"", "me", "我", "自己", "self"}
# English heads only: "随机" alone is ambiguous next to Hikari's recent_random.
FUN_ACTIONS = {
    "roll": "roll",
    "随机": "roll",
    "sx": "sx",
    "扫雪": "sx",
    "ban": "ban",
    "封号": "ban",
    "封号记录": "ban",
    "box": "box",
    "sd": "box",
    "圣诞船池": "box",
}
ROLL_FILTER_HINTS = {
    "nation": "国家可选：日本、美国、德国、苏联、英国、法国、意大利、泛亚、泛美、欧洲、英联邦、荷兰、西班牙",
    "ship_type": "舰种可选：驱逐、巡洋、战列、航母、潜艇、辅助航母",
    "tier": "等级可选：1 到 11",
}
LLM_IMAGE_NOTE = (
    "结果图片已直接发送给用户。不要复述或编造任何数据，用一句话简短回应即可。"
)
LLM_TEXT_NOTE = (
    "插件没有发送结果图片，而是已直接向用户发送了以下文字：\n{texts}\n"
    "不要原样重复这些文字，也不要声称已经发送了图片。根据内容简短回应：如果提示需要先绑定账号，"
    "就询问用户的游戏昵称，拿到后调用 wows_bind；如果是选项列表，提醒用户直接回复序号；"
    "如果提示查询凭据无效，只说需要机器人管理员更新配置，不要提及 token、群号或任何获取方式。"
)
LLM_SEND_FAILED_NOTE = (
    "查询结果发送给用户时失败了。告诉用户发送失败、请稍后重试，不要编造任何数据。"
)


def _quote(text: str) -> str:
    """Wrap multi-word values in the half-width parentheses Hikari expects."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return f"({text})" if any(char.isspace() for char in text) else text


@register(PLUGIN_NAME, "tianqixu", "战舰世界战绩查询（Hikari-core v2）", VERSION)
class WowsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self._token = str(config.get("hikari_token", "") or "").strip()
        self._default_server = str(
            config.get("default_server", "亚服") or "亚服"
        ).strip()
        self._query_timeout = int(config.get("query_timeout", 90) or 90)
        self._selection_timeout = int(config.get("selection_timeout", 120) or 120)
        self._engine = None
        self._engine_lock = asyncio.Lock()
        # (session, sender) -> (pending Hikari model, monotonic deadline)
        self._selections: dict[str, tuple[Any, float]] = {}
        # Hikari's first start downloads resources and can take minutes; do it
        # now instead of inside the first query (and its LLM tool timeout).
        self._warmup_task = None
        if self._token:
            try:
                self._warmup_task = asyncio.get_running_loop().create_task(
                    self._warm_up()
                )
            except RuntimeError:
                pass  # No running loop: the first query starts the engine.

    async def _warm_up(self) -> None:
        try:
            engine = await self._get_engine()
            logger.info("[wows] Hikari engine ready")
        except Exception as error:
            logger.warning(f"[wows] Could not start Hikari ({type(error).__name__})")
            return
        problem = await engine.check_token()
        if problem:
            logger.warning(
                f"[wows] yuyuko token 校验失败：{problem}。近期战绩、单船等需要鉴权的查询会失败，"
                "请在插件配置中填写完整有效的 APIKey:Token。"
            )
        else:
            logger.info("[wows] yuyuko token 校验通过")

    async def _get_engine(self):
        if self._engine is None:
            async with self._engine_lock:
                if self._engine is None:
                    from .hikari_engine import HikariEngine

                    data_dir = StarTools.get_data_dir(PLUGIN_NAME)
                    # Importing Hikari pulls in Playwright; keep it off the loop.
                    self._engine = await asyncio.to_thread(
                        HikariEngine, self._token, data_dir
                    )
        return self._engine

    @staticmethod
    def _user_key(event: AstrMessageEvent) -> str:
        return f"{event.get_platform_id()}:{event.get_sender_id()}"

    @staticmethod
    def _selection_key(event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}:{event.get_sender_id()}"

    def _resolve_server(self, engine, server: str) -> str:
        server = (server or "").strip()
        if server and engine.normalize_server(server) is not None:
            return server
        return self._default_server

    @filter.command("wws")
    async def wws(self, event: AstrMessageEvent):
        """战舰世界战绩查询，发送 /wws help 查看全部指令"""
        parts = event.message_str.strip().split(maxsplit=1)
        command = parts[1].strip() if len(parts) == 2 else "help"
        if command.casefold() == "ping":
            yield event.plain_result("pong")
            return
        async for result in self._run(event, command):
            yield result

    @filter.llm_tool(name="wows_query")
    async def wows_query(
        self,
        event: AstrMessageEvent,
        target: str = "me",
        query_type: str = "overall",
        days: int = 0,
        ship: str = "",
        server: str = "",
    ):
        """查询《战舰世界》(World of Warships) 玩家战绩，结果以图片直接发送给用户。当用户想看自己或他人的战舰世界战绩、近期数据、单船数据时调用。

        Args:
            target(string): 查询对象。查用户自己填 "me"；查别人填对方的游戏昵称本身，不要带服务器。
            query_type(string): 查询类型：overall 总战绩；recent 近期；recent_random 近期随机战；recent_rank 近期排位；ship 单船；ship_recent 单船近期。默认 overall。
            days(number): 近期类查询的天数，例如最近 7 天填 7；用户没说天数时填 0。
            ship(string): 船名，查询类型为 ship 或 ship_recent 时必填，例如 大和。
            server(string): 服务器（亚服/欧服/美服/俄服/国服）。只有用户明确提到服务器时才填，否则留空，插件会使用默认服务器。
        """
        engine = None
        if self._token:
            try:
                engine = await self._get_engine()
            except Exception as error:
                logger.warning(
                    f"[wows] Could not start Hikari ({type(error).__name__})"
                )
                await self._send_to_user(event, event.plain_result(QUERY_FAILED))
                yield "查询失败，请让用户稍后重试。"
                return
        command, problem = (
            self._build_query_command(engine, target, query_type, days, ship, server)
            if engine is not None
            else ("help", None)
        )
        if problem:
            yield problem
            return
        logger.info(f"[wows] wows_query -> {command}")
        async for result in self._run_for_llm(event, command):
            yield result

    @filter.llm_tool(name="wows_fun")
    async def wows_fun(
        self,
        event: AstrMessageEvent,
        action: str,
        target: str = "me",
        server: str = "",
        nation: str = "",
        ship_type: str = "",
        tier: str = "",
    ):
        """《战舰世界》(World of Warships) 娱乐功能，结果直接发送给用户。当用户想随机抽一条船出港（roll 船）、看扫雪收益、查封号记录或圣诞船池时调用。

        Args:
            action(string): 功能：roll 随机抽一条船出港；sx 扫雪（圣诞活动）收益；ban 封号记录（仅支持国服）；box 圣诞船池。
            target(string): sx/ban/box 的查询对象。查用户自己填 "me"；查别人填对方的游戏昵称本身，不要带服务器。roll 总是用用户自己的账号，忽略此项。
            server(string): sx/ban/box 的服务器（亚服/欧服/美服/俄服/国服）。只有用户明确提到服务器时才填，否则留空，插件会使用默认服务器。
            nation(string): 仅 roll 使用，限定国家，例如 日本、美国、德国、苏联；用户没说就留空。
            ship_type(string): 仅 roll 使用，限定舰种：驱逐、巡洋、战列、航母、潜艇；用户没说就留空。
            tier(string): 仅 roll 使用，限定等级 1-11，例如 10；用户没说就留空。
        """
        engine = None
        if self._token:
            try:
                engine = await self._get_engine()
            except Exception as error:
                logger.warning(
                    f"[wows] Could not start Hikari ({type(error).__name__})"
                )
                await self._send_to_user(event, event.plain_result(QUERY_FAILED))
                yield "查询失败，请让用户稍后重试。"
                return
        command, problem = (
            self._build_fun_command(
                engine, action, target, server, nation, ship_type, tier
            )
            if engine is not None
            else ("help", None)
        )
        if problem:
            yield problem
            return
        logger.info(f"[wows] wows_fun -> {command}")
        async for result in self._run_for_llm(event, command):
            yield result

    @filter.llm_tool(name="wows_bind")
    async def wows_bind(self, event: AstrMessageEvent, nickname: str, server: str = ""):
        """把用户自己的《战舰世界》游戏账号绑定到当前聊天账号，绑定后才能查询"我的战绩"。

        Args:
            nickname(string): 用户的游戏昵称本身，不要带服务器。
            server(string): 服务器（亚服/欧服/美服/俄服/国服）。只有用户明确提到服务器时才填，否则留空，插件会使用默认服务器。
        """
        nickname = (nickname or "").strip()
        if not nickname:
            yield "缺少游戏昵称，请先问用户的游戏昵称。"
            return
        server_name = self._default_server
        if self._token:
            try:
                engine = await self._get_engine()
            except Exception as error:
                logger.warning(
                    f"[wows] Could not start Hikari ({type(error).__name__})"
                )
                await self._send_to_user(event, event.plain_result(QUERY_FAILED))
                yield "绑定失败，请让用户稍后重试。"
                return
            # Tolerate "亚服 昵称" even though the tool asks for the bare nickname.
            first, _, rest = nickname.partition(" ")
            if rest.strip() and engine.normalize_server(first) is not None:
                server, nickname = first, rest.strip()
            server_name = self._resolve_server(engine, server)
        command = f"bind {server_name} {_quote(nickname)}"
        logger.info(f"[wows] wows_bind -> {command}")
        async for result in self._run_for_llm(event, command):
            yield result

    async def _send_to_user(self, event: AstrMessageEvent, result) -> bool:
        """Send a result from inside an LLM tool.

        AstrBot's own tool "direct result" path did not reach the platform in
        practice (aiocqhttp), so tools send through event.send like the final
        LLM reply does, and only hand text back to the LLM.
        """
        chain = list(getattr(result, "chain", None) or [])
        if not chain:
            return False
        kinds = ", ".join(
            f"Plain({len(c.text)} chars)"
            if isinstance(c, Comp.Plain)
            else type(c).__name__
            for c in chain
        )
        try:
            await event.send(MessageChain(chain=chain))
        except Exception as error:
            logger.warning(
                f"[wows] Tool send failed to {event.unified_msg_origin}: "
                f"{kinds} ({type(error).__name__})"
            )
            return False
        logger.info(f"[wows] Tool sent to {event.unified_msg_origin}: {kinds}")
        return True

    async def _run_for_llm(self, event: AstrMessageEvent, command: str):
        """Send results to the user, then tell the LLM what was actually sent."""
        sent_image = False
        failed = False
        texts: list[str] = []
        async for result in self._run(event, command):
            if not await self._send_to_user(event, result):
                failed = True
                continue
            for component in result.chain:
                if isinstance(component, Comp.Image):
                    sent_image = True
                elif isinstance(component, Comp.Plain) and component.text.strip():
                    texts.append(component.text.strip())
        if sent_image:
            yield LLM_IMAGE_NOTE
        elif texts:
            yield LLM_TEXT_NOTE.format(texts="\n".join(texts))
        elif failed:
            yield LLM_SEND_FAILED_NOTE
        else:
            yield LLM_TEXT_NOTE.format(texts="（无内容）")

    def _build_query_command(
        self, engine, target: str, query_type: str, days, ship: str, server: str
    ) -> tuple[str, str | None]:
        """Translate structured tool arguments into a Hikari command."""
        query_type = (query_type or "overall").strip().casefold()
        try:
            days = max(0, min(int(float(days or 0)), 999))
        except (TypeError, ValueError):
            days = 0

        parts: list[str] = []
        ship_name = ""
        if query_type in {"ship", "ship_recent"}:
            if not (ship or "").strip():
                return "", "缺少船名，请先问用户要查哪条船。"
            # Hikari reads "<nickname> <ship>" in that order once the server is
            # extracted, so the ship name goes after the identity below.
            ship_name = _quote(ship)
            parts.append("ship")
            if query_type == "ship_recent":
                parts.append("recent")
        elif query_type in RECENT_QUERY_TYPES:
            parts.append(query_type)
        else:
            query_type = "overall"

        parts += self._identity_parts(engine, target, server)
        if ship_name:
            parts.append(ship_name)

        if days and (query_type in RECENT_QUERY_TYPES or query_type == "ship_recent"):
            parts.append(str(days))
        return " ".join(parts), None

    def _identity_parts(self, engine, target: str, server: str) -> list[str]:
        """Hikari's identity slot: "me" or "<server> <nickname>"."""
        target = (target or "").strip()
        if target.casefold() in SELF_TARGETS:
            return ["me"]
        # Tolerate "亚服 昵称" even though the tools ask for the bare nickname.
        first, _, rest = target.partition(" ")
        if rest.strip() and engine.normalize_server(first) is not None:
            server, target = first, rest
        return [self._resolve_server(engine, server), _quote(target)]

    def _build_fun_command(
        self,
        engine,
        action: str,
        target: str,
        server: str,
        nation: str,
        ship_type: str,
        tier,
    ) -> tuple[str, str | None]:
        """Translate wows_fun arguments into a Hikari command."""
        head = FUN_ACTIONS.get(str(action or "").strip().casefold())
        if head is None:
            return "", "action 只能是 roll、sx、ban 或 box，请按用户意图重新选择。"
        if head != "roll":
            return " ".join([head, *self._identity_parts(engine, target, server)]), None

        # Hikari silently drops filter words it does not know, so check them
        # here and let the LLM correct itself instead of rolling unfiltered.
        parts = [head]
        for kind, value in (
            ("nation", nation),
            ("ship_type", ship_type),
            ("tier", tier),
        ):
            value = str(value or "").strip()
            if kind == "tier" and value[:1] in {"t", "T"} and value[1:].isdecimal():
                value = value[1:]
            if not value:
                continue
            canonical = engine.normalize_roll_filter(kind, value)
            if canonical is None:
                return (
                    "",
                    f"无法识别“{value}”。{ROLL_FILTER_HINTS[kind]}。请改正后重试，或询问用户。",
                )
            parts.append(canonical)
        return " ".join(parts), None

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_selection_reply(self, event: AstrMessageEvent):
        """Handle a numbered reply to a pending Hikari selection."""
        key = self._selection_key(event)
        pending = self._selections.get(key)
        if pending is None:
            return
        if pending[1] <= time.monotonic():
            self._selections.pop(key, None)
            return

        text = event.message_str.strip()
        if text == "取消":
            self._selections.pop(key, None)
            event.stop_event()
            yield event.plain_result("已取消。")
            return
        if not text.isdecimal():
            return

        self._selections.pop(key, None)
        event.stop_event()
        try:
            engine = await self._get_engine()
            result = await asyncio.wait_for(
                engine.choose(pending[0], int(text)), timeout=self._query_timeout
            )
            async for reply in self._send_result(event, result):
                yield reply
        except Exception as error:
            logger.warning(
                f"[wows] Could not process selection ({type(error).__name__})"
            )
            yield event.plain_result(QUERY_FAILED)

    async def _run(self, event: AstrMessageEvent, command: str):
        """Shared entry for /wws and the LLM tools."""
        self._selections.pop(self._selection_key(event), None)
        if not self._token:
            yield event.plain_result(MISSING_TOKEN_HINT)
            return
        try:
            async for result in self._process_command(event, command):
                yield result
        except Exception as error:
            # Hikari errors can carry credential-bearing URLs; log the type only.
            logger.warning(f"[wows] Could not process command ({type(error).__name__})")
            yield event.plain_result(QUERY_FAILED)

    async def _process_command(self, event: AstrMessageEvent, command: str):
        engine = await self._get_engine()
        user_key = self._user_key(event)
        bot_id = event.get_self_id() or ""
        group_id = event.get_group_id() or None

        command_parts = command.split(maxsplit=1)
        command_head = command_parts[0].casefold()
        if command_head in {"bind", "绑定", "set"}:
            rest = command_parts[1].strip() if len(command_parts) == 2 else ""
            first, _, remainder = rest.partition(" ")
            if engine.normalize_server(first) is not None:
                server_name, nickname = first, remainder.strip()
            else:
                server_name, nickname = self._default_server, rest
            if not nickname:
                yield event.plain_result(BIND_USAGE.format(server=self._default_server))
                return
            message = await asyncio.wait_for(
                engine.bind_account(user_key, bot_id, group_id, server_name, nickname),
                timeout=self._query_timeout,
            )
            yield event.plain_result(message)
            return

        # Self queries need the local binding; an explicit server + nickname
        # (without "me") is a public lookup of another player.
        command_arguments = command.split()[1:]
        has_explicit_me = any(
            argument.casefold() == "me" for argument in command_arguments
        )
        has_explicit_server = any(
            engine.normalize_server(argument) is not None
            for argument in command_arguments
        )
        if command_head in SERVER_SLOTLESS_COMMANDS:
            uses_bound_account = True
        elif command_head in BOUND_ACCOUNT_COMMANDS | BOUND_ACCOUNT_DEFAULT_COMMANDS:
            uses_bound_account = has_explicit_me or not has_explicit_server
        else:
            uses_bound_account = False

        if uses_bound_account:
            result = await asyncio.wait_for(
                engine.execute_bound(user_key, bot_id, group_id, command),
                timeout=self._query_timeout,
            )
            if result is None:
                yield event.plain_result(BOUND_ACCOUNT_HINT)
                return
        else:
            result = await asyncio.wait_for(
                engine.execute(user_key, bot_id, group_id, command),
                timeout=self._query_timeout,
            )
        async for reply in self._send_result(event, result):
            yield reply

    async def _send_result(self, event: AstrMessageEvent, result):
        waiting = result.Status == "wait"
        if waiting:
            self._selections[self._selection_key(event)] = (
                result,
                time.monotonic() + self._selection_timeout,
            )
        data = result.Output.Data
        if isinstance(data, bytes):
            yield event.chain_result([Comp.Image.fromBytes(data)])
        elif isinstance(data, str) and any(
            marker in data for marker in TOKEN_ERROR_MARKERS
        ):
            # yuyuko's 401 text points users at a QQ group to get a token; that is
            # the bot owner's job, so show a neutral message instead.
            logger.warning("[wows] yuyuko rejected the configured token (401)")
            yield event.plain_result(TOKEN_ERROR_HINT)
        elif data is not None:
            yield event.plain_result(str(data))
        else:
            yield event.plain_result("查询没有返回可显示的内容。")
        if waiting:
            yield event.plain_result(
                f"请在 {self._selection_timeout} 秒内由同一用户直接回复选项序号"
                "（群聊中如无响应请 @机器人 后回复），发送“取消”可退出。"
            )

    async def terminate(self):
        if self._warmup_task is not None and not self._warmup_task.done():
            self._warmup_task.cancel()
        if self._engine is not None:
            await self._engine.close()
