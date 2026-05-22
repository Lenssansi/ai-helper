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

# ensure_proxy 在「内核没装」时返回这个 sentinel(而不是一长串说明)。
# 上层据此识别:不是普通错误,而是「该按需下载内核组件了」,前端弹惰性提示。
CORE_MISSING = "__MIHOMO_CORE_MISSING__"

# ---- 内核按需下载 ----
# 设计:ai-helper 本体绝不打包 mihomo。用户真正用到走 VPN 的功能、且内核
# 恰好缺失时,才在「他自己的机器上」从 mihomo 官方 GitHub 下载。下载源锁死
# 官方域名 + https,且对下载到的字节做 SHA256 校验(下方 _CORE_SHA256),
# 任何镜像/中间人篡改都会被这一步挡下。校验通过才落地、再 `-v` 实跑确认。
_CORE_VERSION = "v1.19.25"
_CORE_ASSET = f"mihomo-windows-amd64-compatible-{_CORE_VERSION}.zip"
# 该 zip 的官方 SHA256(2026-05 核对 v1.19.25);换版本必须同步更新
_CORE_SHA256 = (
    "e4bc371cd449028e65e7f8b4d63b1ac4cdfa3cd008f05af5d42a77d63935f94b"
)
_CORE_GH = (
    "https://github.com/MetaCubeX/mihomo/releases/download/"
    f"{_CORE_VERSION}/{_CORE_ASSET}"
)
# 官方直连优先;连不上(国内常见)→ GitHub 加速镜像兜底。
# 无论走哪条,下载字节都要过 _CORE_SHA256 校验,镜像不可信也无妨。
_CORE_SOURCES = [
    _CORE_GH,
    f"https://gh-proxy.com/{_CORE_GH}",
    f"https://ghproxy.net/{_CORE_GH}",
]


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
    新协议必填项(由 node_dict 自带)。

    安全关键:
    - bind-address: 127.0.0.1 + allow-lan: false —— 子代理端口只对本机
      监听。绝不能让局域网/外网用上这个代理(否则就成了「开放代理」,既
      可能被陌生人蹭来翻墙,也让你在法律上从「自用」滑向「为他人提供
      代理服务」)。本软件的定位是「仅服务本机 API 调用」,这两行是底线。
    - external-controller: "" —— 关掉 mihomo 的 RESTful 控制接口,
      不暴露任何可远程操控 mihomo 的端口。"""
    import yaml as _yaml
    name = str(node_dict.get("name", "PROXY_NODE"))
    cfg_obj: dict[str, Any] = {
        "mixed-port": http_port,
        "bind-address": "127.0.0.1",  # 子代理仅本机可用
        "allow-lan": False,           # 绝不对局域网开放
        "mode": "rule",
        "log-level": "silent",
        "external-controller": "",    # 关闭 mihomo 控制接口
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
            # 不返回长说明 —— 返回 sentinel,让上层弹「按需下载内核」惰性提示
            return None, CORE_MISSING

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


def core_installed() -> bool:
    """内核是否已就位。"""
    return find_mihomo_exe() is not None


def install_core() -> dict[str, Any]:
    """按需把 mihomo 内核下到 MIHOMO_DIR。返回 {ok, version?, path?, error?}。

    安全:下载源锁死官方 GitHub(+ 加速镜像);下载到的字节先过 SHA256
    校验(_CORE_SHA256),再解压、落地、`-v` 实跑确认。任何环节不通过都
    不留文件。"""
    import hashlib
    import io
    import zipfile

    if core_installed():
        return {"ok": True, "already": True, "path": find_mihomo_exe()}

    try:
        MIHOMO_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法创建目录 {MIHOMO_DIR}:{e}"}

    import httpx
    data: bytes | None = None
    last_err = ""
    for src in _CORE_SOURCES:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(180.0, connect=20.0),
                follow_redirects=True,
            ) as c:
                r = c.get(src)
            if r.status_code == 200 and r.content:
                data = r.content
                break
            last_err = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:  # noqa: PERF203
            last_err = f"{type(e).__name__}"
            continue
    if not data:
        return {"ok": False,
                "error": f"下载失败({last_err})。换个网络后重试。"}

    # SHA256 校验 —— 镜像/中间人篡改在此被挡下
    got = hashlib.sha256(data).hexdigest().lower()
    if got != _CORE_SHA256:
        return {"ok": False,
                "error": "下载文件校验未通过(哈希不符),已丢弃。"
                         "可能下载源不可信或文件损坏。"}

    # 解压取 exe
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"ok": False, "error": "下载内容不是有效压缩包"}
    exe_member = next(
        (n for n in zf.namelist() if n.lower().endswith(".exe")), None
    )
    if not exe_member:
        return {"ok": False, "error": "压缩包内未找到可执行文件"}

    target = MIHOMO_DIR / "mihomo.exe"
    try:
        with zf.open(exe_member) as fsrc, open(target, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
    except OSError as e:
        return {"ok": False, "error": f"写入失败:{e}"}

    # 实跑 `-v` 确认是能用的真内核
    try:
        r = subprocess.run(
            [str(target), "-v"], capture_output=True, text=True,
            timeout=10, errors="replace",
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        ver = ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        try:
            target.unlink()
        except OSError:
            pass
        return {"ok": False, "error": f"内核无法运行:{e}"}
    low = ver.lower()
    if "mihomo" not in low and "meta" not in low:
        try:
            target.unlink()
        except OSError:
            pass
        return {"ok": False, "error": "内核自检未通过,已删除"}
    return {"ok": True, "path": str(target), "version": ver[:160]}


def status() -> list[dict[str, Any]]:
    """当前在跑的子代理(便于设置页显示/调试)。"""
    out = []
    for key, inst in list(_INSTANCES.items()):
        alive = inst["proc"].poll() is None
        out.append({"key": key, "port": inst["port"], "url": inst["url"],
                     "alive": alive})
    return out
