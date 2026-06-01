"""节点延迟 TCP 直连测试 —— 不经 mihomo,纯 socket。

为啥不走 mihomo:测一个节点起一个子进程,32 节点 32 个进程太重 + 慢。
TCP 直连测节点入口端口能联通 + 延迟毫秒数,作为"节点活着 + 速度排名"
判据足够;真要端到端验证再让用户 /aih-vpn-use 起 mihomo 实跑。
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

# main.py 会把本目录加 sys.path,这里直接 import
import subs as _subs  # type: ignore[import-not-found]


def _node_endpoint(node_dict: dict[str, Any]) -> tuple[str, int] | None:
    server = str(node_dict.get("server") or "").strip()
    try:
        port = int(node_dict.get("port") or 0)
    except (ValueError, TypeError):
        return None
    if not server or port <= 0 or port > 65535:
        return None
    return server, port


def _tcp_latency_sync(server: str, port: int, timeout: float = 4.0) -> int | None:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((server, port), timeout=timeout):
            return int((time.perf_counter() - t0) * 1000)
    except (OSError, socket.gaierror, socket.timeout):
        return None


async def test_one(node_dict: dict[str, Any], timeout: float = 4.0) -> dict[str, Any]:
    ep = _node_endpoint(node_dict)
    if not ep:
        return {
            "node": node_dict.get("name", "?"),
            "ok": False,
            "error": "节点缺 server/port",
        }
    server, port = ep
    ms = await asyncio.to_thread(_tcp_latency_sync, server, port, timeout)
    if ms is None:
        return {
            "node": node_dict.get("name", "?"),
            "ok": False,
            "server": server,
            "port": port,
            "error": "TCP 失败",
        }
    return {
        "node": node_dict.get("name", "?"),
        "ok": True,
        "ms": ms,
        "server": server,
        "port": port,
    }


async def test_all(
    sub_id: str, timeout: float = 4.0, max_concurrency: int = 16
) -> list[dict[str, Any]]:
    """并发测一个订阅下所有节点。返回按 ms 升序的结果(失败的在尾部)。"""
    sub = _subs.get_sub(sub_id)
    if not sub:
        raise ValueError("订阅不存在")
    try:
        import yaml as _yaml

        data = _yaml.safe_load(sub.get("yaml_content", "") or "")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"YAML 解析失败:{e}") from e

    proxies = (data or {}).get("proxies") or (data or {}).get("Proxy") or []
    if not proxies:
        return []

    sem = asyncio.Semaphore(max_concurrency)

    async def _bound(p: dict) -> dict[str, Any]:
        async with sem:
            return await test_one(p, timeout=timeout)

    results = await asyncio.gather(*[_bound(p) for p in proxies if isinstance(p, dict)])
    # 按延迟升序,失败的在后
    results.sort(key=lambda r: (0 if r.get("ok") else 1, r.get("ms", 999999)))
    return results
