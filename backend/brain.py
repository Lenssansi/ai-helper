"""本地小模型「大脑」：任务路由 / 琐碎直答 / 长对话摘要。

全部走 Ollama 的 OpenAI 兼容端点（{base}/v1/chat/completions）。
任何 Ollama 故障都安全降级：路由失败→用手动 active；摘要失败→不摘要。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from config import get_ollama, providers_for_router, resolve_choice


async def ollama_status() -> dict[str, Any]:
    cfg = get_ollama()
    base = cfg["base_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{base}/api/tags")
            if r.status_code != 200:
                return {"reachable": False, "models": [], "model": cfg["model"]}
            models = [m.get("name", "") for m in r.json().get("models", [])]
            return {"reachable": True, "models": models,
                    "model": cfg["model"]}
    except httpx.RequestError:
        return {"reachable": False, "models": [], "model": cfg["model"]}


async def _complete(messages: list[dict], max_tokens: int = 512) -> str | None:
    """非流式跑本地模型，拿完整文本。失败返回 None。"""
    cfg = get_ollama()
    base = cfg["base_url"].rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                f"{base}/chat/completions",
                headers={"Authorization": "Bearer ollama"},
                json={
                    "model": cfg["model"],
                    "messages": messages,
                    "stream": False,
                    "max_tokens": max_tokens,
                },
            )
            if r.status_code != 200:
                return None
            return r.json()["choices"][0]["message"]["content"]
    except (httpx.RequestError, KeyError, IndexError, ValueError):
        return None


_TRIVIAL = {
    "你好", "您好", "嗨", "哈喽", "hi", "hello", "hey", "在吗", "在么",
    "谢谢", "多谢", "thanks", "thank you", "thx", "早", "早安", "晚安",
    "再见", "拜拜", "bye", "ok", "好的", "收到", "测试", "test", "?", "？",
}


def _looks_trivial(text: str) -> bool:
    """超短问候/闲聊直接判本地，不依赖弱小模型的 JSON 判断（更稳更快）。"""
    t = text.strip().lower().rstrip("！!。.~ ")
    if not t:
        return True
    if t in _TRIVIAL:
        return True
    # 很短且不含明显任务意图词
    if len(t) <= 6 and not any(
        k in t for k in ("代码", "写", "改", "翻译", "为什么", "如何",
                          "怎么", "解释", "分析", "bug", "报错")
    ):
        return True
    return False


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def route(user_text: str, brain: dict[str, Any]) -> dict[str, Any] | None:
    """返回 {mode:'local'} 或 {mode:'cloud', resolved, reason}；
    None 表示交回调用方用手动 active。"""
    if not brain.get("auto_route", True):
        return None
    provs = [p for p in providers_for_router() if p["has_key"]]
    allow_local = brain.get("local_answer", True)
    if not provs and not allow_local:
        return None

    # 启发式短路：明显的问候/闲聊直接本地，不浪费一次小模型推理
    if allow_local and _looks_trivial(user_text):
        return {"mode": "local", "reason": "简单问候/闲聊，本地直答"}
    # 没有可用云端 API（无 key）→ 只能本地
    if not provs and allow_local:
        return {"mode": "local", "reason": "无可用云端 API，本地直答"}

    # 过滤掉聚合器且无置顶预设的 provider(它们没可路由的目标)
    provs = [
        p for p in provs
        if not p.get("aggregator") or p.get("presets")
    ]
    if not provs and not allow_local:
        return None
    if not provs and allow_local:
        return {"mode": "local", "reason": "无可用云端预设(聚合器请置顶后再用)"}
    lines = []
    for p in provs:
        # presets 现在可能是 [str] (旧) 或 [{"label","description"}] (新)
        preset_view: list[str] = []
        for x in p["presets"]:
            if isinstance(x, dict):
                lbl = x.get("label", "")
                desc = x.get("description") or ""
                preset_view.append(f"{lbl}({desc})" if desc else lbl)
            else:
                preset_view.append(str(x))
        tag = "[聚合器]" if p.get("aggregator") else ""
        lines.append(
            f'- id={p["id"]} 名称={p["name"]}{tag} 擅长={p["capability"] or "未填"}'
            f' 预设={preset_view}'
        )
    local_rule = (
        "规则：打招呼、闲聊、寒暄、常识小问答、简单翻译等轻量任务，"
        '一律选本地，输出 {"target":"local","reason":"简述"}。'
        '只有需要较强能力的任务（写/改代码、推理、长文、专业问答）'
        "才选下面的云端 API。"
        if allow_local
        else "不要选择本地直答，只能在下面的云端 API 中选。"
    )
    sys = (
        "你是任务路由器。严格只输出一个 JSON 对象，禁止任何解释或多余文字。"
        f"{local_rule} 选云端时输出 "
        '{"target":"<id>","preset":"<预设标签>","reason":"简述"}。'
    )
    usr = "可用 API：\n" + ("\n".join(lines) or "（无）") + \
        f"\n\n用户问题：{user_text[:800]}"
    raw = await _complete(
        [{"role": "system", "content": sys},
         {"role": "user", "content": usr}],
        max_tokens=200,
    )
    if not raw:
        return None
    obj = _extract_json(raw)
    if not obj:
        return None
    target = str(obj.get("target", "")).strip()
    reason = str(obj.get("reason", ""))[:200]
    if target == "local" and allow_local:
        return {"mode": "local", "reason": reason}
    prov = next((p for p in provs if p["id"] == target), None)
    if not prov:
        return None
    label = obj.get("preset") or (prov["presets"][0] if prov["presets"] else "")
    resolved = resolve_choice(prov["id"], label)
    if not resolved:
        return None
    return {"mode": "cloud", "resolved": resolved, "reason": reason}


async def summarize(old_messages: list[dict]) -> str:
    convo = "\n".join(
        f'{m.get("role")}: {m.get("content", "")}' for m in old_messages
    )[:6000]
    out = await _complete(
        [
            {"role": "system",
             "content": "把下面的对话压缩成简洁中文要点，保留关键事实、"
                        "结论、用户偏好与未决问题，供后续对话参考。只输出要点。"},
            {"role": "user", "content": convo},
        ],
        max_tokens=400,
    )
    return out or ""
