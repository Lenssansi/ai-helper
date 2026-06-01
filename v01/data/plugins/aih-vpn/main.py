"""aih-vpn —— ai-helper VPN/mihomo 插件入口。

slash 命令:
  /aih-vpn-install         内核装/更/重装(SHA256 验签)
  /aih-vpn-version         报内核版本
  /aih-vpn-status          报当前 mihomo 实例和 AIH_PROXY 值
  /aih-vpn-sub-add NAME URL  导入订阅(URL 模式)
  /aih-vpn-sub-list        列订阅
  /aih-vpn-sub-del ID      删订阅
  /aih-vpn-test ID         并发 TCP 测速,排序展示前 10
  /aih-vpn-use ID NODE     起 mihomo,把 AIH_PROXY 设为该实例 URL
  /aih-vpn-stop            关掉所有 mihomo 实例 + 清 AIH_PROXY

AIH_PROXY:
  /aih-vpn-use 成功时写入 os.environ["AIH_PROXY"] = "http://127.0.0.1:79XX",
  aih-search 等 plugin 会自动读这个变量走 VPN 出口。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

# 让本目录可作平铺 import 用(plugin dir 名含连字符,不能走包式 import)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import latency  # type: ignore[import-not-found]
import mihomo  # type: ignore[import-not-found]
import subs  # type: ignore[import-not-found]

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter


def _fmt_age(unix_ts: int | None) -> str:
    if not unix_ts:
        return "?"
    delta = int(time.time()) - int(unix_ts)
    if delta < 60:
        return f"{delta}s 前"
    if delta < 3600:
        return f"{delta // 60}min 前"
    if delta < 86400:
        return f"{delta // 3600}h 前"
    return f"{delta // 86400}d 前"


def _set_aih_proxy(url: str | None) -> None:
    if url:
        os.environ["AIH_PROXY"] = url
    else:
        os.environ.pop("AIH_PROXY", None)


class Main(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        self.context = context
        config = config or {}

        # ---- mihomo_dir_override:换 mihomo 位置 ----
        mihomo_cfg = config.get("mihomo") or {}
        mihomo_override = (mihomo_cfg.get("mihomo_dir_override") or "").strip()
        if mihomo_override:
            os.environ["AIH_MIHOMO_DIR"] = mihomo_override
            import importlib

            importlib.reload(mihomo)

        # 测速参数
        self._test_timeout = int(mihomo_cfg.get("test_timeout_sec") or 4)
        self._test_concurrency = int(mihomo_cfg.get("test_concurrency") or 16)

        # ---- 启动时自动导入订阅 ----
        auto_urls = (config.get("subscriptions") or {}).get("auto_import_urls") or []
        if auto_urls:
            import asyncio

            asyncio.create_task(self._auto_import(auto_urls))

        ver = mihomo.core_version()
        if ver:
            logger.info(f"[aih-vpn] mihomo 已就位:{ver}")
        else:
            logger.warning("[aih-vpn] mihomo 未安装,首次用前请 /aih-vpn-install")

    async def _auto_import(self, urls: list[str]) -> None:
        """启动时尽力导入配置里的订阅 URL。已存在同 URL 跳过,失败只 log。"""
        import asyncio

        try:
            existing = subs.list_subs()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[aih-vpn] 自动导入跳过(列订阅失败):{e}")
            return
        seen = {(s.get("url") or "").strip() for s in existing if s.get("source") == "url"}
        for raw in urls:
            u = (raw or "").strip()
            if not u or u in seen:
                continue
            try:
                rec = await asyncio.to_thread(subs.add_sub, "auto-import", u, None)
                logger.info(
                    f"[aih-vpn] 自动导入订阅:{rec['id']} ({len(rec.get('nodes') or [])} 节点)"
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[aih-vpn] 自动导入 {u[:60]} 失败:{e}")

    # ---------------------------------------------------- core

    @filter.command("aih-vpn-install")
    async def cmd_install(self, event: AstrMessageEvent, force: str = "") -> None:
        """装/更/重装 mihomo 内核。`/aih-vpn-install force` 强制重下。"""
        is_force = force.strip().lower() in ("force", "f", "1", "yes", "y", "重装")
        yield event.plain_result(f"开始装 mihomo 内核(force={is_force})…")
        # 同步实现,可能要几十秒下载;丢线程别堵 event loop
        import asyncio

        result = await asyncio.to_thread(mihomo.install_core, is_force)
        if result.get("ok"):
            if result.get("already"):
                yield event.plain_result(
                    f"✅ 已就位:{result.get('version', '?')}\n"
                    f"路径:{result.get('path')}"
                )
            else:
                yield event.plain_result(
                    f"✅ 安装成功:{result.get('version', '?')}\n"
                    f"路径:{result.get('path')}"
                )
        else:
            yield event.plain_result(f"❌ 安装失败:{result.get('error', '未知')}")

    @filter.command("aih-vpn-version")
    async def cmd_version(self, event: AstrMessageEvent) -> None:
        ver = mihomo.core_version() or "(未安装)"
        bundled = mihomo.bundled_core_version()
        yield event.plain_result(
            f"mihomo 已装版本:{ver}\n"
            f"ai-helper 目标版本: {bundled}\n"
            "用 /aih-vpn-install 安装或更新"
        )

    @filter.command("aih-vpn-status")
    async def cmd_status(self, event: AstrMessageEvent) -> None:
        running = mihomo.status()
        cur_proxy = os.environ.get("AIH_PROXY", "(未设置)")
        lines = [f"AIH_PROXY = {cur_proxy}"]
        if not running:
            lines.append("当前无 mihomo 实例运行。")
        else:
            lines.append(f"实例数:{len(running)}")
            for i, r in enumerate(running, 1):
                alive = "✓" if r["alive"] else "✗"
                lines.append(f"  [{i}] {r['key']}  port={r['port']}  {alive}")
        yield event.plain_result("\n".join(lines))

    @filter.command("aih-vpn-stop")
    async def cmd_stop(self, event: AstrMessageEvent) -> None:
        n = mihomo.shutdown_all()
        _set_aih_proxy(None)
        yield event.plain_result(f"✅ 关掉 {n} 个 mihomo 实例,AIH_PROXY 已清。")

    # ---------------------------------------------------- subs

    @filter.command("aih-vpn-sub-add")
    async def cmd_sub_add(
        self, event: AstrMessageEvent, name: str = "", url: str = ""
    ) -> None:
        """新增 URL 订阅:/aih-vpn-sub-add <名字> <订阅URL>"""
        if not name or not url:
            yield event.plain_result("用法:/aih-vpn-sub-add <名字> <订阅URL>")
            return
        yield event.plain_result(f"拉订阅中:{url[:80]}…(按 UA 链尝试,可能要 10s+)")
        import asyncio

        try:
            rec = await asyncio.to_thread(subs.add_sub, name, url, None)
        except ValueError as e:
            yield event.plain_result(f"❌ {e}")
            return
        yield event.plain_result(
            f"✅ 添加成功\n"
            f"  ID:    {rec['id']}\n"
            f"  名字:  {rec['name']}\n"
            f"  节点数:{len(rec.get('nodes') or [])}\n"
            f"  转换:  {'V2Ray→Clash 已转' if rec.get('converted_from_uri') else '原 Clash YAML'}\n"
            f"\n下一步:/aih-vpn-test {rec['id']}  并发测速找最快节点"
        )

    @filter.command("aih-vpn-sub-list")
    async def cmd_sub_list(self, event: AstrMessageEvent) -> None:
        items = subs.list_subs()
        if not items:
            yield event.plain_result("没有订阅,用 /aih-vpn-sub-add <名字> <URL> 添加。")
            return
        lines = [f"订阅 {len(items)} 个:"]
        for s in items:
            lines.append(
                f"  • {s['id']}  {s.get('name', '?')}  "
                f"({len(s.get('nodes') or [])} 节点, {_fmt_age(s.get('updated'))})"
            )
        yield event.plain_result("\n".join(lines))

    @filter.command("aih-vpn-sub-del")
    async def cmd_sub_del(self, event: AstrMessageEvent, sid: str = "") -> None:
        if not sid:
            yield event.plain_result("用法:/aih-vpn-sub-del <订阅ID>")
            return
        ok = subs.delete_sub(sid)
        yield event.plain_result(f"{'✅ 删除' if ok else '❌ 未找到订阅'}: {sid}")

    @filter.command("aih-vpn-sub-refresh")
    async def cmd_sub_refresh(self, event: AstrMessageEvent, sid: str = "") -> None:
        if not sid:
            yield event.plain_result("用法:/aih-vpn-sub-refresh <订阅ID>")
            return
        yield event.plain_result("刷新中(从原 URL 重拉)…")
        import asyncio

        try:
            rec = await asyncio.to_thread(subs.refresh_sub, sid)
        except ValueError as e:
            yield event.plain_result(f"❌ {e}")
            return
        yield event.plain_result(
            f"✅ 刷新成功 - {rec['name']}\n  节点数:{len(rec.get('nodes') or [])}"
        )

    # ---------------------------------------------------- test + use

    @filter.command("aih-vpn-test")
    async def cmd_test(self, event: AstrMessageEvent, sid: str = "") -> None:
        if not sid:
            yield event.plain_result("用法:/aih-vpn-test <订阅ID>")
            return
        yield event.plain_result("并发 TCP 测速中(最长 4s/节点 × 16 并发)…")
        try:
            results = await latency.test_all(sid, timeout=4.0, max_concurrency=16)
        except ValueError as e:
            yield event.plain_result(f"❌ {e}")
            return
        if not results:
            yield event.plain_result("订阅里没有可测的节点。")
            return
        ok_count = sum(1 for r in results if r.get("ok"))
        lines = [
            f"测了 {len(results)} 个节点,{ok_count} 个可达。前 10 名:"
        ]
        for r in results[:10]:
            if r.get("ok"):
                lines.append(f"  {r['ms']:>5} ms  {r['node']}")
            else:
                lines.append(f"  {'fail':>5}     {r['node']}  ({r.get('error', '?')})")
        if ok_count > 0:
            top = results[0]
            lines.append("")
            lines.append(f"最快:{top['node']}({top['ms']} ms)")
            lines.append(f"开启:/aih-vpn-use {sid} {top['node']}")
        yield event.plain_result("\n".join(lines))

    @filter.command("aih-vpn-use")
    async def cmd_use(
        self, event: AstrMessageEvent, sid: str = "", node: str = ""
    ) -> None:
        if not sid or not node:
            yield event.plain_result("用法:/aih-vpn-use <订阅ID> <节点名>")
            return
        sub = subs.get_sub(sid)
        if not sub:
            yield event.plain_result(f"❌ 订阅不存在:{sid}")
            return
        node_dict = subs.extract_node_dict(sub.get("yaml_content", ""), node)
        if not node_dict:
            yield event.plain_result(
                f"❌ 节点 '{node}' 不在订阅 {sid} 里\n"
                f"提示:/aih-vpn-test {sid} 可看可用节点列表"
            )
            return
        import asyncio

        url, err = await asyncio.to_thread(mihomo.ensure_proxy, sid, node_dict)
        if err == mihomo.CORE_MISSING:
            yield event.plain_result("❌ mihomo 未装,先 /aih-vpn-install")
            return
        if err:
            yield event.plain_result(f"❌ 启 mihomo 失败:{err}")
            return
        _set_aih_proxy(url)
        yield event.plain_result(
            f"✅ {node} 已起,代理 URL = {url}\n"
            f"AIH_PROXY 已设;aih-search 等插件下一次调用将走这条出口。\n"
            f"停止:/aih-vpn-stop"
        )
