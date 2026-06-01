"""工作目录授权白名单 —— 简化版,从 v0.0.5 backend/config.path_in_scope 迁。

白名单存 v01/data/aih-config.json 的 coding_roots[] 字段。
LLM 工具读写文件前必须 _safe(path) 校验落在白名单内,否则拒绝。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _config_path() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return Path(get_astrbot_data_path()) / "aih-config.json"


def _load_config() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_config(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_roots() -> list[str]:
    return [str(p) for p in (_load_config().get("coding_roots") or [])]


def add_root(path: str) -> tuple[bool, str]:
    """添加授权根目录。返回 (added, message)。"""
    try:
        p = Path(path).resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return False, f"路径解析失败:{e}"
    if not p.is_dir():
        return False, f"不是已存在的目录:{p}"
    cfg = _load_config()
    roots: list[str] = list(cfg.get("coding_roots") or [])
    if str(p) in roots:
        return False, f"已在白名单:{p}"
    roots.append(str(p))
    cfg["coding_roots"] = roots
    _save_config(cfg)
    return True, str(p)


def remove_root(path: str) -> bool:
    """从白名单删一项,返回是否真的删了。"""
    try:
        target = str(Path(path).resolve(strict=False))
    except (OSError, RuntimeError):
        return False
    cfg = _load_config()
    roots: list[str] = list(cfg.get("coding_roots") or [])
    before = len(roots)
    roots = [r for r in roots if r != target]
    if len(roots) == before:
        return False
    cfg["coding_roots"] = roots
    _save_config(cfg)
    return True


def _real(p: str | Path) -> Path:
    """双重 resolve:跟 symlink/junction 到真实路径。

    Path.resolve() + os.path.realpath() 双层,确保 Windows junction(reparse point)
    也被跟随 —— 防止"白名单内放 junction 指向白名单外目标"的越界。
    """
    p1 = Path(p).resolve(strict=False)
    p2 = Path(os.path.realpath(str(p1)))
    return p2


def in_scope(path: str) -> bool:
    """检查 path 是否落在任一白名单根目录内(包含等于和子路径)。"""
    if not path:
        return False
    try:
        pr = _real(path)
    except (OSError, RuntimeError):
        return False
    for r in get_roots():
        try:
            rp = _real(r)
            if pr == rp or pr.is_relative_to(rp):
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


class ScopeError(Exception):
    """路径越出白名单。给 LLM 返这条错误的指引信息。"""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"拒绝:路径 {path} 越出授权白名单。"
            f"用户需先在 WebChat 输 /aih-coding-allow <根目录绝对路径> 添加。"
        )
