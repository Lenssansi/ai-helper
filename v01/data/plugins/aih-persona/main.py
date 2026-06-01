"""aih-persona —— 人格 ↔ LLM provider 绑定。

设计决策:
- **不**直接改 AstrBot 自家的 personas 表 schema,避免后续 AstrBot 升级
  做自己的 migration 时冲突。我们在同一个 data_v4.db 里维护一个独立的
  `aih_persona_provider(persona_id PRIMARY KEY, provider_id, updated_at)`
  映射表,plugin 卸载时不动 personas 数据,只丢自己的小表即可。
- 绑定生效点:`@on_agent_begin` 钩子。该事件在 AstrBot 选 provider 之前
  触发,我们在这里调 `provider_manager.set_provider(..., umo=...)` 做
  会话级临时切换,不污染全局默认。
- 切换粒度按 umo(unified_message_origin)+persona_id 双重判断,
  不同人格交替使用时每次都重新对齐。

slash commands:
  /aih-persona-list       —— 列所有人格 + 当前绑定
  /aih-persona-bind PID VID —— 绑定 persona_id 到 provider_id
  /aih-persona-unbind PID —— 解除绑定
  /aih-persona-check      —— 自检(报当前会话人格 + 应切的 provider)

米花菌后续亲自配:在 WebChat 输 `/aih-persona-list` 看现状,再 bind。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.provider.entities import ProviderType


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS aih_persona_provider (
    persona_id  TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    updated_at  INTEGER DEFAULT (strftime('%s','now'))
)
"""


def _db_path() -> str:
    """指向 AstrBot 主 SQLite,跟 personas/conversations 同一文件。"""
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return str(Path(get_astrbot_data_path()) / "data_v4.db")


