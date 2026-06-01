"""按 provider base_url 查余量。从 v0.0.5 backend/balance.py 迁,几乎原样。

支持的端点:
- DeepSeek    api.deepseek.com/user/balance
- OpenRouter  openrouter.ai/api/v1/auth/key
- Moonshot    api.moonshot.cn/v1/users/me/balance(.ai 也走同一个 API)
- SiliconFlow api.siliconflow.cn/v1/user/info

不支持的 host(Gemini/OpenAI/Anthropic/Groq 等)返 supported=False,
caller 据此区分"凭证错"和"端点没公开"。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _client(proxy: str | None) -> httpx.Client:
    return httpx.Client(timeout=12.0, proxy=proxy, follow_redirects=True)


def _q_deepseek(_base: str, key: str, proxy: str | None) -> dict[str, Any]:
    url = "https://api.deepseek.com/user/balance"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {
            "ok": False,
            "supported": True,
            "error": f"{r.status_code} {r.text[:200]}",
        }
    data = r.json()
    infos = data.get("balance_infos") or []
    if not infos:
        return {
            "ok": False,
            "supported": True,
            "error": "balance_infos 为空",
            "raw": data,
        }
    info = infos[0]
    total = float(info.get("total_balance") or 0)
    granted = float(info.get("granted_balance") or 0)
    topped = float(info.get("topped_up_balance") or 0)
    return {
        "ok": True,
        "supported": True,
        "remaining": total,
        "total": granted + topped,
        "used": (granted + topped) - total,
        "currency": info.get("currency", "USD"),
        "unit": "money",
    }


def _q_openrouter(_base: str, key: str, proxy: str | None) -> dict[str, Any]:
    url = "https://openrouter.ai/api/v1/auth/key"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {
            "ok": False,
            "supported": True,
            "error": f"{r.status_code} {r.text[:200]}",
        }
    data = (r.json() or {}).get("data") or {}
    limit = data.get("limit")
    usage = float(data.get("usage") or 0)
    out: dict[str, Any] = {
        "ok": True,
        "supported": True,
        "used": usage,
        "currency": "USD",
        "unit": "money",
    }
    if limit is not None:
        out["total"] = float(limit)
        out["remaining"] = max(0.0, float(limit) - usage)
    return out


def _q_moonshot(_base: str, key: str, proxy: str | None) -> dict[str, Any]:
    url = "https://api.moonshot.cn/v1/users/me/balance"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {
            "ok": False,
            "supported": True,
            "error": f"{r.status_code} {r.text[:200]}",
        }
    data = (r.json() or {}).get("data") or {}
    avail = float(data.get("available_balance") or 0)
    return {
        "ok": True,
        "supported": True,
        "remaining": avail,
        "currency": "CNY",
        "unit": "money",
    }


def _q_siliconflow(_base: str, key: str, proxy: str | None) -> dict[str, Any]:
    url = "https://api.siliconflow.cn/v1/user/info"
    with _client(proxy) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        return {
            "ok": False,
            "supported": True,
            "error": f"{r.status_code} {r.text[:200]}",
        }
    data = (r.json() or {}).get("data") or {}
    return {
        "ok": True,
        "supported": True,
        "remaining": float(data.get("balance") or 0),
        "total": float(data.get("totalBalance") or 0) or None,
        "currency": "CNY",
        "unit": "money",
    }


# host 关键字 → 查询函数;匹配子串包含,顺序无关
_REGISTRY: list[tuple[str, Any]] = [
    ("deepseek.com", _q_deepseek),
    ("openrouter.ai", _q_openrouter),
    ("moonshot.cn", _q_moonshot),
    ("moonshot.ai", _q_moonshot),
    ("siliconflow.cn", _q_siliconflow),
]


def query_balance(
    base_url: str, api_key: str, proxy: str | None = None
) -> dict[str, Any]:
    """统一入口。{ok, supported, remaining?, total?, used?, currency?, error?}"""
    host = _domain(base_url)
    if not host:
        return {"ok": False, "supported": False, "error": "base_url 无效"}
    if not api_key:
        return {"ok": False, "supported": False, "error": "缺 api_key"}
    for keyword, fn in _REGISTRY:
        if keyword in host:
            try:
                return fn(base_url, api_key, proxy)
            except httpx.HTTPError as e:
                return {
                    "ok": False,
                    "supported": True,
                    "error": f"网络错误: {type(e).__name__} {str(e)[:200]}",
                }
            except (ValueError, KeyError) as e:
                return {
                    "ok": False,
                    "supported": True,
                    "error": f"解析失败: {str(e)[:200]}",
                }
    return {
        "ok": False,
        "supported": False,
        "error": f"{host} 无公开余额端点(OpenAI/Anthropic/Gemini/Groq 等没给)",
    }


def supported_hosts() -> list[str]:
    """返回支持的 host 关键字,供 caller 展示。"""
    return [k for k, _ in _REGISTRY]
