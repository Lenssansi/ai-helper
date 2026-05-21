"""VPN(Clash 兼容)订阅存储 + 元数据解析。

JSON 落盘 data/vpn_subs.json,每条订阅:
{ id, name, source: "url" | "yaml", url, yaml_content,
  updated, expire, upload, download, total, nodes:[name,...] }

订阅刷新:url 模式从远端 GET,顺便解析 subscription-userinfo header(余量);
yaml 模式只重解析 nodes。仅落数据,不启 mihomo 进程(P4 才动核心)。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from config import DATA_DIR

VPN_PATH = DATA_DIR / "vpn_subs.json"


def _load() -> list[dict[str, Any]]:
    try:
        return json.loads(VPN_PATH.read_text(encoding="utf-8")) or []
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VPN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.replace(tmp, VPN_PATH)


def _public(s: dict[str, Any]) -> dict[str, Any]:
    """前端展示:不暴露完整 yaml,只暴露元信息 + 节点名列表 + 规则。"""
    return {
        "id": s["id"],
        "name": s.get("name", ""),
        "source": s.get("source", "yaml"),
        "url": s.get("url") or None,
        "updated": s.get("updated"),
        "expire": s.get("expire"),
        "upload": s.get("upload"),
        "download": s.get("download"),
        "total": s.get("total"),
        "nodes": s.get("nodes", []),
        "rules": s.get("rules", []),
    }


def list_subs() -> list[dict[str, Any]]:
    items = _load()
    # 自愈:解析器升级后,旧订阅 nodes 为空就重解析(无需用户手动刷新)
    healed = False
    for s in items:
        if not s.get("nodes") and s.get("yaml_content"):
            ns = _parse_yaml_nodes(s["yaml_content"])
            if ns:
                s["nodes"] = ns
                healed = True
    if healed:
        _save(items)
    return [_public(s) for s in items]


def _try_parse_clash_yaml(text: str) -> list[str]:
    """尝试用 PyYAML 解析 Clash YAML 提节点名。"""
    try:
        import yaml as _yaml
        data = _yaml.safe_load(text)
        if not isinstance(data, dict):
            return []
        proxies = data.get("proxies") or data.get("Proxy") or []
        names: list[str] = []
        seen: set[str] = set()
        for p in proxies:
            if isinstance(p, dict):
                n = p.get("name")
                if isinstance(n, str) and n.strip() and n not in seen:
                    names.append(n.strip())
                    seen.add(n)
        return names
    except Exception:  # noqa: BLE001
        return []


def _try_parse_v2ray_uris(text: str) -> list[str]:
    """V2Ray 订阅格式:base64 解码后,一行一个 vmess://xxx / vless://... /
    ss://... / trojan://... / ssr://... 的 URI。抽 "#" 后的节点名(或
    vmess 的 ps 字段)。
    """
    import base64
    import json as _json
    from urllib.parse import unquote

    # 1) 尝试 base64 解
    raw = text.strip()
    decoded = ""
    try:
        # 容错:补 padding,去换行
        compact = re.sub(r"\s+", "", raw)
        if compact and all(
            c.isalnum() or c in "+/=-_" for c in compact[:200]
        ):
            pad = "=" * (-len(compact) % 4)
            try:
                decoded = base64.urlsafe_b64decode(compact + pad).decode(
                    "utf-8", errors="ignore"
                )
            except Exception:  # noqa: BLE001
                decoded = base64.b64decode(compact + pad).decode(
                    "utf-8", errors="ignore"
                )
    except Exception:  # noqa: BLE001
        decoded = ""
    # 2) 没 base64 成功 → 看原文本里是否已经是 URI 列表
    candidate = decoded if "://" in decoded else raw

    names: list[str] = []
    seen: set[str] = set()
    for line in candidate.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(
            r"^(vmess|vless|ss|ssr|trojan|hysteria2?|tuic|snell)://(.+)$",
            s,
            re.I,
        )
        if not m:
            continue
        scheme = m.group(1).lower()
        body = m.group(2)
        name: str | None = None
        if scheme == "vmess":
            # vmess://base64(json)
            try:
                pad = "=" * (-len(body) % 4)
                jraw = base64.urlsafe_b64decode(body + pad).decode(
                    "utf-8", errors="ignore"
                )
                obj = _json.loads(jraw)
                cand = obj.get("ps") or obj.get("remarks") or obj.get("name")
                if isinstance(cand, str) and cand.strip():
                    name = cand.strip()
            except Exception:  # noqa: BLE001
                pass
        else:
            # 其它协议:URI 后面 # 是 fragment = 节点名(URL 编码的)
            if "#" in s:
                name = unquote(s.rsplit("#", 1)[1]).strip()
        if not name:
            # 兜底:取 URL 前 24 字节做标识
            name = scheme + "://" + body[:24]
        if name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _parse_yaml_nodes(yaml_text: str) -> list[str]:
    """提取节点名。按顺序尝试:① Clash YAML ② V2Ray base64 / URI 列表
    ③ 老正则兜底。"""
    if not yaml_text or not yaml_text.strip():
        return []
    # 1) Clash YAML(标准格式)
    ns = _try_parse_clash_yaml(yaml_text)
    if ns:
        return ns
    # 2) V2Ray/SS/Trojan URI 列表(base64 或裸文本)
    ns = _try_parse_v2ray_uris(yaml_text)
    if ns:
        return ns
    # 3) 正则兜底(YAML 里有 name: 但 PyYAML 解析失败的奇葩格式)
    names: list[str] = []
    in_proxies = False
    for line in yaml_text.splitlines():
        s = line.rstrip()
        if re.match(r"^proxies\s*:", s):
            in_proxies = True
            continue
        if (in_proxies
                and re.match(r"^[A-Za-z_][\w-]*\s*:", s)
                and not s.lstrip().startswith("-")):
            in_proxies = False
        if not in_proxies:
            continue
        m = re.search(r"name\s*:\s*['\"]?([^'\"]+?)['\"]?\s*(?:,|\}|$)", s)
        if m:
            names.append(m.group(1).strip())
    return names


def detect_format(yaml_text: str) -> str:
    """诊断订阅内容格式,用于前端预览/排错。"""
    if not yaml_text:
        return "empty"
    head = yaml_text.lstrip()[:200]
    if re.match(r"^(proxies|Proxy)\s*:", head, re.M):
        return "clash-yaml"
    if "://" in head and re.search(
        r"^(vmess|vless|ss|ssr|trojan|hysteria2?|tuic)://", head, re.M | re.I
    ):
        return "v2ray-uri"
    # 看着像 base64:只含 base64 字符 + 长度合理
    compact = re.sub(r"\s+", "", yaml_text)
    if compact and len(compact) > 32 and all(
        c.isalnum() or c in "+/=-_" for c in compact[:400]
    ):
        return "base64"
    return "unknown"


_SUB_INFO_RE = re.compile(
    r"(upload|download|total|expire)\s*=\s*(\d+)", re.I
)


def _parse_sub_info(header: str) -> dict[str, int]:
    """subscription-userinfo: upload=1; download=2; total=3; expire=4"""
    out: dict[str, int] = {}
    for m in _SUB_INFO_RE.finditer(header or ""):
        out[m.group(1).lower()] = int(m.group(2))
    return out


def _fetch_url(url: str) -> tuple[str, dict[str, int]]:
    """拉取订阅 URL,返回 (yaml_text, sub_info_dict)。"""
    headers = {"User-Agent": "ClashforWindows/0.20.39"}  # 多数订阅源识别这个 UA
    with httpx.Client(timeout=20.0, follow_redirects=True,
                       headers=headers) as c:
        r = c.get(url)
    r.raise_for_status()
    info = _parse_sub_info(r.headers.get("subscription-userinfo", ""))
    return r.text, info


def add_sub(name: str, url: str | None = None,
            yaml_content: str | None = None) -> dict[str, Any]:
    items = _load()
    sid = uuid.uuid4().hex[:12]
    src = "url" if (url and url.strip()) else "yaml"
    yaml_text = yaml_content or ""
    info: dict[str, int] = {}
    if src == "url":
        try:
            yaml_text, info = _fetch_url(url.strip())  # type: ignore[arg-type]
        except (httpx.HTTPError, ValueError, RuntimeError) as e:
            raise ValueError(f"拉取订阅失败:{e}") from e
    if not yaml_text.strip():
        raise ValueError("订阅内容为空(URL 没下载到 / YAML 没填)")
    nodes = _parse_yaml_nodes(yaml_text)
    rec = {
        "id": sid,
        "name": name or "未命名订阅",
        "source": src,
        "url": (url.strip() if src == "url" else None),
        "yaml_content": yaml_text,
        "updated": int(time.time()),
        "upload": info.get("upload"),
        "download": info.get("download"),
        "total": info.get("total"),
        "expire": info.get("expire"),
        "nodes": nodes,
    }
    items.append(rec)
    _save(items)
    return _public(rec)


def delete_sub(sid: str) -> bool:
    items = _load()
    new = [s for s in items if s["id"] != sid]
    if len(new) == len(items):
        return False
    _save(new)
    return True


def refresh_sub(sid: str) -> dict[str, Any]:
    items = _load()
    for s in items:
        if s["id"] != sid:
            continue
        if s.get("source") != "url" or not s.get("url"):
            raise ValueError("非 URL 订阅,无法刷新")
        try:
            yaml_text, info = _fetch_url(s["url"])
        except (httpx.HTTPError, ValueError, RuntimeError) as e:
            raise ValueError(f"刷新失败:{e}") from e
        s["yaml_content"] = yaml_text
        s["updated"] = int(time.time())
        if "upload" in info:
            s["upload"] = info["upload"]
        if "download" in info:
            s["download"] = info["download"]
        if "total" in info:
            s["total"] = info["total"]
        if "expire" in info:
            s["expire"] = info["expire"]
        s["nodes"] = _parse_yaml_nodes(yaml_text)
        _save(items)
        return _public(s)
    raise ValueError("订阅不存在")


def get_sub_internal(sid: str) -> dict[str, Any] | None:
    """供 P4 mihomo 集成读取完整 yaml + nodes。"""
    for s in _load():
        if s["id"] == sid:
            return s
    return None


def update_rules(sid: str,
                  rules: list[dict[str, Any]]) -> dict[str, Any]:
    """覆盖式更新订阅的 rules。每条 rule:{pattern, node[, note]}。
    runtime 暂不自动按规则切节点(按用户要求),仅持久化供未来用 + UI 展示。"""
    items = _load()
    for s in items:
        if s["id"] != sid:
            continue
        clean: list[dict[str, Any]] = []
        node_set = set(s.get("nodes") or [])
        for r in rules or []:
            if not isinstance(r, dict):
                continue
            pat = str(r.get("pattern") or "").strip()
            node = str(r.get("node") or "").strip()
            if not pat or not node:
                continue
            if node_set and node not in node_set:
                # 规则指向不存在的节点 → 跳过(避免脏数据)
                continue
            clean.append({
                "pattern": pat[:200],
                "node": node[:200],
                "note": str(r.get("note") or "")[:200],
            })
        s["rules"] = clean
        _save(items)
        return _public(s)
    raise ValueError("订阅不存在")