def _provider_id(prov: Any) -> str:
    """从 Provider 实例拿 id —— 都是 provider_config['id']。"""
    return getattr(prov, "provider_config", {}).get("id", "?")


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self._db_path = _db_path()
        self._bindings: dict[str, str] = {}
        self._initialized = False
        logger.info(f"[aih-persona] db at {self._db_path}")

    async def _ensure_init(self) -> None:
        """首次访问时建表 + 装载缓存。多次调安全(_initialized 自防御)。"""
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_SQL)
            await db.commit()
            async with db.execute(
                "SELECT persona_id, provider_id FROM aih_persona_provider"
            ) as cur:
                rows = await cur.fetchall()
        self._bindings = {pid: provid for pid, provid in rows}
        self._initialized = True
        logger.info(f"[aih-persona] loaded {len(self._bindings)} bindings")

    async def _persist_binding(self, persona_id: str, provider_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO aih_persona_provider (persona_id, provider_id, updated_at)
                   VALUES (?, ?, strftime('%s','now'))
                   ON CONFLICT(persona_id) DO UPDATE SET
                     provider_id = excluded.provider_id,
                     updated_at  = excluded.updated_at""",
                (persona_id, provider_id),
            )
            await db.commit()
        self._bindings[persona_id] = provider_id

    async def _delete_binding(self, persona_id: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "DELETE FROM aih_persona_provider WHERE persona_id = ?",
                (persona_id,),
            )
            await db.commit()
            removed = cur.rowcount > 0
        self._bindings.pop(persona_id, None)
        return removed

    def _all_persona_ids(self) -> list[str]:
        """AstrBot persona_manager 已在内存里缓存了 personas。"""
        try:
            personas = self.context.persona_manager.personas or []
            return [p.persona_id for p in personas if getattr(p, "persona_id", None)]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[aih-persona] 取 personas 失败:{e}")
            return []

    def _all_provider_ids(self) -> list[str]:
        try:
            return [_provider_id(p) for p in self.context.get_all_providers()]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[aih-persona] 取 providers 失败:{e}")
            return []

    # ---------------------------------------------------------------- commands

    @filter.command("aih-persona-list")
    async def cmd_list(self, event: AstrMessageEvent) -> None:
        """列所有人格 + 当前 provider 绑定。"""
        await self._ensure_init()
        personas = self._all_persona_ids()
        providers = self._all_provider_ids()

        lines = ["aih-persona 绑定一览:"]
        if not personas:
            lines.append("  (AstrBot 还没有任何人格 —— 先去 dashboard → 人格 建一个)")
        for pid in personas:
            bound = self._bindings.get(pid)
            mark = f"→ {bound}" if bound else "(未绑定 → 走全局默认)"
            lines.append(f"  • {pid}: {mark}")
        lines.append("")
        lines.append(
            f"可选 provider_id ({len(providers)} 个): "
            f"{', '.join(providers) if providers else '(尚无 provider,先去 dashboard 添加)'}"
        )
        lines.append("")
        lines.append("用法:")
        lines.append("  /aih-persona-bind <persona_id> <provider_id>")
        lines.append("  /aih-persona-unbind <persona_id>")
        await event.send("\n".join(lines))

    @filter.command("aih-persona-bind")
    async def cmd_bind(
        self,
        event: AstrMessageEvent,
        persona_id: str = "",
        provider_id: str = "",
    ) -> None:
        """绑定:/aih-persona-bind <persona_id> <provider_id>"""
        await self._ensure_init()
        if not persona_id or not provider_id:
            await event.send("用法:/aih-persona-bind <persona_id> <provider_id>")
            return
        providers = self._all_provider_ids()
        if provider_id not in providers:
            await event.send(
                f"❌ 未知 provider_id: {provider_id}\n"
                f"已加载的:{', '.join(providers) or '(无)'}"
            )
            return
        personas = set(self._all_persona_ids())
        if persona_id not in personas:
            await event.send(
                f"❌ 未知 persona_id: {persona_id}\n"
                f"已有的:{', '.join(personas) or '(无)'}"
            )
            return
        await self._persist_binding(persona_id, provider_id)
        await event.send(f"✅ {persona_id} → {provider_id}")

    @filter.command("aih-persona-unbind")
    async def cmd_unbind(
        self, event: AstrMessageEvent, persona_id: str = ""
    ) -> None:
        """解除:/aih-persona-unbind <persona_id>"""
        await self._ensure_init()
        if not persona_id:
            await event.send("用法:/aih-persona-unbind <persona_id>")
            return
        if await self._delete_binding(persona_id):
            await event.send(f"✅ {persona_id} 解除绑定,后续会用全局默认 provider")
        else:
            await event.send(f"{persona_id} 本就未绑定,无操作。")

    @filter.command("aih-persona-check")
    async def cmd_check(self, event: AstrMessageEvent) -> None:
        """自检:报当前会话的人格 + 该用哪个 provider。"""
        await self._ensure_init()
        umo = event.unified_msg_origin
        lines = [f"会话:{umo}"]
        try:
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(
                umo
            )
            if conv_id:
                conv = await self.context.conversation_manager.get_conversation(
                    umo, conv_id
                )
                pid = getattr(conv, "persona_id", None) if conv else None
                lines.append(f"对话 ID:    {conv_id}")
                lines.append(f"当前人格:  {pid or '(未指定 → 全局默认)'}")
                if pid:
                    bound = self._bindings.get(pid)
                    lines.append(f"绑定 provider: {bound or '(未绑定 → 全局默认)'}")
            else:
                lines.append("当前会话还没有对话,无人格上下文。")
        except Exception as e:  # noqa: BLE001
            lines.append(f"读取失败:{e}")
        try:
            curr = self.context.get_using_provider(umo=umo)
            lines.append(f"实际使用:  {_provider_id(curr) if curr else '(None)'}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"取当前 provider 失败:{e}")
        await event.send("\n".join(lines))

    # ----------------------------------------------------------------- hook

    @filter.on_agent_begin()
    async def hook_swap_provider(
        self,
        event: AstrMessageEvent,
        run_context: Any,  # ContextWrapper[AstrAgentContext]
    ) -> None:
        """每次 Agent 启动前,根据 conversation.persona_id 调对应 provider。

        失败/异常都吞掉只 log warning,不中断主链路 —— 用户消息能正常发出去,
        只是没切到绑定 provider,会走全局默认。
        """
        await self._ensure_init()
        if not self._bindings:
            return

        umo = event.unified_msg_origin
        try:
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(
                umo
            )
            if not conv_id:
                return
            conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            persona_id = getattr(conv, "persona_id", None) if conv else None
            if not persona_id:
                return

            target_provider_id = self._bindings.get(persona_id)
            if not target_provider_id:
                return

            current = self.context.get_using_provider(umo=umo)
            if current and _provider_id(current) == target_provider_id:
                return  # 已是目标 provider,避免无谓抖动

            # 校验目标 provider 真存在(可能已被删掉)
            if target_provider_id not in self._all_provider_ids():
                logger.warning(
                    f"[aih-persona] 人格 {persona_id} 绑定的 provider "
                    f"{target_provider_id} 已不存在,跳过切换"
                )
                return

            await self.context.provider_manager.set_provider(
                target_provider_id, ProviderType.CHAT_COMPLETION, umo=umo
            )
            logger.debug(
                f"[aih-persona] {umo} (persona={persona_id}) "
                f"→ provider {target_provider_id}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[aih-persona] swap failed: {e}")
