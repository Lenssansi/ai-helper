"""mihomo 二进制管理 + 子进程生命周期。

从 v0.0.5 backend/vpn.py 直接迁,改动:
- 路径:复用 D:\\ai-helper\\mihomo\\(跟 v0.0.5 共享同一份二进制)
- 工作目录:落到 v01/data/aih-vpn-work/<sub>::<node>/
- httpx 同步下载保留(SHA256 校验后才落盘)
- 拒绝任何 0.0.0.0 / external-controller 暴露

安全基线:
- mixed-port 锁 127.0.0.1
- allow-lan: false
- external-controller: "" (关掉 mihomo 自己的 API)
- 内核包 SHA256 锁定,镜像也要过校验
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

# mihomo 二进制位置查找顺序:
#   1) 环境变量 AIH_MIHOMO_DIR(bootstrap.py 在 packed 模式设置)
#   2) 源码同级 D:\ai-helper\mihomo\(dev 模式 + v0.0.5 共用)
# 路径推算:mihomo.py(0) → aih-vpn(0) → plugins(1) → data(2) → v01(3) → ai-helper(4)
_ENV_MIHOMO = os.environ.get("AIH_MIHOMO_DIR", "").strip()
if _ENV_MIHOMO:
    MIHOMO_DIR = Path(_ENV_MIHOMO)
else:
    _PROJECT_ROOT = Path(__file__).resolve().parents[4]
    MIHOMO_DIR = _PROJECT_ROOT / "mihomo"
MIHOMO_EXE_CANDIDATES = ("mihomo.exe", "mihomo", "clash-meta.exe", "clash-meta")

# 工作目录:跟 AstrBot 数据隔离,v0.0.5 也不会争
def _work_base() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return Path(get_astrbot_data_path()) / "aih-vpn-work"


_PORT_START = 7900
_PORT_END = 7999

# (sub_id, node) -> {"proc": Popen, "port": int, "url": str}
_INSTANCES: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

CORE_MISSING = "__MIHOMO_CORE_MISSING__"

# 内核版本/哈希跟 v0.0.5 保持一致,换版必须同步更新 SHA256
_CORE_VERSION = "v1.19.25"
_CORE_ASSET = f"mihomo-windows-amd64-compatible-{_CORE_VERSION}.zip"
_CORE_SHA256 = "e4bc371cd449028e65e7f8b4d63b1ac4cdfa3cd008f05af5d42a77d63935f94b"
_CORE_GH = (
    "https://github.com/MetaCubeX/mihomo/releases/download/"
    f"{_CORE_VERSION}/{_CORE_ASSET}"
)
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
    return shutil.which("mihomo") or shutil.which("clash-meta")


def core_installed() -> bool:
    return find_mihomo_exe() is not None


def core_version() -> str:
    """跑 `mihomo -v` 取版本字符串(如 'v1.19.25');取不到返 ''。"""
    exe = find_mihomo_exe()
    if not exe:
        return ""
    try:
        r = subprocess.run(
            [exe, "-v"],
            capture_output=True,
            text=True,
            timeout=8,
            errors="replace",
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r"v?\d+\.\d+\.\d+", out)
    return m.group(0) if m else (out[:40] if out else "")


def bundled_core_version() -> str:
    return _CORE_VERSION


def install_core(force: bool = False) -> dict[str, Any]:
    """按需下 mihomo 内核 + SHA256 + 实跑 -v 自检。

    返回 {ok: bool, version?: str, path?: str, error?: str, already?: bool}
    """
    if core_installed() and not force:
        return {
            "ok": True,
            "already": True,
            "path": find_mihomo_exe(),
            "version": core_version(),
        }

    try:
        MIHOMO_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法创建 {MIHOMO_DIR}:{e}"}

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
            last_err = f"HTTP {r.status_code} ({src.split('/')[2]})"
        except httpx.HTTPError as e:
            last_err = f"{type(e).__name__} ({src.split('/')[2]})"
            continue
    if not data:
        return {"ok": False, "error": f"下载失败:{last_err}"}

    got = hashlib.sha256(data).hexdigest().lower()
    if got != _CORE_SHA256:
        return {
            "ok": False,
            "error": "下载文件 SHA256 不符,已丢弃(镜像被污染?)",
        }

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"ok": False, "error": "下载内容不是有效 zip"}
    exe_member = next(
        (n for n in zf.namelist() if n.lower().endswith(".exe")), None
    )
    if not exe_member:
        return {"ok": False, "error": "zip 内未找到 .exe"}

    target = MIHOMO_DIR / "mihomo.exe"
    try:
        with zf.open(exe_member) as fsrc, open(target, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
    except OSError as e:
        return {"ok": False, "error": f"写入失败:{e}"}

    try:
        r = subprocess.run(
            [str(target), "-v"],
            capture_output=True,
            text=True,
            timeout=10,
            errors="replace",
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        ver_text = ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        try:
            target.unlink()
        except OSError:
            pass
        return {"ok": False, "error": f"内核 -v 自检失败:{e}"}

    if "mihomo" not in ver_text.lower() and "meta" not in ver_text.lower():
        try:
            target.unlink()
        except OSError:
            pass
        return {"ok": False, "error": "自检输出不像 mihomo,已删除"}

    return {"ok": True, "path": str(target), "version": ver_text[:160]}


# --------------------------------------------------------- runtime process


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
    raise RuntimeError("没有空闲端口(7900-7999)")


def _build_config(http_port: int, node_dict: dict) -> str:
    """构造 mihomo 单节点 yaml。安全关键:external-controller 关闭。"""
    import yaml

    name = node_dict.get("name", "node")
    cfg = {
        "mixed-port": http_port,
        "bind-address": "127.0.0.1",  # 绑死本机
        "allow-lan": False,           # 拒局域网
        "mode": "rule",
        "log-level": "silent",
        "external-controller": "",    # 关掉 mihomo 自己的 API,避免外露
        "proxies": [node_dict],
        "rules": [f"MATCH,{name}"],
    }
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)


def _wait_port_alive(port: int, timeout_s: float = 3.0) -> bool:
    """轮询直到 mihomo 监听端口起来。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.1)
    return False


