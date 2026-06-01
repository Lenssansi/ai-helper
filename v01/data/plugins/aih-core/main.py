"""aih-core —— ai-helper plugin suite 的基础包。

本插件目前只承担"骨架 + 健康自检"职责:
- /aih-hello:验证 plugin 系统能跑(WebChat 输入即触发)
- /aih-version:报 ai-helper / AstrBot / Python 版本,方便排障
- /aih-where:报关键路径,方便确认数据没落到 ~/.astrbot

后续 aih-search / aih-vpn / aih-persona / aih-balance / aih-coding 会作为
独立 plugin 各自一个目录,通过 from astrbot.api import logger 等共享 API
而不是 cross-plugin import(AstrBot 插件之间隔离运行,不强求依赖关系)。
"""

from __future__ import annotations

import sys
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

AIH_VERSION = "0.1.1"  # 跟 app/package.json 的 version 字段保持同步


class Main(star.Star):
    """ai-helper 核心插件入口。"""

    def __init__(self, context: star.Context) -> None:
        self.context = context
        logger.info(f"[aih-core] loaded v{AIH_VERSION}")

    @filter.command("aih-hello")
    async def aih_hello(self, event: AstrMessageEvent) -> None:
        """快速验证 ai-helper 插件已加载,WebChat 输入 /aih-hello 即触发。"""
        await event.send(
            f"✅ ai-helper v{AIH_VERSION} 在线 —— 插件系统工作正常。"
        )

    @filter.command("aih-version")
    async def aih_version(self, event: AstrMessageEvent) -> None:
        """报版本信息,排障用。"""
        try:
            from astrbot.core.config.default import VERSION as ASTRBOT_VER
        except ImportError:
            ASTRBOT_VER = "unknown"
        py = sys.version.split()[0]
        await event.send(
            f"ai-helper:v{AIH_VERSION}\n"
            f"AstrBot: v{ASTRBOT_VER}\n"
            f"Python:  {py}"
        )

    @filter.command("aih-where")
    async def aih_where(self, event: AstrMessageEvent) -> None:
        """报关键路径,确认数据隔离在 v01/ 没落到 ~/.astrbot。"""
        try:
            from astrbot.core.utils.astrbot_path import (
                get_astrbot_data_path,
                get_astrbot_root,
            )

            root = get_astrbot_root()
            data = get_astrbot_data_path()
        except ImportError:
            root = "<astrbot.core.utils.astrbot_path 不可用>"
            data = "<同上>"

        plugin_path = Path(__file__).resolve().parent
        await event.send(
            f"ASTRBOT_ROOT: {root}\n"
            f"DATA_PATH:    {data}\n"
            f"本插件路径:   {plugin_path}"
        )
