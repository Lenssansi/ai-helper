"""无需 API key 的联网搜索。

主源 Bing HTML（对无 key GET 较宽容），失败兜底 DuckDuckGo lite。
只取前几条标题/摘要/链接喂给模型当上下文。任何失败都安全降级（返回 []），
绝不打断对话。
"""

from __future__ import annotations

import base64
import binascii
import html
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
_HDRS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
_TAG = re.compile(r"<[^>]+>")

_BING = re.compile(
    r'<h2[^>]*><a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a></h2>'
    r'.*?<p class="b_lineclamp[^"]*">(?P<snip>.*?)</p>',
    re.DOTALL,
)
_DDG = re.compile(
    r'class="result-link"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?class="result-snippet"[^>]*>(?P<snip>.*?)</td>',
    re.DOTALL,
)


def _clean(s: str) -> str:
    return html.unescape(_TAG.sub("", s)).strip()


def _debing(u: str) -> str:
    """Bing 重定向 https://www.bing.com/ck/a?...&u=a1<base64> -> 真实 URL。"""
    u = html.unescape(u)
    if "bing.com/ck/a" not in u:
        return u
    q = parse_qs(urlparse(u).query)
    raw = (q.get("u") or [""])[0]
    if raw.startswith("a1"):
        raw = raw[2:]
    try:
        pad = "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode(raw + pad).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return u


async def _fetch(client: httpx.AsyncClient, method: str, url: str,
                  **kw) -> str | None:
    try:
        r = await (client.get(url, **kw) if method == "GET"
                   else client.post(url, **kw))
        if r.status_code == 200:
            return r.text
    except (httpx.RequestError, httpx.HTTPError):
        pass
    return None


async def web_search(query: str, n: int = 5) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        timeout=8.0, headers=_HDRS, follow_redirects=True
    ) as c:
        # 1) Bing
        body = await _fetch(c, "GET", "https://www.bing.com/search",
                            params={"q": q})
        if body:
            for m in _BING.finditer(body):
                out.append({
                    "title": _clean(m.group("title"))[:160],
                    "snippet": _clean(m.group("snip"))[:320],
                    "url": _debing(m.group("url")),
                })
                if len(out) >= n:
                    return out
        if out:
            return out
        # 2) DuckDuckGo lite 兜底
        body = await _fetch(c, "POST", "https://lite.duckduckgo.com/lite/",
                            data={"q": q})
        if body:
            for m in _DDG.finditer(body):
                out.append({
                    "title": _clean(m.group("title"))[:160],
                    "snippet": _clean(m.group("snip"))[:320],
                    "url": html.unescape(m.group("url")),
                })
                if len(out) >= n:
                    break
    return out


def web_search_sync(query: str, n: int = 5) -> list[dict[str, Any]]:
    """同步版（供 Agent 工具循环用，避免在已运行的事件循环里 asyncio.run）。
    逻辑同 web_search：Bing 优先，DuckDuckGo lite 兜底；失败安全返回 []。"""
    q = (query or "").strip()
    if not q:
        return []
    out: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=8.0, headers=_HDRS,
                          follow_redirects=True) as c:
            try:
                r = c.get("https://www.bing.com/search", params={"q": q})
                body = r.text if r.status_code == 200 else None
            except (httpx.RequestError, httpx.HTTPError):
                body = None
            if body:
                for m in _BING.finditer(body):
                    out.append({
                        "title": _clean(m.group("title"))[:160],
                        "snippet": _clean(m.group("snip"))[:320],
                        "url": _debing(m.group("url")),
                    })
                    if len(out) >= n:
                        return out
            if out:
                return out
            try:
                r = c.post("https://lite.duckduckgo.com/lite/",
                           data={"q": q})
                body = r.text if r.status_code == 200 else None
            except (httpx.RequestError, httpx.HTTPError):
                body = None
            if body:
                for m in _DDG.finditer(body):
                    out.append({
                        "title": _clean(m.group("title"))[:160],
                        "snippet": _clean(m.group("snip"))[:320],
                        "url": html.unescape(m.group("url")),
                    })
                    if len(out) >= n:
                        break
    except Exception:  # noqa: BLE001
        return out
    return out


def as_context(results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    lines = ["以下是联网搜索结果（可能有时效性，请据此作答并指出信息时间）："]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
    return "\n".join(lines)
