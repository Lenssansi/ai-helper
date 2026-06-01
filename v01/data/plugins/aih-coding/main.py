"""aih-coding —— LLM 编程工具集 + skills 加载器。

工具(对 LLM):
  aih_user_dirs()                    取本机主目录/桌面/下载/文档绝对路径
  aih_list_dir(path)                 列目录(校验白名单)
  aih_read_file(path)                读文件(校验白名单 + 编码兜底)
  aih_write_file(path, content)      写/覆盖文件(校验白名单)
  aih_bash(cmd, cwd?)                跑 shell(cwd 必须在白名单内)
  aih_search_text(query, path)       目录内搜文本

slash 命令(对作者):
  /aih-coding-allow <绝对路径>       授权根目录加白名单
  /aih-coding-roots                  看现有白名单
  /aih-coding-revoke <绝对路径>      撤销授权
  /aih-skill-list                    列可用 skills
  /aih-skill-show <skill-name>       预览 skill 内容

钩子:
  @on_llm_request:读当前会话 persona 的 skills 字段,把对应 SKILL.md
  内容拼到 system_prompt 末尾。grill-with-docs 就是通过这条链子激活的。

安全:
  - 所有路径工具都过 workspace.in_scope 白名单
  - aih_bash 的 cwd 必须在白名单内,且 timeout 120s 防卡死
  - 文件读 200KB 上限,防爆 LLM 上下文
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# 平铺 import(plugin 目录名含连字符)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import skills  # type: ignore[import-not-found]
import workspace  # type: ignore[import-not-found]

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

MAX_READ = 200_000
CMD_TIMEOUT = 120


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", "replace")


def _resolve_safe(path: str) -> Path:
    """校验 path 在白名单内,返回 resolve 后的 Path;否则抛 ScopeError。"""
    if not path:
        raise workspace.ScopeError("(空路径)")
    p = Path(path).resolve(strict=False)
    if not workspace.in_scope(str(p)):
        raise workspace.ScopeError(str(p))
    return p


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        roots = workspace.get_roots()
        sks = skills.list_available()
        logger.info(
            f"[aih-coding] {len(roots)} 个授权根目录,{len(sks)} 个可用 skills"
        )

    # ============ slash 命令(给作者看/管的)============

    @filter.command("aih-coding-allow")
    async def cmd_allow(self, event: AstrMessageEvent, path: str = ""):
        """授权一个根目录,LLM 工具才能在该目录下读写。"""
        if not path:
            yield event.plain_result("用法:/aih-coding-allow <绝对路径>")
            return
        ok, msg = workspace.add_root(path)
        if ok:
            yield event.plain_result(f"✅ 已加白名单:{msg}")
        else:
            yield event.plain_result(f"❌ {msg}")

    @filter.command("aih-coding-roots")
    async def cmd_roots(self, event: AstrMessageEvent):
        """列出已授权的根目录。"""
        roots = workspace.get_roots()
        if not roots:
            yield event.plain_result(
                "授权根目录为空。LLM 工具暂时无法读写任何文件。\n"
                "用 /aih-coding-allow <绝对路径> 加目录。"
            )
            return
        lines = [f"已授权 {len(roots)} 个根目录:"]
        for r in roots:
            lines.append(f"  • {r}")
        yield event.plain_result("\n".join(lines))

    @filter.command("aih-coding-revoke")
    async def cmd_revoke(self, event: AstrMessageEvent, path: str = ""):
        """撤销一个根目录的授权。"""
        if not path:
            yield event.plain_result("用法:/aih-coding-revoke <绝对路径>")
            return
        ok = workspace.remove_root(path)
        yield event.plain_result(f"{'✅ 已撤销' if ok else '❌ 不在白名单内'}: {path}")

    @filter.command("aih-skill-list")
    async def cmd_skill_list(self, event: AstrMessageEvent):
        """列出 D:\\ai-helper\\skills 下所有可用的 skills。"""
        items = skills.list_available()
        if not items:
            yield event.plain_result(
                "没找到任何 skill。检查 D:\\ai-helper\\skills 目录是否克隆了"
                " 作者的 skills 仓库。"
            )
            return
        lines = [f"可用 skills({len(items)} 个):"]
        for s in items:
            lines.append(f"  • [{s['category']:15s}] {s['name']}")
        lines.append("")
        lines.append(
            "如何启用:dashboard → 人格 → 编辑 → skills 字段填 skill 名(如 grill-with-docs)"
        )
        yield event.plain_result("\n".join(lines))

    @filter.command("aih-skill-show")
    async def cmd_skill_show(self, event: AstrMessageEvent, name: str = ""):
        """预览一个 skill 的内容(去 frontmatter)。"""
        if not name:
            yield event.plain_result("用法:/aih-skill-show <skill-name>")
            return
        content = skills.load_skill_content(name)
        if not content:
            yield event.plain_result(f"❌ 没找到 skill '{name}',用 /aih-skill-list 看可用")
            return
        # 截断防爆消息(WebChat 也能展示长文本但实在过长不友好)
        if len(content) > 4000:
            content = content[:4000] + "\n\n…(内容过长,截断,完整请去文件看)"
        yield event.plain_result(f"=== {name} ===\n\n{content}")

    # ============ LLM tools(LLM 自动调的)============

    @filter.llm_tool(name="aih_user_dirs")
    async def t_user_dirs(self, event: AstrMessageEvent) -> str:
        """取本机真实用户名/主目录/桌面/下载/文档的绝对路径。涉及这些位置时务必先调用,避免猜测 admin 之类的用户名。"""
        home = (
            os.environ.get("USERPROFILE")
            or os.path.expanduser("~")
            or os.getcwd()
        )
        od = (
            os.environ.get("OneDrive")
            or os.environ.get("OneDriveConsumer")
            or ""
        )
        user = (
            os.environ.get("USERNAME")
            or os.path.basename(home.rstrip("\\/"))
            or ""
        )

        def _pick(bases: list[str], names: list[str]) -> str:
            for b in bases:
                if not b:
                    continue
                for n in names:
                    c = os.path.join(b, n)
                    if Path(c).is_dir():
                        return c
            return os.path.join(home, names[0]) if names else ""

        bases = [od, home] if od else [home]
        return (
            f"用户名: {user}\n"
            f"主目录: {home}\n"
            f"桌面:   {_pick(bases, ['Desktop', '桌面'])}\n"
            f"下载:   {_pick([home, od], ['Downloads', '下载'])}\n"
            f"文档:   {_pick(bases, ['Documents', '文档'])}"
        )

    @filter.llm_tool(name="aih_list_dir")
    async def t_list_dir(self, event: AstrMessageEvent, path: str) -> str:
        """列目录的文件和子目录(限授权白名单内)。

        Args:
            path(string): 要列的目录绝对路径
        """
        try:
            p = _resolve_safe(path)
        except workspace.ScopeError as e:
            return f"[scope error] {e}"
        if not p.exists():
            return f"[error] 不存在:{p}"
        if not p.is_dir():
            return f"[error] 不是目录:{p}"
        entries = []
        for e in sorted(p.iterdir()):
            entries.append(("dir " if e.is_dir() else "file") + " " + e.name)
        text = f"目录 {p}:\n" + "\n".join(entries[:500])
        if len(entries) > 500:
            text += f"\n…(共 {len(entries)} 项,只显示前 500)"
        return text

    @filter.llm_tool(name="aih_read_file")
    async def t_read_file(self, event: AstrMessageEvent, path: str) -> str:
        """读文件内容(限授权白名单内)。

        Args:
            path(string): 要读的文件绝对路径
        """
        try:
            p = _resolve_safe(path)
        except workspace.ScopeError as e:
            return f"[scope error] {e}"
        if not p.is_file():
            return f"[error] 不是文件或不存在:{p}"
        try:
            data = _decode_bytes(p.read_bytes())
        except OSError as e:
            return f"[error] 读失败:{e}"
        truncated = len(data) > MAX_READ
        return data[:MAX_READ] + ("\n\n…(内容超过 200KB,已截断)" if truncated else "")

    @filter.llm_tool(name="aih_write_file")
    async def t_write_file(
        self, event: AstrMessageEvent, path: str, content: str
    ) -> str:
        """写或覆盖文件(限授权白名单内)。文件不存在会自动创建父目录。

        Args:
            path(string): 要写的文件绝对路径
            content(string): 文件完整新内容
        """
        try:
            p = _resolve_safe(path)
        except workspace.ScopeError as e:
            return f"[scope error] {e}"
        try:
            existed = p.exists()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"[error] 写失败:{e}"
        return f"{'已覆盖' if existed else '已新建'}:{p}"

    @filter.llm_tool(name="aih_bash")
    async def t_bash(
        self, event: AstrMessageEvent, command: str, cwd: str = ""
    ) -> str:
        """在指定工作目录跑 shell 命令(cwd 必须在白名单内,超时 120s)。

        Args:
            command(string): shell 命令
            cwd(string): 工作目录绝对路径,必填且必须在白名单内
        """
        if not cwd:
            return "[error] 缺 cwd 参数(必须给授权白名单内的工作目录)"
        try:
            cwd_p = _resolve_safe(cwd)
        except workspace.ScopeError as e:
            return f"[scope error] {e}"
        if not cwd_p.is_dir():
            return f"[error] cwd 不是目录:{cwd_p}"
        try:
            r = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd_p),
                capture_output=True,
                text=True,
                timeout=CMD_TIMEOUT,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return f"[error] 命令超时(>{CMD_TIMEOUT}s)"
        out = (r.stdout or "")[-8000:]
        err = (r.stderr or "")[-4000:]
        return f"exit={r.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"

    @filter.llm_tool(name="aih_search_text")
    async def t_search_text(
        self, event: AstrMessageEvent, query: str, path: str
    ) -> str:
        """在目录树内搜文本(限授权白名单)。

        Args:
            query(string): 要搜的关键词
            path(string): 起始目录绝对路径
        """
        try:
            base = _resolve_safe(path)
        except workspace.ScopeError as e:
            return f"[scope error] {e}"
        if not base.is_dir():
            return f"[error] 不是目录:{base}"
        hits: list[str] = []
        for root, _dirs, files in os.walk(base):
            if any(s in root for s in (".git", "node_modules", ".venv", "__pycache__")):
                continue
            for f in files:
                fp = Path(root) / f
                try:
                    text = _decode_bytes(fp.read_bytes())
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if query in line:
                        hits.append(f"{fp}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 200:
                            return "\n".join(hits) + "\n…(超 200 条,截断)"
        if not hits:
            return f"未找到 '{query}'(在 {base} 下)"
        return "\n".join(hits)

    # ============ 钩子:按 persona.skills 注入 system_prompt ============

    @filter.on_llm_request()
    async def hook_inject_skills(
        self,
        event: AstrMessageEvent,
        req: Any,  # ProviderRequest
    ) -> None:
        """读当前会话的 persona.skills,把对应 SKILL.md 拼到 system_prompt 末尾。

        失败安全:任何异常吞掉只 log,不打断 LLM 调用。
        """
        try:
            umo = event.unified_msg_origin
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(
                umo
            )
            if not conv_id:
                return
            conv = await self.context.conversation_manager.get_conversation(
                umo, conv_id
            )
            persona_id = getattr(conv, "persona_id", None) if conv else None
            if not persona_id:
                return
            # AstrBot persona_manager.personas 是缓存,O(N)线性扫(N 通常 < 20)
            persona = None
            for p in (self.context.persona_manager.personas or []):
                if p.persona_id == persona_id:
                    persona = p
                    break
            if not persona:
                return
            skill_names = list(getattr(persona, "skills", None) or [])
            if not skill_names:
                return
            extra = skills.build_skills_prompt(skill_names)
            if not extra:
                return
            current = getattr(req, "system_prompt", "") or ""
            req.system_prompt = current + extra
            logger.debug(
                f"[aih-coding] persona={persona_id} 注入 {len(skill_names)} 个 skills"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[aih-coding] skills hook failed: {e}")
