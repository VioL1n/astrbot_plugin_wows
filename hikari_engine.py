"""Small bridge between AstrBot and Hikari-core v2.

Ported from kokomibot's KOOK bridge (app/platforms/hikari.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from . import log_patch
from .bindings import UserBinding, UserBindingStore

# Hikari only knows QQ-style platform codes. QQ official bots expose openids
# rather than QQ numbers, so self queries go through the local binding instead.
HIKARI_PLATFORM = "QQ"
# Probing cache/check with an empty body: yuyuko answers 401 for a wrong token,
# 507 when it cannot decode one, and 400 (missing parameters) once auth passed.
TOKEN_REJECTED_CODES = {401, 507}
TOKEN_ACCEPTED_CODE = 400


class HikariEngine:
    """Configure Hikari once and serialize calls to its shared HTTP client."""

    def __init__(self, token: str, data_dir: Path) -> None:
        from hikari_core import set_hikari_config
        from hikari_core.core import http_client
        from hikari_core.core.config import hikari_config
        from hikari_core.features.system import (
            async_update_ship_cache,
            async_update_template,
            check_version,
        )

        # Hikari logs its entire configuration, including the API token. AstrBot
        # shares the same loguru logger, so redact with a global patcher instead
        # of replacing sinks the way the standalone bot did.
        log_patch.install(token)

        # Hikari invokes the Playwright CLI by name when it installs its browser.
        # AstrBot's bundled interpreter directory is not necessarily on PATH.
        python_bin = os.path.dirname(sys.executable)
        if python_bin not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = python_bin + os.pathsep + os.environ.get("PATH", "")

        # Hikari-core still uses the removed httpx `proxies` argument. Keep the
        # SDK's public API intact while adapting its WG client to httpx >= 0.28.
        async def create_client_wg_compatible():
            http_client._client_wg = httpx.AsyncClient(proxy=hikari_config.proxy)
            return http_client._client_wg

        http_client.create_client_wg = create_client_wg_compatible
        data_dir.mkdir(parents=True, exist_ok=True)
        set_hikari_config(
            token=token,
            game_path=str(data_dir),
            command_language="zh",
            auto_rendering=True,
            auto_image=True,
            image_type="jpeg",
            render_error_fallback=True,
        )
        if sys.platform == "darwin":
            # Hikari-core's default macOS glob still expects Chromium.app, but
            # recent Playwright builds install Google Chrome for Testing.app.
            # Use Hikari's own browser-path override to find the installed binary.
            browser_paths_file = data_dir / "browsers-find-executable.json"
            browser_paths = (
                json.loads(browser_paths_file.read_text(encoding="utf-8"))
                if browser_paths_file.exists()
                else {}
            )
            browser_paths.setdefault("chromium", {})["darwin"] = (
                "chromium-*/chrome-mac*/Google Chrome for Testing.app/"
                "Contents/MacOS/Google Chrome for Testing"
            )
            browser_paths_file.write_text(
                json.dumps(browser_paths, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self._lock = asyncio.Lock()
        self._disabled = [check_version, async_update_template, async_update_ship_cache]
        self._bindings = UserBindingStore(data_dir / "user_bindings.sqlite3")

    async def execute(
        self, user_key: str, bot_id: str, group_id: str | None, command: str
    ):
        from hikari_core import init_hikari

        async with self._lock:
            return await init_hikari(
                platform=HIKARI_PLATFORM,
                PlatformId=user_key,
                BotId=bot_id,
                command_text=command,
                GroupId=group_id,
                Ignore_List=self._disabled,
            )

    async def bind_account(
        self,
        user_key: str,
        bot_id: str,
        group_id: str | None,
        server_name: str,
        account_name: str,
    ) -> str:
        """Resolve a game account and bind it to the chat user locally."""
        from hikari_core.core.model import Hikari_Model, UserInfo_Model
        from hikari_core.features.api import get_AccountIdByName

        server = self.normalize_server(server_name)
        if server is None:
            return "服务器名输入错误，请使用亚服、欧服、美服、俄服或国服。"

        account_name = account_name.strip()
        if account_name.startswith("(") and account_name.endswith(")"):
            account_name = account_name[1:-1].strip()
        if not account_name:
            return "请发送 /wws bind 亚服 游戏昵称。"

        context = Hikari_Model(
            UserInfo=UserInfo_Model(
                Platform=HIKARI_PLATFORM,
                PlatformId=user_key,
                BotId=bot_id,
                GroupId=group_id,
            )
        )
        async with self._lock:
            account_id = await get_AccountIdByName(context, server, account_name)
        if account_id is None:
            if context.Status == "failed" and isinstance(context.Output.Data, str):
                return context.Output.Data
            return "查询账号失败，请稍后重试或确认游戏昵称。"

        await asyncio.to_thread(
            self._bindings.set,
            user_key,
            UserBinding(server, account_id, account_name),
        )
        return f"绑定成功：{server_name} {account_name}。发送 /wws me 查询战绩。"

    async def execute_bound(
        self, user_key: str, bot_id: str, group_id: str | None, command: str
    ):
        """Run a self-scoped query with the locally bound game identity."""
        from hikari_core import init_hikari

        binding = await asyncio.to_thread(self._bindings.get, user_key)
        if binding is None:
            return None
        async with self._lock:
            return await init_hikari(
                platform=binding.server,
                PlatformId=str(binding.account_id),
                BotId=bot_id,
                command_text=command,
                GroupId=group_id,
                Ignore_List=self._disabled,
            )

    @staticmethod
    def normalize_server(server_name: str) -> str | None:
        from hikari_core.core.constants import servers

        normalized_name = server_name.casefold()
        return next(
            (
                entry.match_keywords
                for entry in servers
                if normalized_name in (word.casefold() for word in entry.keywords)
            ),
            None,
        )

    @staticmethod
    def normalize_roll_filter(kind: str, value: str) -> str | None:
        """Map a roll filter word to a nation/type/tier keyword Hikari parses.

        Returns the entry's first keyword rather than its match value: the
        Soviet value "Russia" is a server word, not a roll keyword.
        """
        from hikari_core.core import constants

        table = {
            "nation": constants.nations,
            "ship_type": constants.shiptypes,
            "tier": constants.levels,
        }[kind]
        normalized_value = value.casefold()
        return next(
            (
                entry.keywords[0]
                for entry in table
                if normalized_value in (word.casefold() for word in entry.keywords)
            ),
            None,
        )

    async def choose(self, pending, index: int):
        from hikari_core import callback_hikari

        async with self._lock:
            pending.Input.Select_Index = index
            return await callback_hikari(pending)

    async def check_token(self) -> str | None:
        """Probe an authenticated yuyuko endpoint; return a problem or None.

        An empty POST to cache/check needs no account: a valid token gets a
        400 parameter error, an invalid one 401, a missing/garbled one 507.
        """
        from hikari_core import __version__
        from hikari_core.core.config import hikari_config

        url = f"{hikari_config.yuyuko_url}/api/wows/cache/check"
        try:
            async with httpx.AsyncClient(
                timeout=15, proxy=hikari_config.proxy
            ) as client:
                response = await client.post(
                    url,
                    # Same headers as Hikari's own client; without the client
                    # type yuyuko answers 409 before looking at the token.
                    headers={
                        "Authorization": hikari_config.token,
                        "accept": "application/json",
                        "Content-Type": "application/json",
                        "Yuyuko-Client-Type": (
                            f"{hikari_config.yuyuko_type};{__version__};Base64UserInfoImg"
                        ),
                    },
                    json={},
                )
            code = response.json().get("code")
        except Exception as error:
            return f"无法连接 yuyuko 校验 token（{type(error).__name__}）"
        if code in TOKEN_REJECTED_CODES:
            return f"yuyuko 拒绝了该 token（code={code}）"
        if code != TOKEN_ACCEPTED_CODE:
            return f"无法确认 token 是否有效（yuyuko 返回 code={code}）"
        return None

    async def close(self) -> None:
        from hikari_core.Html_Render import shutdown

        await shutdown()