def ensure_proxy(sub_id: str, node_dict: dict) -> tuple[str | None, str]:
    """按 (sub_id, node) 启 mihomo,返回 (proxy_url, error_or_empty)。

    幂等:同一 (sub_id, node) 重复调,复用已起的实例。
    """
    exe = find_mihomo_exe()
    if not exe:
        return None, CORE_MISSING

    node_name = node_dict.get("name", "")
    if not node_name:
        return None, "节点缺 name"

    key = f"{sub_id}::{node_name}"

    with _LOCK:
        inst = _INSTANCES.get(key)
        if inst and inst["proc"].poll() is None:
            return inst["url"], ""

        # 清理同 key 的死进程
        if inst:
            _INSTANCES.pop(key, None)

        try:
            port = _free_port()
        except RuntimeError as e:
            return None, str(e)

        work = _work_base() / re.sub(r"[^A-Za-z0-9._-]+", "_", key)
        work.mkdir(parents=True, exist_ok=True)
        cfg_path = work / "config.yaml"
        try:
            cfg_path.write_text(_build_config(port, node_dict), encoding="utf-8")
        except OSError as e:
            return None, f"写配置失败:{e}"

        try:
            proc = subprocess.Popen(
                [exe, "-d", str(work), "-f", str(cfg_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(0x08000000 if os.name == "nt" else 0),
            )
        except OSError as e:
            return None, f"启 mihomo 失败:{e}"

        if not _wait_port_alive(port, timeout_s=4.0):
            try:
                proc.kill()
            except OSError:
                pass
            return None, f"mihomo 起来后 4s 内 {port} 端口仍未监听"

        url = f"http://127.0.0.1:{port}"
        _INSTANCES[key] = {"proc": proc, "port": port, "url": url}
        return url, ""


def shutdown_one(sub_id: str, node_name: str) -> bool:
    key = f"{sub_id}::{node_name}"
    with _LOCK:
        inst = _INSTANCES.pop(key, None)
    if not inst:
        return False
    try:
        inst["proc"].kill()
    except OSError:
        pass
    return True


def shutdown_all() -> int:
    """关掉所有 mihomo 实例,返回关掉的个数。"""
    with _LOCK:
        keys = list(_INSTANCES.keys())
        for key in keys:
            inst = _INSTANCES.pop(key, None)
            if inst:
                try:
                    inst["proc"].kill()
                except OSError:
                    pass
    return len(keys)


def status() -> list[dict[str, Any]]:
    out = []
    for key, inst in list(_INSTANCES.items()):
        alive = inst["proc"].poll() is None
        out.append(
            {
                "key": key,
                "port": inst["port"],
                "url": inst["url"],
                "alive": alive,
            }
        )
    return out
