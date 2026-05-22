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
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import (
    PROJECT_ROOT,
    delete_provider,
    get_active_resolved,
    get_brain,
    get_confirm_level,
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
    set_confirm_level,
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

# 日志一次性初始化:5MB×2 滚动到 data/logs/ai-helper.log;接管 uvicorn
import applog
applog.setup_logging()

app = FastAPI(title="ai-helper", version=APP_VERSION)

# 退出时清掉所有被按需启动的 mihomo 子代理
import atexit
import vpn as _vpn_mod  # noqa: E402
atexit.register(_vpn_mod.shutdown_all)

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


# ===== CSRF 防护 =====
# 后端绑 127.0.0.1，任何本机浏览器里的恶意网页都能 fetch 本接口。
# CORS 只挡「读响应」，挡不住「请求被执行」——所以一个 evil.com 页面能
# 静默 POST /api/git/install 等高危接口造成 RCE。这里在中间件层把关:
# 状态变更请求(POST/PUT/PATCH/DELETE)必须来自可信来源,否则 403。
#
# 放行判定(任一成立):
#  1. 带 Electron 外壳注入的 X-AIH-Shell 令牌(app 自身请求,启动时由
#     Electron 主进程通过 onBeforeSendHeaders 注入,网页伪造不了)
#  2. Origin 与本服务同源(Origin 的 host:port == Host 头)
#  3. Origin 是开发期 Vite(5173)
#  4. 无 Origin 且非浏览器跨站(curl/Cline 等原生客户端,本就不是 CSRF 媒介)
_SHELL_TOKEN = os.environ.get("AIH_SHELL_TOKEN", "")
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_DEV_ORIGINS = {
    "http://localhost:5173", "http://127.0.0.1:5173",
}


def _csrf_ok(request: Request) -> bool:
    # 1) Electron 外壳令牌
    if _SHELL_TOKEN:
        tok = request.headers.get("X-AIH-Shell", "")
        if tok and secrets.compare_digest(tok, _SHELL_TOKEN):
            return True
    origin = request.headers.get("origin")
    if origin:
        if origin in _CSRF_DEV_ORIGINS:
            return True
        try:
            o_netloc = (urlparse(origin).netloc or "").lower()
        except ValueError:
            return False
        host = (request.headers.get("host") or "").lower()
        # 同源:Origin 的 host:port 与本服务 Host 一致(远程 LAN IP 也适用)
        if o_netloc and host and o_netloc == host:
            return True
        return False  # 有 Origin 但跨站 → 拒
    # 无 Origin:浏览器对跨源写请求一定带 Origin;没有 = 原生客户端。
    # 再用 Sec-Fetch-Site 兜底:浏览器强制设置,JS 无法伪造。
    sfs = (request.headers.get("sec-fetch-site") or "").lower()
    if sfs == "cross-site":
        return False
    return True


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/")
            and request.method.upper() not in _CSRF_SAFE_METHODS
            and not _csrf_ok(request)):
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF 校验失败:请求来源不可信。"
                     "请通过 ai-helper 本体或同源页面访问。"},
        )
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


# ---------- 聚合 API 代理 /v1/* (OpenAI 兼容) ----------

import proxy_v1  # noqa: E402


@app.get("/v1/models")
async def v1_models(request: Request) -> dict:
    proxy_v1._check_auth(request)
    return {"object": "list", "data": proxy_v1.list_models_data()}


@app.post("/v1/chat/completions")
async def v1_chat_completions(request: Request):
    return await proxy_v1.proxy_chat_completions(request)


