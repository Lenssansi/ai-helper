"""aih-balance —— Provider 余额查询。

slash 命令:
  /aih-balance              查所有已配 provider
  /aih-balance <provider_id> 查单个
  /aih-balance-hosts        列出支持的 API host

特性:
- 走 AIH_PROXY(aih-vpn 设置的代理)出墙,直连失败时自动 fallback 无代理
- 不支持的 host(Anthropic/OpenAI 等)显示 "暂无公开端点",不是错误
- 余额 < 5 元 / 10% 时打 ⚠️ 警告
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# 平铺 import(plugin dir 名含连字符)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import query  # type: ignore[import-not-found]

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter


def _provider_view(prov: Any) -> dict[str, Any]:
    """从 Provider 实例提关键字段。"""
    cfg = getattr(prov, "provider_config", {}) or {}
    keys = cfg.get("key") or []
    first_key = keys[0] if isinstance(keys, list) and keys else (keys if isinstance(keys, str) else "")
    return {
        "id": cfg.get("id", "?"),
        "type": cfg.get("type", "?"),
        "api_base": cfg.get("api_base") or "",
        "key": first_key,
    }


def _fmt_money(val: float | None, currency: str) -> str:
    if val is None:
        return "?"
    sym = "¥" if currency == "CNY" else "$"
    return f"{sym}{val:.2f}"


def _fmt_warn(remaining: float | None, total: float | None) -> str:
    """⚠️ 触发条件:余额 < 5 单位 或 剩余率 < 10%。"""
    if remaining is None:
        return ""
    if remaining < 5:
        return " ⚠️ 余额偏低"
    if total and total > 0 and remaining / total < 0.1:
        return " ⚠️ 剩 < 10%"
    return ""


def _fmt_one(pinfo: dict[str, Any], result: dict[str, Any]) -> str:
    pid = pinfo["id"]
    host = pinfo.get("api_base") or "(无 api_base)"
    if not result.get("supported"):
        return f"  • {pid:20s} {host}\n      → {result.get('error', '不支持')}"
    if not result.get("ok"):
        return f"  • {pid:20s} {host}\n      ❌ {result.get('error', '查询失败')}"
    cur = result.get("currency", "?")
    remaining = result.get("remaining")
    total = result.get("total")
    used = result.get("used")
    warn = _fmt_warn(remaining, total)
    line = f"  • {pid:20s} {host}\n      余 {_fmt_money(remaining, cur)}"
    if total is not None:
        line += f" / 总 {_fmt_money(total, cur)}"
    if used is not None:
        line += f"  (用 {_fmt_money(used, cur)})"
    line += warn
    return line


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        logger.info(
            f"[aih-balance] loaded,支持 host: {', '.join(query.supported_hosts())}"
        )

    def _enumerate_providers(self) -> list[dict[str, Any]]:
        try:
            insts = self.context.get_all_providers()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[aih-balance] get_all_providers 失败:{e}")
            return []
        return [_provider_view(p) for p in insts]

    @filter.command("aih-balance-hosts")
    async def cmd_hosts(self, event: AstrMessageEvent):
        """列出支持余额查询的 host。"""
        hosts = query.supported_hosts()
        lines = ["aih-balance 支持的 host:"]
        for h in hosts:
            lines.append(f"  • {h}")
        lines.append("")
        lines.append("不支持(没公开端点):OpenAI / Anthropic / Gemini / Groq / Cerebras / Together…")
        yield event.plain_result("\n".join(lines))

    @filter.command("aih-balance")
    async def cmd_balance(self, event: AstrMessageEvent, provider_id: str = ""):
        """查 provider 余额。无参 = 查全部,有参 = 查指定。"""
        providers = self._enumerate_providers()
        if not providers:
            yield event.plain_result(
                "AstrBot 还没有任何 provider —— 先去 dashboard → 提供商 配一个"
            )
            return

        targets = providers
        if provider_id:
            targets = [p for p in providers if p["id"] == provider_id]
            if not targets:
                yield event.plain_result(
                    f"❌ 没找到 provider_id={provider_id}\n"
                    f"已有:{', '.join(p['id'] for p in providers)}"
                )
                return

        proxy = os.environ.get("AIH_PROXY") or None
        proxy_label = proxy or "直连"
        yield event.plain_result(
            f"查询中…({len(targets)} 个 provider,出口 {proxy_label})"
        )

        # 并发查询所有 targets
        async def _query_one(p: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            result = await asyncio.to_thread(
                query.query_balance, p["api_base"], p["key"], proxy
            )
            return p, result

        results = await asyncio.gather(*[_query_one(p) for p in targets])

        # 排序:有 remaining 的在前(按余额升序),其次 supported=False,最后失败
        def _sort_key(item: tuple[dict, dict]) -> tuple[int, float]:
            _, r = item
            if r.get("ok") and r.get("remaining") is not None:
                return (0, r["remaining"])
            if not r.get("supported"):
                return (1, 0)
            return (2, 0)

        results.sort(key=_sort_key)
        lines = [f"Provider 余额(共 {len(results)} 个):"]
        for p, r in results:
            lines.append(_fmt_one(p, r))
        yield event.plain_result("\n".join(lines))
