"""ai-helper 后端入口（P0 骨架）。

唯一核心服务：同时服务本机 Electron 外壳与浏览器（本机/局域网/ZeroTier）。
P3：本地小模型「大脑」(路由/直答/摘要)。
P4：编程 Agent——/api/agent/start|respond|rollback(SSE 逐步工具循环，
高危确认暂停)、/api/workspace(授权白名单/工作目录/测试命令)，
作用域护栏 + git 检查点/回滚 + 改后测试。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import (
    PROJECT_ROOT,
    delete_provider,
    get_active_resolved,
    get_brain,
    get_ollama,
    get_system_prompt,
    get_theme,
    load_settings,
    public_state,
    get_github,
    get_skills_enabled,
    get_workspace,
    set_active,
    set_github,
    set_brain,
    set_ollama,
    set_skills_enabled,
    set_system_prompt,
    set_theme,
    set_workspace,
    upsert_provider,
)
from skills_loader import status as skills_status
import github_up
from agent_session import rollback as agent_rollback
from agent_session import stream_continue, stream_respond, stream_start
from chat_agent import stream_respond as cf_respond
from chat_agent import stream_start as cf_start
from brain import ollama_status, route, summarize
from search import as_context, web_search
from providers import get_provider as build_provider
from providers.base import ProviderError
from security import Caller, get_caller, require_permission
from store import (
    delete_agent,
    delete_conv,
    get_agent,
    get_conv,
    list_agent,
    list_convs,
    new_id,
    upsert_conv,
)

APP_VERSION = "0.2.0-p2"

app = FastAPI(title="ai-helper", version=APP_VERSION)

# 开发期前端跑在 Vite(5173)，与后端(8756)跨端口，需放行本地源。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/whoami")
def whoami(caller: Caller = Depends(get_caller)) -> dict[str, object]:
    """前端据此自适应隐藏/禁用远程不可用的功能（仅体验，后端才是真门禁）。"""
    return {
        "trust": caller.trust,
        "client_host": caller.client_host,
        "permissions": caller.permissions,
    }


class PresetModel(BaseModel):
    label: str
    model: str
    extra_body: dict = Field(default_factory=dict)


class ProviderPatch(BaseModel):
    id: str | None = None  # 有=改，无=新建
    name: str | None = None
    format: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # 留空=不改，保留原 key
    capability: str | None = None  # 擅长描述，供本地模型路由
    presets: list[PresetModel] | None = None


class ActivePatch(BaseModel):
    provider_id: str
    preset_label: str


class ThemePatch(BaseModel):
    theme: str


class SystemPromptPatch(BaseModel):
    system_prompt: str


class SkillsPatch(BaseModel):
    enabled: bool


class GithubSave(BaseModel):
    token: str | None = None
    username: str | None = None


class GithubPath(BaseModel):
    path: str


class GithubUpload(BaseModel):
    path: str
    repo: str
    private: bool = True
class GithubSaveDoc(BaseModel):
    path: str
    content: str


class OllamaPatch(BaseModel):
    base_url: str | None = None
    model: str | None = None


class BrainPatch(BaseModel):
    auto_route: bool | None = None
    local_answer: bool | None = None
    summary: bool | None = None
    summary_threshold: int | None = None


class WorkspacePatch(BaseModel):
    allowed_roots: list[str] | None = None
    cwd: str | None = None
    test_cmd: str | None = None


class AgentStart(BaseModel):
    task: str


class AgentRespond(BaseModel):
    run_id: str
    approve: bool
    edited_args: dict | None = None


class AgentContinue(BaseModel):
    run_id: str
    task: str


class AgentRollback(BaseModel):
    run_id: str


class ChatfsStart(BaseModel):
    messages: list[dict]
    base: str = ""
    mode: str = "file"  # file=全盘文件 / settings=改设置


class ChatfsRespond(BaseModel):
    run_id: str
    approve: bool
    edited_args: dict | None = None


class ChatRequest(BaseModel):
    messages: list[dict]  # [{role, content}, ...]，前端维护的规范历史
    web: bool = False     # 开启则先联网搜索再作答


@app.get("/api/providers")
def list_providers(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    # 脱敏（无明文 key）；列表+预设+当前选择，远程也要据此渲染下拉
    return public_state()


@app.post("/api/providers")
def write_provider(
    patch: ProviderPatch,
    caller: Caller = Depends(require_permission("api_manage")),  # noqa: ARG001
) -> dict:
    return upsert_provider(patch.model_dump(exclude_none=True))


@app.delete("/api/providers/{pid}")
def remove_provider(
    pid: str,
    caller: Caller = Depends(require_permission("api_manage")),  # noqa: ARG001
) -> dict:
    return {"ok": delete_provider(pid)}


@app.post("/api/active")
def post_active(
    patch: ActivePatch,
    # 仅切换当前 API/预设，不碰密钥 → api_switch，远程也允许
    caller: Caller = Depends(require_permission("api_switch")),  # noqa: ARG001
) -> dict:
    return set_active(patch.provider_id, patch.preset_label)


@app.get("/api/theme")
def read_theme(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return {"theme": get_theme()}


@app.post("/api/theme")
def write_theme(
    patch: ThemePatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"theme": set_theme(patch.theme)}


@app.get("/api/system_prompt")
def read_system_prompt(
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return {"system_prompt": get_system_prompt()}


@app.post("/api/system_prompt")
def write_system_prompt(
    patch: SystemPromptPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"system_prompt": set_system_prompt(patch.system_prompt)}


_FROZEN = getattr(sys, "frozen", False)


def _local_only(caller: Caller) -> None:
    if caller.trust != "local":
        raise HTTPException(status_code=403, detail="该功能仅本机可用")


def _dev_only(caller: Caller) -> None:
    """GitHub 发布是开发者工具：分发安装版(frozen)里禁用，远程也禁用。"""
    _local_only(caller)
    if _FROZEN:
        raise HTTPException(
            status_code=403,
            detail="安装分发版已禁用 GitHub 发布功能（开发者工具）",
        )


@app.get("/api/github")
def github_status(caller: Caller = Depends(get_caller)) -> dict:
    _local_only(caller)
    g = get_github()
    return {"has_token": bool(g.get("token")),
            "username": g.get("username", ""),
            "project_root": str(PROJECT_ROOT),
            "dev": not _FROZEN}  # 打包分发版=False → 前端隐藏整组


@app.post("/api/github")
def github_save(
    body: GithubSave, caller: Caller = Depends(get_caller)
) -> dict:
    _dev_only(caller)
    g = set_github(body.token, body.username)
    return {"has_token": bool(g.get("token")),
            "username": g.get("username", "")}


@app.post("/api/github/preview")
def github_preview(
    body: GithubPath, caller: Caller = Depends(get_caller)
) -> dict:
    _dev_only(caller)
    try:
        return github_up.preview(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/github/gen-doc")
def github_gen_doc(
    body: GithubPath, caller: Caller = Depends(get_caller)
) -> dict:
    _dev_only(caller)
    base = Path(body.path)
    if not base.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    ctx = []
    try:
        names = [p.name for p in sorted(base.iterdir())][:60]
        ctx.append("目录文件：" + ", ".join(names))
        for fn in ("README.md", "package.json", "requirements.txt"):
            f = base / fn
            if f.is_file():
                ctx.append(
                    f"\n--- {fn} ---\n"
                    + f.read_text(encoding="utf-8", errors="replace")[:2500]
                )
    except OSError:
        pass
    resolved = get_active_resolved()
    if not resolved or not resolved.get("api_key"):
        raise HTTPException(status_code=400,
                            detail="未配置可用 API，无法生成文档")
    prov = build_provider(resolved)
    msgs = [
        {"role": "system",
         "content": "为该项目写一份简洁中文「说明文档」(项目描述,"
                    "用作仓库介绍)：它是什么、主要功能、定位特点。"
                    "仅作项目介绍，不写安装步骤/命令/源码构建/开发细节,"
                    "不提任何 .bat。只输出 Markdown。"},
        {"role": "user", "content": "项目信息：\n" + "\n".join(ctx)},
    ]
    import asyncio
    try:
        res = asyncio.run(prov.tool_complete(msgs, []))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"生成失败：{e}")
    return {"doc": res.get("content", "")}


@app.post("/api/github/save-doc")
def github_save_doc(
    body: GithubSaveDoc, caller: Caller = Depends(get_caller)
) -> dict:
    _dev_only(caller)
    base = Path(body.path)
    if not base.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    fn = base / "说明文档.md"
    fn.write_text(body.content, encoding="utf-8")
    return {"ok": True, "file": str(fn)}


@app.post("/api/github/upload")
def github_upload(
    body: GithubUpload, caller: Caller = Depends(get_caller)
) -> dict:
    _dev_only(caller)
    g = get_github()
    try:
        return github_up.upload(body.path, body.repo, body.private,
                                g.get("token", ""), g.get("username", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/skills")
def read_skills(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return skills_status(get_skills_enabled())


@app.post("/api/skills")
def write_skills(
    patch: SkillsPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return skills_status(set_skills_enabled(patch.enabled))


@app.post("/api/skills/update")
def update_skills(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    import shutil
    import subprocess
    dst = PROJECT_ROOT / "skills"
    tmp = PROJECT_ROOT / "skills__new"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    r = subprocess.run(
        ["git", "clone", "--depth", "1",
         "https://github.com/mattpocock/skills", str(tmp)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise HTTPException(status_code=400,
                            detail=f"克隆失败：{r.stderr[:300]}")
    shutil.rmtree(tmp / ".git", ignore_errors=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    tmp.rename(dst)
    return skills_status(get_skills_enabled())


@app.get("/api/ollama/status")
async def ollama_stat(
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    s = await ollama_status()
    s["config"] = get_ollama()
    return s


@app.get("/api/brain")
def read_brain(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return {"brain": get_brain(), "ollama": get_ollama()}


@app.post("/api/brain")
def write_brain(
    brain: BrainPatch | None = None,
    ollama: OllamaPatch | None = None,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    b = set_brain(brain.model_dump(exclude_none=True)) if brain else get_brain()
    o = set_ollama(ollama.model_dump(exclude_none=True)) if ollama \
        else get_ollama()
    return {"brain": b, "ollama": o}


def _ws_git(ws: dict) -> dict:
    cwd = ws.get("cwd", "")
    return {**ws, "cwd_is_git": bool(
        cwd and os.path.isdir(os.path.join(cwd, ".git"))
    )}


@app.get("/api/workspace")
def read_workspace(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return _ws_git(get_workspace())


@app.post("/api/workspace")
def write_workspace(
    patch: WorkspacePatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return _ws_git(set_workspace(patch.model_dump(exclude_none=True)))


@app.post("/api/workspace/git-init")
def workspace_git_init(
    body: dict,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    from agent_tools import git_init, ToolError
    try:
        return git_init(body.get("path", ""))
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/agent/start")
async def agent_start(
    body: AgentStart,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> StreamingResponse:
    return StreamingResponse(
        stream_start(body.task), media_type="text/event-stream"
    )


@app.post("/api/agent/respond")
async def agent_respond(
    body: AgentRespond,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> StreamingResponse:
    return StreamingResponse(
        stream_respond(body.run_id, body.approve, body.edited_args),
        media_type="text/event-stream",
    )


@app.post("/api/agent/continue")
async def agent_continue(
    body: AgentContinue,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> StreamingResponse:
    return StreamingResponse(
        stream_continue(body.run_id, body.task),
        media_type="text/event-stream",
    )


@app.get("/api/agent/sessions")
def agent_sessions(
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> list:
    return list_agent()


@app.get("/api/agent/sessions/{sid}")
def agent_session_get(
    sid: str,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> dict:
    s = get_agent(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


@app.delete("/api/agent/sessions/{sid}")
def agent_session_del(
    sid: str,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> dict:
    return {"ok": delete_agent(sid)}


@app.post("/api/agent/rollback")
def agent_rollback_ep(
    body: AgentRollback,
    caller: Caller = Depends(require_permission("agent")),  # noqa: ARG001
) -> dict:
    return agent_rollback(body.run_id)


@app.post("/api/chatfs/start")
async def chatfs_start(
    body: ChatfsStart,
    caller: Caller = Depends(get_caller),
) -> StreamingResponse:
    if caller.trust != "local":
        raise HTTPException(status_code=403,
                            detail="文件模式仅本机可用（远程已禁用）")
    return StreamingResponse(
        cf_start(body.messages, body.base, body.mode),
        media_type="text/event-stream",
    )


@app.post("/api/chatfs/respond")
async def chatfs_respond(
    body: ChatfsRespond,
    caller: Caller = Depends(get_caller),
) -> StreamingResponse:
    if caller.trust != "local":
        raise HTTPException(status_code=403,
                            detail="文件模式仅本机可用（远程已禁用）")
    return StreamingResponse(
        cf_respond(body.run_id, body.approve, body.edited_args),
        media_type="text/event-stream",
    )


@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    caller: Caller = Depends(require_permission("chat")),  # noqa: ARG001
) -> StreamingResponse:
    sys_prompt = get_system_prompt().strip()
    brain = get_brain()
    base_msgs = list(req.messages)

    async def event_stream() -> AsyncIterator[bytes]:
        msgs = list(base_msgs)

        # 1) 长对话滚动摘要（本地模型，失败则跳过不影响对话）
        thr = int(brain.get("summary_threshold", 20))
        if brain.get("summary", True) and len(msgs) > thr:
            keep = 8
            summary = await summarize(msgs[:-keep])
            msgs = msgs[-keep:]
            if summary:
                msgs = [{"role": "system",
                         "content": f"（早前对话摘要）\n{summary}"}] + msgs

        # 2) 全局系统提示词置最前
        if sys_prompt:
            msgs = [{"role": "system", "content": sys_prompt}] + msgs

        last_user = next(
            (m.get("content", "") for m in reversed(base_msgs)
             if m.get("role") == "user"),
            "",
        )

        # 2.5) 联网（方案A）：当前日期+搜索结果+强指令，直接拼进最后一条
        #      用户消息（模型必看，根治"说无法联网/编数据/日期错"）
        if req.web and last_user:
            results = await web_search(last_user)
            wk = "一二三四五六日"[datetime.now().weekday()]
            today = datetime.now().strftime("%Y-%m-%d")
            block = [f"【系统提供的实时信息】今天是 {today}，星期{wk}"
                     "（本机时区）。"]
            if results:
                block.append(as_context(results))
            else:
                block.append("（联网检索这次没返回结果。若问题依赖实时"
                             "信息，请如实说明检索未果，不要编造。）")
            block.append(
                "要求：以上为系统已为你实时联网获取的信息，请直接据此"
                "回答；严禁声称你无法联网或没有实时数据；严禁编造/给"
                "模拟数据；涉及日期以上面给出的今天为准。\n\n用户的问题："
                + last_user
            )
            pos = next(
                (i for i in range(len(msgs) - 1, -1, -1)
                 if msgs[i].get("role") == "user"),
                None,
            )
            if pos is not None:
                msgs[pos] = {"role": "user", "content": "\n".join(block)}
            yield _sse({"web": len(results)})

        # 3) 路由：本地模型决定本地直答 / 派给哪个云端 API
        decision = await route(last_user, brain)
        if decision is None:
            resolved = get_active_resolved()
            if resolved is None:
                yield _sse({"error":
                            "未配置任何 API，请到「API 管理」页添加并设为当前。"})
                return
            route_meta = {
                "mode": "manual",
                "name": f'{resolved["provider_name"]}·'
                        f'{resolved["preset_label"]}',
            }
        elif decision["mode"] == "local":
            oc = get_ollama()
            resolved = {
                "format": "openai_compat",
                "base_url": oc["base_url"],
                "api_key": "ollama",
                "model": oc["model"],
                "extra_body": {},
            }
            route_meta = {"mode": "local", "name": f'本地 {oc["model"]}',
                          "reason": decision.get("reason", "")}
        else:
            resolved = decision["resolved"]
            route_meta = {
                "mode": "cloud",
                "name": f'{resolved["provider_name"]}·'
                        f'{resolved["preset_label"]}',
                "reason": decision.get("reason", ""),
            }

        yield _sse({"route": route_meta})
        provider = build_provider(resolved)
        try:
            async for kind, piece in provider.stream_chat(msgs):  # type: ignore[arg-type]
                yield _sse(
                    {"reasoning": piece} if kind == "reasoning"
                    else {"delta": piece}
                )
            yield _sse({"done": True})
        except ProviderError as e:
            yield _sse({"error": str(e)})
        except Exception as e:  # noqa: BLE001
            yield _sse({"error": f"未预期错误：{e}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


# ===== 对话持久化（单用户；本地数据，远程亦可读写自己的历史）=====
class ConvSave(BaseModel):
    title: str = ""
    messages: list[dict] = []
    web: bool = False
    file: bool = False


@app.get("/api/conversations")
def conversations(caller: Caller = Depends(get_caller)) -> list:  # noqa: ARG001
    return list_convs()


@app.post("/api/conversations")
def create_conversation(
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return {"id": new_id()}


@app.get("/api/conversations/{cid}")
def read_conversation(
    cid: str, caller: Caller = Depends(get_caller)  # noqa: ARG001
) -> dict:
    conv = get_conv(cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


@app.put("/api/conversations/{cid}")
def save_conversation(
    cid: str,
    body: ConvSave,
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return upsert_conv(cid, body.title, body.messages, body.web, body.file)


@app.delete("/api/conversations/{cid}")
def remove_conversation(
    cid: str, caller: Caller = Depends(get_caller)  # noqa: ARG001
) -> dict:
    return {"ok": delete_conv(cid)}


# 生产/浏览器访问：若前端已构建（app/dist），由后端直接托管。
_DIST = PROJECT_ROOT / "app" / "dist"
if _DIST.exists():
    app.mount(
        "/assets", StaticFiles(directory=_DIST / "assets"), name="assets"
    )

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        # SPA 兜底绝不吞 /api/*：未匹配的 api 路径一律 404，
        # 否则前端会拿到 index.html 而不是 JSON。
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="未知接口")
        return FileResponse(_DIST / "index.html")


def main() -> None:
    settings = load_settings()
    host = settings["host"]
    port = int(settings["port"])
    print(f"[ai-helper] backend on http://{host}:{port}  (remote_enabled="
          f"{settings['remote_enabled']})")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
