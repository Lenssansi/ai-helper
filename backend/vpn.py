"""按需启动 mihomo 子代理,仅服务 API(不接管整机网络)。

设计:
- 每个 (sub_id, node) 组合一个 mihomo 实例,端口 7900+ 递增
- 每次调用 ensure_proxy() 复用已有的;不存在则 spawn
- mihomo 二进制位置: D:\\ai-helper\\mihomo\\mihomo.exe(用户自己放)
- 第一次需要时才 spawn(不是软件启动就拉),软件关闭时被 Electron killOurs 清掉
- 没装二进制 → 返回 (None, error_msg),调用方据此报清晰错误

依赖临时配置文件:每个实例一个 ~/.cache/aih-mihomo/<id>/config.yaml
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR
import vpn_store

# 用户应放二进制到这里(可在设置页改)
MIHOMO_DIR = (
    Path(sys.executable).resolve().parent.parent / "mihomo"
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent / "mihomo"
)
MIHOMO_EXE_CANDIDATES = ("mihomo.exe", "mihomo", "clash-meta.exe", "clash-meta")
WORK_BASE = DATA_DIR / "mihomo_work"

# 端口分配起点;避开 7890(常见 Clash 默认)
_PORT_START = 7900
_PORT_END = 7999

# (sub_id, node) -> {"proc": Popen, "port": int, "url": str}
_INSTANCES: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def find_mihomo_exe() -> str | None:
    for name in MIHOMO_EXE_CANDIDATES:
        p = MIHOMO_DIR / name
        if p.is_file():
            return str(p)
    # 也兜底找 PATH
    return shutil.which("mihomo") or shutil.which("clash-meta")


def _free_port() -> int:
    used = {info["port"] for info in _INSTANCES.values()}
    for p in range(_PORT_START, _PORT_END):
        if p in used:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("没有空闲端口可用(7900-7999)")


def _extract_node_dict(yaml_text: str, node_name: str) -> dict | None:
    """用 PyYAML 解析订阅,按 name 精确匹配返回该节点的 dict。
    比之前的行级正则更可靠 —— inline flow `- { name: ..., type: anytls }`
    / 引号转义 / 中文键值这些情况一锅端。"""
    try:
        import yaml as _yaml
        data = _yaml.safe_load(yaml_text or "")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    proxies = data.get("proxies") or data.get("Proxy") or []
    for p in proxies:
        if isinstance(p, dict) and str(p.get("name", "")).strip() == node_name:
            return p
    return None


def _build_config(http_port: int, node_dict: dict) -> str:
    """生成最小可用的 mihomo 配置:仅 HTTP 入站,出站固定到指定节点。
    用 PyYAML dump 保证缩进/转义正确,顺便补 client-fingerprint 等
    新协议必填项(由 node_dict 自带)。"""
    import yaml as _yaml
    name = str(node_dict.get("name", "PROXY_NODE"))
    cfg_obj: dict[str, Any] = {
        "mixed-port": http_port,
        "mode": "rule",
        "log-level": "silent",
        "external-controller": "",
        "proxies": [node_dict],
        "rules": [f"MATCH,{name}"],
    }
    return _yaml.dump(cfg_obj, allow_unicode=True, sort_keys=False)


def ensure_proxy(sub_id: str, node: str) -> tuple[str | None, str]:
    """确保 (sub_id, node) 的子代理在跑,返回 (http_proxy_url, error)。
    成功时 error == '';失败 url=None error 非空。"""
    if not sub_id or not node:
        return None, "VPN 订阅或节点未指定"
    key = f"{sub_id}::{node}"
    with _LOCK:
        inst = _INSTANCES.get(key)
        if inst and inst["proc"].poll() is None:
            return inst["url"], ""
        # 已死 → 重建
        if inst:
            _INSTANCES.pop(key, None)

        exe = find_mihomo_exe()
        if not exe:
            return None, (
                f"未找到 mihomo 二进制。请把 mihomo.exe 放到 {MIHOMO_DIR}"
                "(或将其加入 PATH)。可去 https://github.com/MetaCubeX/mihomo "
                "下载对应平台的 release。"
            )

        sub = vpn_store.get_sub_internal(sub_id)
        if not sub:
            return None, f"订阅不存在:{sub_id}"
        node_dict = _extract_node_dict(sub.get("yaml_content", ""), node)
        if not node_dict:
            return None, f"订阅 {sub_id} 里未找到节点 '{node}'"

        try:
            port = _free_port()
        except RuntimeError as e:
            return None, str(e)

        work = WORK_BASE / key.replace("/", "_").replace(":", "_")
        work.mkdir(parents=True, exist_ok=True)
        cfg_path = work / "config.yaml"
        cfg_path.write_text(_build_config(port, node_dict), encoding="utf-8")

        try:
            proc = subprocess.Popen(
                [exe, "-d", str(work), "-f", str(cfg_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(0x08000000 if os.name == "nt" else 0),  # NO_WINDOW
            )
        except OSError as e:
            return None, f"启动 mihomo 失败:{e}"

        # 等端口起来(最多 3s)
        ok = False
        for _ in range(30):
            if proc.poll() is not None:
                break
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                try:
                    s.connect(("127.0.0.1", port))
                    ok = True
                    break
                except OSError:
                    time.sleep(0.1)
        if not ok:
            try:
                proc.kill()
            except OSError:
                pass
            return None, "mihomo 起来了但端口没监听(可能配置/二进制不兼容)"

        url = f"http://127.0.0.1:{port}"
        _INSTANCES[key] = {"proc": proc, "port": port, "url": url}
        return url, ""


def shutdown_all() -> None:
    """软件退出时调用(由 main.py atexit / Electron killOurs 兜底)。"""
    with _LOCK:
        for inst in _INSTANCES.values():
            try:
                inst["proc"].kill()
            except OSError:
                pass
        _INSTANCES.clear()


def status() -> list[dict[str, Any]]:
    """当前在跑的子代理(便于设置页显示/调试)。"""
    out = []
    for key, inst in list(_INSTANCES.items()):
        alive = inst["proc"].poll() is None
        out.append({"key": key, "port": inst["port"], "url": inst["url"],
                     "alive": alive})
    return out
