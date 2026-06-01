"""skills 导入桥 —— 把克隆仓里的 skill 搬进 AstrBot 原生 skills_root。

背景:AstrBot 4.25 自带完整的 Skill 系统。它从 skills_root(=data/skills/)
扫描 `<name>/SKILL.md`,在人格编辑器里列为可选项,并在对话时由原生的
`_ensure_persona_and_skills()` 把 persona 选中的 skill 内容注入 system_prompt。

但作者克隆的 skills 仓在 D:\\ai-helper\\skills\\(另一个目录,结构:
  skills/skills/<category>/<skill-name>/SKILL.md),AstrBot 看不到它们。

本模块的职责 = 把克隆仓里的某个 skill **复制进** skills_root,
使它被原生系统识别。不再自己做 prompt 注入(那是原生系统的事,重复=污染)。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

# skills 仓位置查找顺序:
#   1) 环境变量 AIH_SKILLS_DIR(bootstrap.py 在 packed 模式设置)
#   2) 源码同级 D:\ai-helper\skills\(dev 模式 + v0.0.5 共用)
import os as _os

_ENV_SKILLS = _os.environ.get("AIH_SKILLS_DIR", "").strip()
if _ENV_SKILLS:
    _SKILLS_ROOT = Path(_ENV_SKILLS)
else:
    _PROJECT_ROOT = Path(__file__).resolve().parents[4]
    _SKILLS_ROOT = _PROJECT_ROOT / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _strip_frontmatter(text: str) -> str:
    m = _FRONTMATTER_RE.match(text or "")
    if not m:
        return text
    return text[m.end() :]


def list_available() -> list[dict[str, str]]:
    """扫 D:\\ai-helper\\skills 下所有 SKILL.md,返回 [{name, path, category}, ...]。"""
    out: list[dict[str, str]] = []
    if not _SKILLS_ROOT.exists():
        return out
    for skill_md in _SKILLS_ROOT.rglob("SKILL.md"):
        try:
            rel = skill_md.relative_to(_SKILLS_ROOT)
            # 期望结构 .../skills/<category>/<skill-name>/SKILL.md
            parts = rel.parts
            name = parts[-2] if len(parts) >= 2 else "?"
            cat = parts[-3] if len(parts) >= 3 else "(uncategorized)"
            out.append({"name": name, "category": cat, "path": str(skill_md)})
        except (ValueError, OSError):
            continue
    return sorted(out, key=lambda d: (d["category"], d["name"]))


def load_skill_content(name: str) -> str | None:
    """按名找克隆仓里的 SKILL.md,返回去 frontmatter 后的正文;找不到返 None。"""
    for s in list_available():
        if s["name"] == name:
            try:
                text = Path(s["path"]).read_text(encoding="utf-8")
                return _strip_frontmatter(text).strip()
            except OSError:
                return None
    return None


def skills_root() -> Path:
    """AstrBot 原生 skills_root = data/skills/。"""
    from astrbot.core.utils.astrbot_path import get_astrbot_skills_path

    return Path(get_astrbot_skills_path())


def is_installed(name: str) -> bool:
    """该 skill 是否已在 skills_root 下(即原生系统已可见)。"""
    return (skills_root() / name / "SKILL.md").exists()


def install_to_root(name: str) -> tuple[bool, str]:
    """把克隆仓里名为 name 的 skill 整目录复制进 skills_root。

    返回 (ok, message)。已存在则覆盖(以仓里版本为准)。
    """
    src_dir: Path | None = None
    for s in list_available():
        if s["name"] == name:
            src_dir = Path(s["path"]).parent
            break
    if src_dir is None or not src_dir.is_dir():
        return False, f"克隆仓里找不到 skill「{name}」"

    dst = skills_root() / name
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src_dir, dst)
    except OSError as e:
        return False, f"复制失败:{e}"
    return True, str(dst)