@app.get("/api/proxy/info")
def proxy_info(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    """供设置页显示代理 base_url、key、开关状态。"""
    from config import get_proxy_enabled, get_proxy_key
    s = load_settings()
    return {
        "enabled": get_proxy_enabled(),
        "key": get_proxy_key(),
        "host": s.get("host", "127.0.0.1"),
        "port": s.get("port", 8756),
        "models_count": len(proxy_v1.list_models_data()),
    }


class ProxyToggle(BaseModel):
    enabled: bool


@app.post("/api/proxy/toggle")
def proxy_toggle(
    body: ProxyToggle,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    from config import set_proxy_enabled
    return {"enabled": set_proxy_enabled(body.enabled)}


@app.post("/api/proxy/regen-key")
def proxy_regen(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    from config import regenerate_proxy_key
    return {"key": regenerate_proxy_key()}


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
    pinned: bool = False  # provider 内置顶,排前面 + 自动路由仅用置顶
    description: str = ""  # 该预设的用途说明(用户输入)


class ProviderPatch(BaseModel):
    id: str | None = None  # 有=改，无=新建
    name: str | None = None
    format: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # 留空=不改，保留原 key
    capability: str | None = None  # 擅长描述，供本地模型路由
    presets: list[PresetModel] | None = None
    # VPN 字段 —— 之前漏在 schema 里,Pydantic 默认丢弃未声明字段,
    # 导致前端发的 use_vpn / vpn_sub_id 永远不落库,编辑→保存→再开就全空
    use_vpn: bool | None = None
    vpn_sub_id: str | None = None
    vpn_node: str | None = None
    vpn_nodes: list[str] | None = None
    vpn_node_latency: dict[str, int | None] | None = None


class ActivePatch(BaseModel):
    provider_id: str
    preset_label: str


class ProviderTestReq(BaseModel):
    provider_id: str
    preset_label: str = ""


class ProviderDiscoverReq(BaseModel):
    base_url: str
    api_key: str | None = None  # Gemini/OpenAI 这类需要 Bearer 才返模型列表
    provider_id: str | None = None  # 给则走该 provider 的 VPN(若开启)
    # 草稿态用 —— 直传 sub_id + node,不依赖 provider 已保存
    vpn_sub_id: str | None = None
    vpn_node: str | None = None


class ThemePatch(BaseModel):
    theme: str


class ConfirmLevelPatch(BaseModel):
    confirm_level: str


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
    confirm: bool = False  # 自检发现疑似脏文件后，用户确认强制上传
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
    web: bool = True  # 是否允许 Agent 调 web_search 工具


class AgentRespond(BaseModel):
    run_id: str
    approve: bool
    edited_args: dict | None = None


class AgentContinue(BaseModel):
    run_id: str
    task: str
    web: bool = True


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


class PresetPinPatch(BaseModel):
    label: str
    pinned: bool


@app.post("/api/providers/{pid}/pin")
def toggle_preset_pin(
    pid: str,
    body: PresetPinPatch,
    caller: Caller = Depends(require_permission("api_manage")),  # noqa: ARG001
) -> dict:
    """翻转某 provider 某 preset 的 pinned 标志。供下拉里的 ★ 用。"""
    from config import _find, load_settings, mask_provider, save_settings
    s = load_settings()
    prov = _find(s["providers"], pid)
    if not prov:
        raise HTTPException(status_code=404, detail="provider 不存在")
    for pr in prov.get("presets", []):
        if pr.get("label") == body.label:
            pr["pinned"] = bool(body.pinned)
            save_settings(s)
            return mask_provider(prov)
    raise HTTPException(status_code=404, detail=f"preset '{body.label}' 不存在")


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


@app.post("/api/providers/discover")
def discover_provider_models(
    req: ProviderDiscoverReq,
    caller: Caller = Depends(get_caller),
) -> dict:
    """给前端「自动发现模型」按钮:探测 base_url 的可用模型列表。
    走 VPN 的优先级:
      ① req.vpn_sub_id + req.vpn_node(草稿态直传,不需 provider 已保存)
      ② provider_id 已保存且开了 VPN → 用其 vpn_sub_id + 活跃/候选节点
    其余情况直连。
    仅本机:本端点会对任意 base_url 发起请求(SSRF 面),远程不开放。"""
    _local_only(caller)
    from config import _find, discover_models, load_settings

    proxy: str | None = None
    sub_id: str | None = None
    node: str | None = None
    if req.vpn_sub_id:
        sub_id = req.vpn_sub_id
        node = req.vpn_node
        if not node:
            # 没指定节点 → 用订阅里最快/第一个可用节点
            import vpn_store
            try:
                results = vpn_store.test_sub_all(sub_id, timeout=3.0)
                alive = [r for r in results if r.get("ok")]
                alive.sort(key=lambda r: r.get("ms") or 99999)
                if alive:
                    node = alive[0]["node"]
            except Exception:  # noqa: BLE001
                pass
    elif req.provider_id:
        s = load_settings()
        prov = _find(s["providers"], req.provider_id)
        if prov and prov.get("use_vpn") and prov.get("vpn_sub_id") and (
            prov.get("vpn_node") or (prov.get("vpn_nodes") or [])
        ):
            sub_id = prov["vpn_sub_id"]
            node = (prov.get("vpn_node")
                    or (prov.get("vpn_nodes") or [None])[0])
    if sub_id and node:
        import vpn
        url, err = vpn.ensure_proxy(sub_id, node)
        if not url and err == vpn.CORE_MISSING:
            return {"models": [], "errors": [], "core_missing": True}
        if url:
            proxy = url
    result = discover_models(req.base_url, req.api_key, proxy)
    # 兼容:result 现在是 {models, errors};旧版只返 list
    if isinstance(result, dict):
        return {"models": result.get("models", []),
                "errors": result.get("errors", []),
                "via_vpn": bool(proxy)}
    return {"models": result, "errors": [], "via_vpn": bool(proxy)}


@app.post("/api/providers/test")
async def test_provider(
    req: ProviderTestReq,
    caller: Caller = Depends(require_permission("api_switch")),  # noqa: ARG001
) -> dict:
    """用最小请求（max_tokens=1）实测该 API+预设能否连通。
    不写真实业务、不计入对话历史；返回 ok/耗时/错误。"""
    import time

    from config import resolve_choice

    resolved = resolve_choice(req.provider_id, req.preset_label)
    if not resolved:
        return {"ok": False, "error": "未找到该 API/预设"}
    if not resolved.get("api_key"):
        return {"ok": False, "error": "未配置 API key"}
    prov = build_provider(resolved)
    t0 = time.perf_counter()
    try:
        r = await prov.tool_complete(
            [{"role": "user", "content": "ping"}], []
        )
    except ProviderError as e:
        return {"ok": False, "error": str(e)[:300],
                "model": resolved.get("model", "")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                "model": resolved.get("model", "")}
    ms = int((time.perf_counter() - t0) * 1000)
    _ = r
    return {"ok": True, "ms": ms, "model": resolved.get("model", "")}


@app.get("/api/theme")
def read_theme(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    return {"theme": get_theme()}


@app.post("/api/theme")
def write_theme(
    patch: ThemePatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"theme": set_theme(patch.theme)}


@app.get("/api/confirm_level")
def read_confirm_level(
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return {"confirm_level": get_confirm_level()}


@app.post("/api/confirm_level")
def write_confirm_level(
    patch: ConfirmLevelPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"confirm_level": set_confirm_level(patch.confirm_level)}


@app.get("/api/usage")
def read_usage(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    import usage
    return usage.get_usage()


# ---------- Git 检测 + 安装引导 ----------

class GitInstallReq(BaseModel):
    url: str
    install_dir: str = ""


class VpnSubAdd(BaseModel):
    name: str
    url: str | None = None
    yaml: str | None = None


class VpnSubPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    yaml: str | None = None
    refetch: bool = False


class VpnRule(BaseModel):
    pattern: str
    node: str
    note: str | None = ""


class VpnRulesPatch(BaseModel):
    rules: list[VpnRule]


class NodeTestReq(BaseModel):
    provider_id: str
    node: str
    target: str | None = None  # 留空=用 provider 自身 base_url


# ---------- VPN 订阅 ----------

@app.get("/api/vpn/subs")
def vpn_list(caller: Caller = Depends(get_caller)) -> list:
    # VPN 涉及代理凭证 + 本机子进程,整组功能仅本机可用
    _local_only(caller)
    import vpn_store
    return vpn_store.list_subs()


@app.post("/api/vpn/subs")
def vpn_add(
    body: VpnSubAdd,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    _local_only(caller)
    import vpn_store
    try:
        return vpn_store.add_sub(body.name, body.url, body.yaml)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/vpn/subs/{sid}")
def vpn_delete(
    sid: str,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    _local_only(caller)
    import vpn_store
    return {"ok": vpn_store.delete_sub(sid)}


@app.patch("/api/vpn/subs/{sid}")
def vpn_update(
    sid: str,
    body: VpnSubPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    """编辑订阅:改名 / 改 URL / 替换 YAML / 强制刷新。"""
    _local_only(caller)
    import vpn_store
    try:
        return vpn_store.update_sub(
            sid,
            name=body.name,
            url=body.url,
            yaml_content=body.yaml,
            refetch=body.refetch,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/vpn/subs/{sid}/refresh")
def vpn_refresh(
    sid: str,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    _local_only(caller)
    import vpn_store
    try:
        return vpn_store.refresh_sub(sid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/vpn/subs/{sid}/preview")
def vpn_preview(
    sid: str,
    caller: Caller = Depends(get_caller),
) -> dict:
    """订阅内容预览:实时再解析一次给出节点列表 + 格式诊断 + 头部样本。
    raw_head 含订阅明文(可能有节点 server/密码),严格仅本机。"""
    _local_only(caller)
    import vpn_store
    s = vpn_store.get_sub_internal(sid)
    if not s:
        raise HTTPException(status_code=404, detail="订阅不存在")
    yaml_text = s.get("yaml_content", "") or ""
    nodes = vpn_store._parse_yaml_nodes(yaml_text)
    fmt = vpn_store.detect_format(yaml_text)
    return {
        "id": sid,
        "name": s.get("name", ""),
        "format": fmt,
        "nodes": nodes,
        "raw_head": yaml_text[:2000],
        "raw_len": len(yaml_text),
    }


@app.post("/api/vpn/subs/{sid}/rules")
def vpn_set_rules(
    sid: str,
    body: VpnRulesPatch,
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    _local_only(caller)
    import vpn_store
    try:
        return vpn_store.update_rules(
            sid, [r.model_dump() for r in body.rules]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/providers/test-node")
async def test_provider_node(
    req: NodeTestReq,
    caller: Caller = Depends(get_caller),
) -> dict:
    """测 provider 候选节点的延迟,默认走轻量 TCP(不依赖 mihomo)。
    req.target 给了就走重的:启 mihomo + HEAD 该 URL,验证整条代理链。
    仅本机:涉及 VPN 节点 + 任意 target 请求。"""
    _local_only(caller)
    from config import (
        _find,
        load_settings,
        set_provider_node_latency,
    )

    s = load_settings()
    prov = _find(s["providers"], req.provider_id)
    if not prov:
        return {"ok": False, "error": "provider 不存在"}
    sub_id = prov.get("vpn_sub_id", "")
    if not sub_id:
        return {"ok": False, "error": "该 provider 未配置 VPN 订阅"}
    if not req.node.strip():
        return {"ok": False, "error": "缺少节点名"}

    # 默认:TCP 直连,不依赖 mihomo
    if not req.target:
        import vpn_store
        result = vpn_store.test_sub_node(sub_id, req.node)
        set_provider_node_latency(req.provider_id, req.node,
                                  result.get("ms") if result.get("ok") else None)
        return result

    # target 显式给了 → 走 mihomo 端到端验证
    import time
    target = req.target.strip()
    if not target.lower().startswith(("http://", "https://")):
        target = "https://" + target
    import vpn
    url, err = vpn.ensure_proxy(sub_id, req.node)
    if not url:
        if err == vpn.CORE_MISSING:
            return {"ok": False, "core_missing": True}
        return {"ok": False, "error": err}
    import httpx
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=6.0),
            proxy=url, follow_redirects=False,
        ) as c:
            r = await c.head(target)
            _ = r.status_code
    except httpx.RequestError as e:
        set_provider_node_latency(req.provider_id, req.node, None)
        return {"ok": False, "error": f"连接失败: {e}"[:200]}
    ms = int((time.perf_counter() - t0) * 1000)
    set_provider_node_latency(req.provider_id, req.node, ms)
    return {"ok": True, "ms": ms, "node": req.node}


@app.post("/api/vpn/subs/{sid}/test-all")
def vpn_test_all(
    sid: str,
    caller: Caller = Depends(get_caller),
) -> dict:
    """订阅级:并发 TCP 测全部节点,返回结果列表(不写回 provider)。"""
    _local_only(caller)
    import vpn_store
    try:
        results = vpn_store.test_sub_all(sid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"results": results, "count": len(results),
            "alive": sum(1 for r in results if r.get("ok"))}


class VpnSubNodeTest(BaseModel):
    node: str


@app.get("/api/providers/{pid}/balance")
def provider_balance(
    pid: str,
    caller: Caller = Depends(get_caller),
) -> dict:
    """查 provider 余量。已知支持:DeepSeek / OpenRouter / Moonshot /
    SiliconFlow。不支持的 host 返 {supported: False}。
    走该 provider 的 VPN(若开启)。仅本机:会用到 api_key + 启 mihomo。"""
    _local_only(caller)
    from config import _find, load_settings
    import balance as _bal
    s = load_settings()
    prov = _find(s["providers"], pid)
    if not prov:
        raise HTTPException(status_code=404, detail="provider 不存在")
    api_key = prov.get("api_key") or ""
    base_url = prov.get("base_url") or ""
    proxy: str | None = None
    if prov.get("use_vpn") and prov.get("vpn_sub_id") and (
        prov.get("vpn_node") or (prov.get("vpn_nodes") or [])
    ):
        import vpn
        node = (prov.get("vpn_node")
                or (prov.get("vpn_nodes") or [None])[0])
        url, err = vpn.ensure_proxy(prov["vpn_sub_id"], node)
        if not url and err == vpn.CORE_MISSING:
            return {"ok": False, "supported": True,
                    "core_missing": True, "provider_id": pid}
        if url:
            proxy = url
    out = _bal.query_balance(base_url, api_key, proxy)
    out["provider_id"] = pid
    out["via_vpn"] = bool(proxy)
    return out


@app.get("/api/vpn/core-status")
def vpn_core_status(caller: Caller = Depends(get_caller)) -> dict:
    """内核是否已就位。前端据此决定是否需要弹下载提示。"""
    _local_only(caller)
    import vpn
    return {"installed": vpn.core_installed()}


class CoreInstallReq(BaseModel):
    force: bool = False  # True=即使已装也重装(用于「更新内核」)


@app.post("/api/vpn/install-core")
def vpn_install_core(
    body: CoreInstallReq | None = None,
    caller: Caller = Depends(require_permission("settings")),
) -> dict:
    """按需下载 mihomo 内核到本机(官方源 + 镜像兜底 + SHA256 校验)。
    force=True 重装(更新)。仅本机:这是落地可执行文件的操作。"""
    _local_only(caller)
    import vpn
    return vpn.install_core(force=bool(body and body.force))


@app.get("/api/components")
async def components_status(
    caller: Caller = Depends(get_caller),
) -> dict:
    """统一返回 ai-helper 用到的可更新外部组件状态,供设置页「组件与
    更新」面板渲染。仅本机(涉及 mihomo/git 等本机组件)。"""
    _local_only(caller)
    import shutil
    import subprocess
    import vpn

    # mihomo 内核
    mihomo = {
        "installed": vpn.core_installed(),
        "version": vpn.core_version(),
        "bundled": vpn.bundled_core_version(),
    }
    mihomo["updatable"] = (
        mihomo["installed"]
        and mihomo["version"]
        and mihomo["bundled"] not in mihomo["version"]
    )

    # 工程 skills
    sk = skills_status(get_skills_enabled())
    skills_info = {
        "installed": bool(sk.get("cloned")),
        "count": sk.get("count", 0),
        "enabled": bool(sk.get("enabled")),
    }

    # Git(系统软件,只读状态)
    gp = shutil.which("git")
    git_info: dict = {"installed": bool(gp)}
    if gp:
        try:
            r = subprocess.run([gp, "--version"], capture_output=True,
                               text=True, timeout=5, errors="replace")
            git_info["version"] = (r.stdout or "").strip()
        except Exception:  # noqa: BLE001
            pass

    # Ollama(系统软件,只读状态)
    ollama_info: dict = {"installed": False}
    try:
        st = await ollama_status()
        ollama_info = {
            "installed": bool(st.get("reachable")),
            "models": len(st.get("models", []) or []),
        }
    except Exception:  # noqa: BLE001
        pass

    return {
        "mihomo": mihomo,
        "skills": skills_info,
        "git": git_info,
        "ollama": ollama_info,
    }


@app.post("/api/vpn/subs/{sid}/test-node")
def vpn_test_node(
    sid: str,
    body: VpnSubNodeTest,
    caller: Caller = Depends(get_caller),
) -> dict:
    """订阅级:测单个节点 TCP 延迟。不需要 provider 已保存或绑订阅,
    ApiPage 在草稿状态下也能用 draft.vpn_sub_id 触发。"""
    _local_only(caller)
    import vpn_store
    return vpn_store.test_sub_node(sid, body.node)


@app.post("/api/providers/{pid}/test-nodes-all")
def provider_test_nodes_all(
    pid: str,
    caller: Caller = Depends(get_caller),
) -> dict:
    """provider 候选节点全测(TCP),结果落到 provider.vpn_node_latency。"""
    _local_only(caller)
    from config import _find, load_settings, set_provider_node_latency
    import vpn_store
    s = load_settings()
    prov = _find(s["providers"], pid)
    if not prov:
        raise HTTPException(status_code=404, detail="provider 不存在")
    sub_id = prov.get("vpn_sub_id", "")
    if not sub_id:
        raise HTTPException(status_code=400,
                            detail="该 provider 未配置 VPN 订阅")
    cands: list[str] = prov.get("vpn_nodes") or (
        [prov.get("vpn_node")] if prov.get("vpn_node") else []
    )
    cands = [n for n in cands if n]
    if not cands:
        return {"results": [], "count": 0, "alive": 0}
    # 用 sub 的全测,但只 keep 候选节点
    try:
        all_results = vpn_store.test_sub_all(sub_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cand_set = set(cands)
    filtered = [r for r in all_results if r.get("node") in cand_set]
    # 写回 latency
    for r in filtered:
        set_provider_node_latency(pid, r["node"],
                                   r.get("ms") if r.get("ok") else None)
    return {"results": filtered, "count": len(filtered),
            "alive": sum(1 for r in filtered if r.get("ok"))}


@app.get("/api/git/status")
def git_status(caller: Caller = Depends(get_caller)) -> dict:  # noqa: ARG001
    import shutil
    import subprocess
    p = shutil.which("git")
    if not p:
        return {"installed": False}
    try:
        r = subprocess.run([p, "--version"], capture_output=True,
                            text=True, timeout=5, errors="replace")
        return {"installed": True, "path": p,
                "version": (r.stdout or "").strip()}
    except Exception as e:  # noqa: BLE001
        return {"installed": False, "error": str(e)}


@app.post("/api/git/install")
async def git_install(
    req: GitInstallReq,
    caller: Caller = Depends(require_permission("settings")),
) -> dict:
    _local_only(caller)
    url = req.url.strip()
    if not url:
        return {"ok": False, "error": "缺少下载链接"}
    # 只允许 https —— 安装包要落地执行,绝不能走可被中间人篡改的 http
    if not url.lower().startswith("https://"):
        return {"ok": False, "error": "下载链接必须是 https://(不接受 http)"}
    import shutil
    import subprocess

    tmp_dir = PROJECT_ROOT / "data" / "tmp"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法建临时目录:{e}"}
    fn = (url.rsplit("/", 1)[-1].split("?")[0]
          or "git-installer.exe")
    # 文件名只留安全字符,杜绝路径穿越(如 ..\\..\\evil.exe)
    fn = re.sub(r"[^A-Za-z0-9._-]", "_", fn) or "git-installer.exe"
    target = tmp_dir / fn
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=30.0),
            follow_redirects=True,
        ) as c:
            async with c.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return {"ok": False,
                            "error": f"下载失败 HTTP {resp.status_code}"}
                with open(target, "wb") as f:
                    async for chunk in resp.aiter_bytes(256 * 1024):
                        f.write(chunk)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"下载失败:{e}"}

    # Inno Setup 静默参数
    args = [str(target), "/VERYSILENT", "/SUPPRESSMSGBOXES",
            "/NORESTART", "/NOCANCEL", "/SP-"]
    if req.install_dir.strip():
        args.append(f"/DIR={req.install_dir.strip()}")
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                            timeout=900, errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "安装超时(>15min)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"安装失败:{e}"}

    # 安装后:既看 PATH,也补查 install_dir/cmd 与默认路径
    candidate_paths = []
    if req.install_dir.strip():
        candidate_paths.append(
            str(Path(req.install_dir) / "cmd" / "git.exe")
        )
    candidate_paths += [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    found = shutil.which("git") or next(
        (p for p in candidate_paths if Path(p).is_file()), None
    )
    return {
        "ok": bool(found),
        "path": found or "",
        "note": ("Git 已装,但当前后端进程 PATH 还没刷新——重启 ai-helper "
                  "应能识别。"
                  if (found and not shutil.which("git")) else ""),
        "installer_log_tail": (r.stdout or "")[-800:],
    }


@app.get("/api/logs")
def read_logs(
    lines: int = 200,
    caller: Caller = Depends(get_caller),  # noqa: ARG001
) -> dict:
    return {"text": applog.get_tail(lines), **applog.stats()}


@app.post("/api/logs/clear")
def clear_logs(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    return {"ok": applog.clear_log(), **applog.stats()}


@app.post("/api/usage/reset")
def clear_usage(
    caller: Caller = Depends(require_permission("settings")),  # noqa: ARG001
) -> dict:
    import usage
    return usage.reset_usage()


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
    """GitHub 上传仅本机可用（远程禁用,防远端拿走凭证乱推）。
    打包分发版下也开放——它是个普适开发者工具,任何项目都能用。"""
    _local_only(caller)


@app.get("/api/github")
def github_status(caller: Caller = Depends(get_caller)) -> dict:
    _local_only(caller)
    g = get_github()
    return {"has_token": bool(g.get("token")),
            "username": g.get("username", ""),
            "project_root": str(PROJECT_ROOT),
            "dev": True}  # 普适开发者工具,任何分发版都开放


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
                                g.get("token", ""), g.get("username", ""),
                                body.confirm)
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
        stream_start(body.task, web=body.web),
        media_type="text/event-stream",
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
        stream_continue(body.run_id, body.task, web=body.web),
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

        # 2.5) 联网:先让云端模型自决「该不该搜+搜什么」(tool-calling),
        #      思考模式回退到文本决策,均失败再兜底总搜。
        did_search = False
        results: list = []
        query = last_user
        if req.web and last_user:
            decided = False
            try:
                resolved_d = get_active_resolved()
                if resolved_d and resolved_d.get("api_key"):
                    prov_d = build_provider(resolved_d)
                    web_spec = {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": ("Search the web. Call ONLY if the "
                                            "user's question needs fresh / "
                                            "external info you don't already "
                                            "have."),
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            },
                        },
                    }
                    try:
                        d = await prov_d.tool_complete(msgs, [web_spec])
                        for tc in d.get("tool_calls", []):
                            args = tc.get("arguments") or {}
                            q = (args.get("query") if isinstance(args, dict)
                                 else "") or last_user
                            results.extend(await web_search(q))
                            query = q
                            did_search = True
                        decided = True  # 空 tool_calls = 模型判不需要搜
                    except Exception:  # noqa: BLE001
                        # 思考模式不支持 tools → 文本决策兜底
                        dec_msgs = [
                            {"role": "system",
                             "content": ("严格只输出一行:用户消息若需联网才能"
                                         "准确答 → 输出 SEARCH: <精炼关键词>;"
                                         "否则 → 输出 NO。不要其它字。")},
                            {"role": "user", "content": last_user},
                        ]
                        d2 = await prov_d.tool_complete(dec_msgs, [])
                        txt = (d2.get("content") or "").strip()
                        if txt.upper().startswith("SEARCH:"):
                            query = txt.split(":", 1)[1].strip() or last_user
                            results = await web_search(query)
                            did_search = True
                        decided = True
            except Exception:  # noqa: BLE001
                decided = False
            if not decided:
                results = await web_search(last_user)
                did_search = True
        if did_search:
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
            # 走 VPN 但内核没装 → 发惰性信号,前端弹「按需下载组件」提示
            if _vpn_mod.CORE_MISSING in str(e):
                yield _sse({"core_missing": True})
            else:
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
