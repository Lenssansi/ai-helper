from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from .base import ChatMessage, ProviderError


def _normalize_base(base_url: str) -> str:
    """统一成 .../v1，兼容用户填 https://api.deepseek.com 或 .../v1。"""
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise ProviderError("未配置 base_url，请在对话页「当前 API」里填写。")
    if not b.endswith("/v1"):
        b = b + "/v1"
    return b


class OpenAICompatProvider:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.base_url = cfg.get("base_url", "")
        self.api_key = cfg.get("api_key", "")
        self.model = cfg.get("model", "")
        self.extra_body = cfg.get("extra_body", {}) or {}

    async def stream_chat(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[tuple[str, str]]:
        if not self.api_key:
            raise ProviderError("未配置 API key，请在对话页「当前 API」里填写后再发送。")
        if not self.model:
            raise ProviderError("未配置 model，请在对话页「当前 API」里填写。")

        url = _normalize_base(self.base_url) + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 预设的额外参数（如 DeepSeek thinking、reasoning_effort）合并进请求体
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra_body,
        }

        timeout = httpx.Timeout(60.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", url, headers=headers, json=payload
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise ProviderError(
                            f"上游返回 {resp.status_code}：{body[:500]}"
                        )
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        # 思考模型（DeepSeek thinking 等）把推理放 reasoning_content
                        rc = delta.get("reasoning_content")
                        if rc:
                            yield ("reasoning", rc)
                        piece = delta.get("content")
                        if piece:
                            yield ("answer", piece)
        except httpx.RequestError as e:
            raise ProviderError(f"网络错误：连不上 {self.base_url}（{e}）") from e

    async def tool_complete(
        self, messages: list[dict], tools: list[dict]
    ) -> dict:
        """非流式、带 function-calling 的一轮补全。
        返回 {content, tool_calls:[{id,name,arguments(dict)}]}。供 Agent 循环用。"""
        if not self.api_key:
            raise ProviderError("未配置 API key")
        url = _normalize_base(self.base_url) + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "tools": tools,
            **self.extra_body,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as c:
                r = await c.post(
                    url,
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.RequestError as e:
            raise ProviderError(f"网络错误：{e}") from e
        if r.status_code != 200:
            raise ProviderError(f"上游 {r.status_code}：{r.text[:500]}")
        msg = r.json()["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.get("id", ""),
                          "name": fn.get("name", ""), "arguments": args})
        return {"content": msg.get("content") or "", "tool_calls": calls}
