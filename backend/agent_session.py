"""Agent 会话状态机：逐步工具循环 + 高危确认暂停/恢复 + 持久化。

一次只跑一个工具，高危工具执行前暂停要确认；只读自动执行。
任务开始打 git 检查点，可一键回滚。会话(messages+transcript+checkpoint)
持久化到 data/agent_sessions.json，后端重启后仍可载回并继续。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from config import (
    confirm_required,
    get_active_resolved,
    get_skills_enabled,
    get_workspace,
    path_in_scope,
)
from skills_loader import build_injection
from providers.openai_compat import OpenAICompatProvider
from agent_tools import is_high_risk, run_tool, tool_specs
import userdirs
from store import get_agent, save_agent

RUNS: dict[str, dict[str, Any]] = {}


def _persist(rid: str) -> None:
    run = RUNS.get(rid)
    if not run:
        return
    save_agent(rid, {
        "title": run.get("title", "(未命名)"),
        "cwd": run.get("cwd", ""),
        "checkpoint": run.get("checkpoint"),
        "messages": run.get("messages", []),
        "transcript": run.get("transcript", []),
        "status": run.get("status", "done"),
    })


def _ensure_loaded(rid: str) -> bool:
    """内存没有就从盘载回（后端重启后仍可继续旧会话）。"""
    if rid in RUNS:
        return True
    s = get_agent(rid)
    if not s:
        return False
    RUNS[rid] = {
        "messages": s.get("messages", []),
        "transcript": s.get("transcript", []),
        "batch": [],
        "bi": 0,
        "checkpoint": s.get("checkpoint"),
        "status": s.get("status", "done"),
        "title": s.get("title", "(未命名)"),
        "cwd": s.get("cwd", ""),
    }
    return True


def _rec(run: dict, obj: dict) -> bytes:
    """记录事件到 transcript（供持久化/重载渲染）并编码为 SSE。"""
    run.setdefault("transcript", []).append(obj)
    return _sse(obj)

_SYS = (
    "你是 ai-helper 的编程 Agent。只能在当前工作目录及授权白名单内操作。"
    "用提供的工具完成用户的编程/文件任务：先用只读工具(list_dir/read_file/"
    "search_text)了解情况，再做最小且精确的改动(edit_file 优先于 write_file)。"
    "改完代码调用 run_tests 验证。一次只调用一个工具。"
    "凡是涉及第三方库/框架/API 的用法、版本差异、报错信息、或任何你不确定"
    "的最新信息，务必先调用 web_search 查官方文档/资料再动手，不要凭记忆"
    "硬写。{dirs}涉及用户目录时用上述真实路径或 user_dirs 工具，别猜。"
    "完成后用简洁中文说明你做了什么。当前工作目录：{cwd}"
)


def _provider() -> OpenAICompatProvider | None:
    r = get_active_resolved()
    if not r or not r.get("api_key"):
        return None
    return OpenAICompatProvider(r)


def start_run(task: str) -> tuple[str | None, str | None]:
    """返回 (run_id, error)。校验工作区与 API。"""
    ws = get_workspace()
    cwd = (ws.get("cwd") or "").strip()
    if not cwd:
        return None, "未设置工作目录（设置页配置授权根目录并选当前工作目录）"
    if not path_in_scope(cwd):
        return None, f"工作目录不在授权白名单内：{cwd}"
    import os
    if not os.path.isdir(os.path.join(cwd, ".git")):
        return None, f"工作目录不是 git 仓库：{cwd}（需要 git 安全网，请先 git init）"
    if _provider() is None:
        return None, "未配置可用的云端 API（Agent 需要支持工具调用的模型）"
    sys_content = _SYS.format(cwd=cwd, dirs=userdirs.prompt_hint())
    skills = build_injection(get_skills_enabled())
    if skills:
        sys_content = skills + "\n\n" + sys_content
    rid = uuid.uuid4().hex[:12]
    RUNS[rid] = {
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": task},
        ],
        "transcript": [{"type": "user", "content": task}],
        "batch": [],          # 当前待处理 tool_calls
        "bi": 0,
        "checkpoint": None,
        "status": "running",  # running | awaiting | done | error
        "title": (task.strip()[:40] or "(未命名)"),
        "cwd": cwd,
    }
    _persist(rid)
    return rid, None


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


async def _drive(rid: str) -> AsyncIterator[bytes]:
    run = RUNS[rid]
    prov = _provider()
    if prov is None:
        run["status"] = "error"
        yield _rec(run, {"type": "error", "error": "云端 API 不可用"})
        _persist(rid)
        return

    # 任务开始打 git 检查点（仅一次）
    if run["checkpoint"] is None:
        cp = run_tool("git_checkpoint", {"message": "ai-helper agent 起点"})
        if "error" in cp:
            run["status"] = "error"
            yield _rec(run, {"type": "error",
                             "error": f"无法建检查点：{cp['error']}"})
            _persist(rid)
            return
        run["checkpoint"] = cp["checkpoint"]
        yield _rec(run, {"type": "checkpoint", "commit": cp["checkpoint"]})

    while True:
        # 1) 处理当前 batch 里未完成的 tool_call
        while run["bi"] < len(run["batch"]):
            c = run["batch"][run["bi"]]
            if (confirm_required(c["name"], is_high_risk(c["name"]))
                    and not c.get("_decided")):
                run["status"] = "awaiting"
                yield _rec(run, {"type": "confirm", "tool": c["name"],
                                 "args": c["arguments"],
                                 "call_id": c["id"]})
                _persist(rid)
                return  # 等 /respond
            if c.get("_denied"):
                content = "用户拒绝执行该操作。请换一种安全的方式或询问用户。"
            else:
                yield _rec(run, {"type": "tool", "name": c["name"],
                                 "args": c["arguments"]})
                res = run_tool(c["name"], c["arguments"])
                content = json.dumps(res, ensure_ascii=False)[:8000]
                yield _rec(run, {"type": "result", "name": c["name"],
                                 "result": res})
            run["messages"].append({
                "role": "tool", "tool_call_id": c["id"],
                "content": content,
            })
            run["bi"] += 1

        # 2) batch 处理完，问模型下一步
        try:
            resp = await prov.tool_complete(run["messages"], tool_specs())
        except Exception as e:  # noqa: BLE001
            run["status"] = "error"
            yield _rec(run, {"type": "error",
                             "error": f"模型调用失败：{e}"})
            _persist(rid)
            return

        asst: dict[str, Any] = {"role": "assistant",
                                "content": resp["content"]}
        if resp["tool_calls"]:
            asst["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"],
                              "arguments": json.dumps(tc["arguments"],
                                                      ensure_ascii=False)}}
                for tc in resp["tool_calls"]
            ]
        run["messages"].append(asst)

        if not resp["tool_calls"]:
            run["status"] = "done"
            if resp["content"]:
                yield _rec(run, {"type": "answer",
                                 "content": resp["content"]})
            yield _rec(run, {"type": "done"})
            _persist(rid)
            return

        run["batch"] = resp["tool_calls"]
        run["bi"] = 0
        # 回到循环顶部处理新 batch


async def stream_start(task: str) -> AsyncIterator[bytes]:
    rid, err = start_run(task)
    if err:
        yield _sse({"type": "error", "error": err})
        return
    yield _sse({"type": "run", "run_id": rid})
    # 用户首条指令（transcript 已在 start_run 写入，这里仅推送给前端）
    yield _sse({"type": "user", "content": task})
    async for ev in _drive(rid):
        yield ev


async def stream_respond(
    rid: str, approve: bool, edited_args: dict | None
) -> AsyncIterator[bytes]:
    if not _ensure_loaded(rid):
        yield _sse({"type": "error", "error": "会话不存在或已过期"})
        return
    run = RUNS[rid]
    if run["status"] != "awaiting" or run["bi"] >= len(run["batch"]):
        yield _sse({"type": "error", "error": "当前没有待确认操作"})
        return
    c = run["batch"][run["bi"]]
    c["_decided"] = True
    if not approve:
        c["_denied"] = True
    elif edited_args:
        c["arguments"] = edited_args
    run["status"] = "running"
    async for ev in _drive(rid):
        yield ev


async def stream_continue(rid: str, task: str) -> AsyncIterator[bytes]:
    """同一 run 追加一轮指令，复用上下文与会话起点检查点（迭代式）。"""
    if not _ensure_loaded(rid):
        yield _sse({"type": "error",
                    "error": "会话不存在或已过期，请新建会话"})
        return
    run = RUNS[rid]
    if run["status"] == "awaiting":
        yield _sse({"type": "error",
                    "error": "有待确认的高危操作，请先批准/拒绝再继续"})
        return
    run["messages"].append({"role": "user", "content": task})
    yield _rec(run, {"type": "user", "content": task})
    run["batch"] = []
    run["bi"] = 0
    run["status"] = "running"
    _persist(rid)
    async for ev in _drive(rid):
        yield ev


def rollback(rid: str) -> dict:
    if not _ensure_loaded(rid):
        return {"error": "会话不存在或已过期"}
    run = RUNS[rid]
    if not run.get("checkpoint"):
        return {"error": "无可回滚的检查点"}
    return run_tool("git_rollback", {"to": run["checkpoint"]})
