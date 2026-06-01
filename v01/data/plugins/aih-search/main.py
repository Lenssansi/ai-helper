"""aih-search —— ai-helper 联网搜索 LLM tool。

设计原则:
- 注册一个 LLM-callable tool: aih_web_search(query, max_results)
- 后端目前用 Tavily(future-ready:换 backend 不动 tool 接口)
- API key + 代理配置从 v01/data/aih-config.json 读取
- 代理:支持从 config / 环境变量 AIH_PROXY 取,后续 DD4 mihomo 起来后会自动注入
- VPN 不通时优雅降级:tool 返回错误说明而非抛崩

LLM-callable tool 跟 slash command 不同 —— 这是给 LLM 函数调用用的,
人格描述里只要写"你可以联网搜索",LLM 就会按需调 aih_web_search。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

TAVILY_ENDPOINT = "https://api.tavily.com/search"
TAVILY_TIMEOUT = 15.0


def _load_aih_config() -> dict[str, Any]:
    """读 v01/data/aih-config.json,失败时返回空字典(不抛)。

    config 文件不进 git(v01/data/* 已被 .gitignore 拦)。
    示例:
      {
        "tavily_api_key": "tvly-xxxxx",
        "proxy": "http://127.0.0.1:7890"   // 可选,VPN 起来后填
      }
    """
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        cfg_path = Path(get_astrbot_data_path()) / "aih-config.json"
    except ImportError:
        cfg_path = Path(__file__).resolve().parents[3] / "data" / "aih-config.json"

    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[aih-search] 读 {cfg_path} 失败:{e}")
        return {}


def _format_results(results: list[dict[str, Any]], query: str) -> str:
    """把 Tavily 结果拼成 LLM-friendly 字符串。"""
    if not results:
        return f"针对 '{query}' 的搜索没有返回任何结果。"

    lines = [f"搜索 '{query}' 的结果(共 {len(results)} 条):"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(无标题)")
        url = r.get("url", "")
        # Tavily 的正文字段叫 content,不是 snippet
        content = r.get("content", "").strip()
        if len(content) > 400:
            content = content[:400] + "…"
        lines.append(f"\n[{i}] {title}\n    {url}\n    {content}")
    return "\n".join(lines)


class Main(star.Star):
    """ai-helper 联网搜索插件。"""

    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        self.context = context
        config = config or {}

        # ---- 主路径:AstrBot dashboard 配置(_conf_schema.json)----
        tavily_cfg = config.get("tavily") or {}
        proxy_cfg = config.get("proxy") or {}
        self._api_key = (tavily_cfg.get("api_key") or "").strip()
        self._proxy_override = (proxy_cfg.get("proxy_override") or "").strip()
        self._max_results_default = int(tavily_cfg.get("max_results") or 7)
        self._search_depth = tavily_cfg.get("search_depth") or "basic"

        # ---- Fallback:老 aih-config.json(向下兼容)----
        if not self._api_key or not self._proxy_override:
            old = _load_aih_config()
            if not self._api_key:
                self._api_key = (old.get("tavily_api_key") or "").strip()
            if not self._proxy_override:
                self._proxy_override = (old.get("proxy") or "").strip()

        # 运行时 proxy:override > AIH_PROXY env > 直连
        self._proxy = self._proxy_override or None

        if not self._api_key:
            logger.warning(
                "[aih-search] tavily_api_key 未配置;请在 dashboard → 插件 → "
                "ai-helper 联网搜索 → 配置 里填,或 v01/data/aih-config.json"
            )
        else:
            logger.info(
                f"[aih-search] 已配置 API key,depth={self._search_depth},"
                f"max={self._max_results_default},proxy={self._proxy or '环境变量/直连'}"
            )

    @filter.command("aih-search-check")
    async def search_check(self, event: AstrMessageEvent) -> None:
        """诊断:确认搜索插件配置情况(给作者用,不是给 LLM)。"""
        lines = ["aih-search 配置自检:"]
        # 自检命令只对作者本人显示,但仍避免输出 key 片段以防截屏外泄
        lines.append(
            f"  API key:      {'✅ 已配置' if self._api_key else '❌ 未配置'}"
        )
        lines.append(f"  代理:         {self._proxy or '直连(无 VPN)'}")
        lines.append(f"  Tavily 端点:  {TAVILY_ENDPOINT}")
        lines.append("")
        if not self._api_key:
            lines.append("缺 API key,请编辑 v01/data/aih-config.json:")
            lines.append('  {"tavily_api_key": "tvly-..."}')
        yield event.plain_result("\n".join(lines))

    @filter.llm_tool(name="aih_web_search")
    async def aih_web_search(
        self, event: AstrMessageEvent, query: str, max_results: int = 7
    ) -> str:
        """联网搜索互联网获取实时信息,返回带链接和摘要的搜索结果。

        Args:
            query(string): 搜索查询关键词,描述你想找什么
            max_results(number): 返回结果数,1-10 之间,默认 7
        """
        if not self._api_key:
            return (
                "[aih-search 错误] Tavily API key 未配置。请告知用户在"
                " v01/data/aih-config.json 填入 tavily_api_key 字段。"
            )

        # clamp 防越界
        try:
            max_results = max(1, min(10, int(max_results)))
        except (TypeError, ValueError):
            max_results = self._max_results_default

        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": self._search_depth,
        }
        # 每次重读 proxy:VPN 状态变化后无需重启 AstrBot
        proxy_now = self._proxy or os.environ.get("AIH_PROXY") or None
        try:
            async with httpx.AsyncClient(
                timeout=TAVILY_TIMEOUT, proxy=proxy_now
            ) as client:
                resp = await client.post(TAVILY_ENDPOINT, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else "?"
            if status == 401:
                return "[aih-search 错误] Tavily API key 无效或已过期,请用户检查 v01/data/aih-config.json。"
            if status == 429:
                return "[aih-search 错误] Tavily 配额耗尽或被限流,请用户稍后再试。"
            return f"[aih-search 错误] Tavily 返回 HTTP {status}。"
        except httpx.ProxyError:
            return (
                "[aih-search 错误] 代理连接失败。如果你正在用 ai-helper 自带 VPN,"
                "请确认 mihomo 已启动;否则在 v01/data/aih-config.json 移除 proxy 字段以直连。"
            )
        except httpx.TimeoutException:
            return "[aih-search 错误] 请求超时(>15s)。网络不稳或代理慢,请用户重试。"
        except Exception as e:  # noqa: BLE001
            return f"[aih-search 错误] 未预期异常:{type(e).__name__}: {e}"

        results = data.get("results", [])
        return _format_results(results, query)
