"""skills 加载器 —— 按名字找 SKILL.md,把内容做成 LLM 的 system prompt 增强。

作者的 skills 仓库克隆到了 D:\\ai-helper\\skills\\,里面是这种结构:
  skills/
    skills/
      engineering/
        grill-with-docs/SKILL.md
      productivity/
        grill-me/SKILL.md
      ...

支持 SKILL.md 的 frontmatter:
  ---
  name: skill-name
  description: ...
  ---

  正文 ...

加载时去掉 frontmatter,留正文。
"""

from __future__ import annotations

import re
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
    """按名找 SKILL.md,返回去 frontmatter 后的正文;找不到返 None。"""
    for s in list_available():
        if s["name"] == name:
            try:
                text = Path(s["path"]).read_text(encoding="utf-8")
                return _strip_frontmatter(text).strip()
            except OSError:
                return None
    return None


def build_skills_prompt(skill_names: list[str]) -> str:
    """把多个 skill 内容拼成一段附加 system prompt。"""
    blocks: list[str] = []
    for name in skill_names or []:
        content = load_skill_content(name)
        if content:
            blocks.append(
                f"\n\n### Skill: {name}\n\n{content}"
            )
    if not blocks:
        return ""
    return "\n\n--- 以下是用户人格激活的 skills ---\n" + "".join(blocks)
