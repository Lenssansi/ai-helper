"""订阅存储 + 多协议解析。从 v0.0.5 backend/vpn_store.py 迁。

存储:JSON 落盘 v01/data/aih-vpn-subs.json,每条:
{ id, name, source: "url"|"yaml", url, yaml_content,
  updated, expire, upload, download, total, nodes:[name,...],
  converted_from_uri }

支持的订阅源格式:
- Clash YAML(标准、机场常给)
- V2Ray URI / base64(ss/trojan/vmess/vless),自动转 Clash YAML
- 检测不出的格式存原文 + 节点列表为空(警告)

URL 拉取时按 UA 链尝试,优先 clash.meta/mihomo UA(机场对它返完整新协议)。
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx


def _subs_path() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return Path(get_astrbot_data_path()) / "aih-vpn-subs.json"


def _load() -> list[dict[str, Any]]:
    try:
        return json.loads(_subs_path().read_text(encoding="utf-8")) or []
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    p = _subs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------- format parse helpers


def _decode_b64_loose(s: str) -> str:
    compact = re.sub(r"\s+", "", s)
    if not compact:
        return ""
    pad = "=" * (-len(compact) % 4)
    for fn in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return fn(compact + pad).decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
    return ""


def _try_parse_clash_yaml(text: str) -> list[str]:
    try:
        import yaml as _yaml

        data = _yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        return []
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


def _try_parse_v2ray_uris(text: str) -> list[str]:
    raw = text.strip()
    decoded = _decode_b64_loose(raw)
    candidate = decoded if "://" in decoded else raw
    names: list[str] = []
    seen: set[str] = set()
    for line in candidate.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(
            r"^(vmess|vless|ss|ssr|trojan|hysteria2?|tuic|snell)://(.+)$", s, re.I
        )
        if not m:
            continue
        scheme = m.group(1).lower()
        body = m.group(2)
        name: str | None = None
        if scheme == "vmess":
            try:
                pad = "=" * (-len(body) % 4)
                jraw = base64.urlsafe_b64decode(body + pad).decode("utf-8", errors="ignore")
                obj = json.loads(jraw)
                cand = obj.get("ps") or obj.get("remarks") or obj.get("name")
                if isinstance(cand, str) and cand.strip():
                    name = cand.strip()
            except Exception:  # noqa: BLE001
                pass
        else:
            if "#" in s:
                name = unquote(s.rsplit("#", 1)[1]).strip()
        if not name:
            name = scheme + "://" + body[:24]
        if name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _parse_yaml_nodes(yaml_text: str) -> list[str]:
    """三层尝试:Clash YAML → V2Ray URI → 正则兜底。"""
    if not yaml_text or not yaml_text.strip():
        return []
    ns = _try_parse_clash_yaml(yaml_text)
    if ns:
        return ns
    ns = _try_parse_v2ray_uris(yaml_text)
    if ns:
        return ns
    names: list[str] = []
    in_proxies = False
    for line in yaml_text.splitlines():
        s = line.rstrip()
        if re.match(r"^proxies\s*:", s):
            in_proxies = True
            continue
        if (
            in_proxies
            and re.match(r"^[A-Za-z_][\w-]*\s*:", s)
            and not s.lstrip().startswith("-")
        ):
            in_proxies = False
        if not in_proxies:
            continue
        m = re.search(r"name\s*:\s*['\"]?([^'\"]+?)['\"]?\s*(?:,|\}|$)", s)
        if m:
            names.append(m.group(1).strip())
    return names


def detect_format(yaml_text: str) -> str:
    if not yaml_text:
        return "empty"
    if re.search(r"^(proxies|Proxy)\s*:", yaml_text, re.M):
        return "clash-yaml"
    try:
        import yaml as _yaml

        d = _yaml.safe_load(yaml_text)
        if isinstance(d, dict) and ("proxies" in d or "Proxy" in d):
            return "clash-yaml"
    except Exception:  # noqa: BLE001
        pass
    if re.search(r"^(vmess|vless|ss|ssr|trojan|hysteria2?|tuic|anytls)://", yaml_text, re.M | re.I):
        return "v2ray-uri"
    if yaml_text.lstrip().startswith("#!MANAGED-CONFIG"):
        return "surge"
    compact = re.sub(r"\s+", "", yaml_text)
    if compact and len(compact) > 32 and all(c.isalnum() or c in "+/=-_" for c in compact[:400]):
        return "base64"
    return "unknown"


# -------------------------------------------- V2Ray URI -> Clash YAML 转换


def _parse_ss(uri: str) -> dict | None:
    body = uri[5:]
    name_part = body.split("#", 1)[1] if "#" in body else ""
    body = body.split("#", 1)[0]
    if "@" not in body:
        dec = _decode_b64_loose(body)
        if "@" in dec:
            body = dec
    if "@" not in body:
        return None
    cred, addr = body.rsplit("@", 1)
    if ":" not in cred:
        dec = _decode_b64_loose(cred)
        if ":" in dec:
            cred = dec
    if ":" not in cred or ":" not in addr:
        return None
    method, password = cred.split(":", 1)
    try:
        server, port = addr.rsplit(":", 1)
        port = int(port.split("/", 1)[0].split("?", 1)[0])
    except (ValueError, IndexError):
        return None
    return {
        "name": unquote(name_part) or f"ss-{server}",
        "type": "ss",
        "server": server,
        "port": port,
        "cipher": method,
        "password": password,
    }


def _parse_trojan(uri: str) -> dict | None:
    try:
        u = urlparse(uri)
        password = unquote(u.username or "")
        server = u.hostname or ""
        port = u.port or 443
        q = parse_qs(u.query)
        name = unquote(u.fragment) if u.fragment else f"trojan-{server}"
        proxy: dict = {
            "name": name,
            "type": "trojan",
            "server": server,
            "port": port,
            "password": password,
            "udp": True,
        }
        sni = q.get("sni", [None])[0] or q.get("peer", [None])[0]
        if sni:
            proxy["sni"] = sni
        if q.get("allowInsecure", q.get("insecure", ["0"]))[0] in ("1", "true"):
            proxy["skip-cert-verify"] = True
        return proxy if server and password else None
    except Exception:  # noqa: BLE001
        return None


def _parse_vmess(uri: str) -> dict | None:
    body = uri[8:]
    dec = _decode_b64_loose(body)
    try:
        obj = json.loads(dec)
    except Exception:  # noqa: BLE001
        return None
    name = obj.get("ps") or obj.get("remarks") or obj.get("name") or f"vmess-{obj.get('add', '')}"
    try:
        port = int(obj.get("port") or 443)
    except (ValueError, TypeError):
        port = 443
    proxy = {
        "name": str(name)[:200],
        "type": "vmess",
        "server": obj.get("add") or "",
        "port": port,
        "uuid": obj.get("id") or "",
        "alterId": int(obj.get("aid") or 0),
        "cipher": obj.get("scy") or obj.get("security") or "auto",
        "udp": True,
    }
    net = obj.get("net") or "tcp"
    if net != "tcp":
        proxy["network"] = net
    if (obj.get("tls") or "") == "tls":
        proxy["tls"] = True
        sni = obj.get("sni") or obj.get("host")
        if sni:
            proxy["servername"] = sni
    if net == "ws":
        ws_opts: dict = {}
        if obj.get("path"):
            ws_opts["path"] = obj["path"]
        if obj.get("host"):
            ws_opts["headers"] = {"Host": obj["host"]}
        if ws_opts:
            proxy["ws-opts"] = ws_opts
    elif net == "grpc":
        gn = obj.get("path") or obj.get("serviceName")
        if gn:
            proxy["grpc-opts"] = {"grpc-service-name": gn}
    return proxy if proxy["server"] and proxy["uuid"] else None


def _parse_vless(uri: str) -> dict | None:
    try:
        u = urlparse(uri)
        uuid_v = unquote(u.username or "")
        server = u.hostname or ""
        port = u.port or 443
        q = parse_qs(u.query)
        name = unquote(u.fragment) if u.fragment else f"vless-{server}"
        proxy: dict = {
            "name": name,
            "type": "vless",
            "server": server,
            "port": port,
            "uuid": uuid_v,
            "udp": True,
        }
        if q.get("security", [""])[0] in ("tls", "reality"):
            proxy["tls"] = True
            sni = q.get("sni", q.get("peer", [None]))[0]
            if sni:
                proxy["servername"] = sni
        net = q.get("type", ["tcp"])[0]
        if net != "tcp":
            proxy["network"] = net
        if net == "ws":
            ws_opts: dict = {}
            p = q.get("path", [None])[0]
            if p:
                ws_opts["path"] = p
            h = q.get("host", [None])[0]
            if h:
                ws_opts["headers"] = {"Host": h}
            if ws_opts:
                proxy["ws-opts"] = ws_opts
        flow = q.get("flow", [None])[0]
        if flow:
            proxy["flow"] = flow
        return proxy if server and uuid_v else None
    except Exception:  # noqa: BLE001
        return None


def _convert_uri_list_to_clash(text: str) -> str:
    import yaml as _yaml

    raw = text.strip()
    decoded = _decode_b64_loose(raw)
    candidate = decoded if "://" in decoded else raw
    proxies: list[dict] = []
    seen_names: set[str] = set()
    for line in candidate.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        proto = s.split("://", 1)[0].lower() if "://" in s else ""
        proxy: dict | None = None
        if proto == "ss":
            proxy = _parse_ss(s)
        elif proto == "trojan":
            proxy = _parse_trojan(s)
        elif proto == "vmess":
            proxy = _parse_vmess(s)
        elif proto == "vless":
            proxy = _parse_vless(s)
        if not proxy:
            continue
        base_name = str(proxy.get("name") or "")[:200]
        nm = base_name
        n = 2
        while nm in seen_names:
            nm = f"{base_name} ({n})"
            n += 1
        proxy["name"] = nm
        seen_names.add(nm)
        proxies.append(proxy)
    if not proxies:
        return ""
    config = {
        "mixed-port": 7890,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": [p["name"] for p in proxies]}
        ],
        "rules": ["MATCH,PROXY"],
    }
    return _yaml.dump(config, allow_unicode=True, sort_keys=False)


def maybe_convert(text: str) -> tuple[str, bool]:
    if not text or not text.strip():
        return text, False
    fmt = detect_format(text)
    if fmt == "clash-yaml":
        return text, False
    if fmt in ("v2ray-uri", "base64"):
        conv = _convert_uri_list_to_clash(text)
        if conv:
            return conv, True
    return text, False


# ---------------------------------------------- URL 拉取(UA 链)


_SUB_INFO_RE = re.compile(r"(upload|download|total|expire)\s*=\s*(\d+)", re.I)


def _parse_sub_info(header: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in _SUB_INFO_RE.finditer(header or ""):
        out[m.group(1).lower()] = int(m.group(2))
    return out


def _fetch_url(url: str) -> tuple[str, dict[str, int]]:
    ua_chain = [
        "clash.meta/v1.18.0",
        "mihomo/1.18.7",
        "clash-verge/v1.7.7",
        "Clash/v1.18.0",
        "ClashforWindows/0.20.39",
    ]

    def _proxy_count(t: str) -> int:
        try:
            return len(_try_parse_clash_yaml(t)) if t else 0
        except Exception:  # noqa: BLE001
            return 0

    best_text = ""
    best_info: dict[str, int] = {}
    best_count = -1
    last_err: Exception | None = None
    for ua in ua_chain:
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": ua}) as c:
                r = c.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            last_err = e
            continue
        text = r.text
        info = _parse_sub_info(r.headers.get("subscription-userinfo", ""))
        cnt = _proxy_count(text)
        if cnt == 0 and detect_format(text) in ("base64", "v2ray-uri"):
            cnt = max(1, text.count("://"))
        if cnt > best_count:
            best_count = cnt
            best_text = text
            best_info = info or best_info
            if cnt > 0 and ua == ua_chain[0]:
                break
    if not best_text:
        if last_err:
            raise last_err
        raise httpx.HTTPError("订阅 URL 所有 UA 都失败")
    return best_text, best_info


# ---------------------------------------------- 公共 CRUD


def list_subs() -> list[dict[str, Any]]:
    items = _load()
    healed = False
    for s in items:
        raw = s.get("yaml_content") or ""
        if raw and not s.get("converted_from_uri"):
            fmt = detect_format(raw)
            if fmt in ("v2ray-uri", "base64"):
                conv = _convert_uri_list_to_clash(raw)
                if conv:
                    s["yaml_content"] = conv
                    s["converted_from_uri"] = True
                    healed = True
        if not s.get("nodes") and s.get("yaml_content"):
            ns = _parse_yaml_nodes(s["yaml_content"])
            if ns:
                s["nodes"] = ns
                healed = True
    if healed:
        _save(items)
    return items


def add_sub(name: str, url: str | None = None, yaml_content: str | None = None) -> dict[str, Any]:
    items = _load()
    sid = uuid.uuid4().hex[:12]
    src = "url" if (url and url.strip()) else "yaml"
    yaml_text = yaml_content or ""
    info: dict[str, int] = {}
    if src == "url":
        try:
            yaml_text, info = _fetch_url(url.strip())
        except (httpx.HTTPError, ValueError, RuntimeError) as e:
            raise ValueError(f"拉取订阅失败:{e}") from e
    if not yaml_text.strip():
        raise ValueError("订阅内容为空(URL 没下载到 / YAML 没填)")
    yaml_text, converted = maybe_convert(yaml_text)
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
        "converted_from_uri": converted,
    }
    items.append(rec)
    _save(items)
    return rec


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
        yaml_text, converted = maybe_convert(yaml_text)
        s["yaml_content"] = yaml_text
        s["updated"] = int(time.time())
        s["converted_from_uri"] = converted
        for k in ("upload", "download", "total", "expire"):
            if k in info:
                s[k] = info[k]
        s["nodes"] = _parse_yaml_nodes(yaml_text)
        _save(items)
        return s
    raise ValueError("订阅不存在")


def get_sub(sid: str) -> dict[str, Any] | None:
    for s in _load():
        if s["id"] == sid:
            return s
    return None


def extract_node_dict(yaml_text: str, node_name: str) -> dict | None:
    """从订阅 YAML 里按 name 取节点 dict,供 mihomo 启动用。"""
    try:
        import yaml as _yaml

        data = _yaml.safe_load(yaml_text or "")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    proxies = data.get("proxies") or data.get("Proxy") or []
    for p in proxies:
        if isinstance(p, dict) and str(p.get("name", "")).strip() == node_name:
            return p
    return None
