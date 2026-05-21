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
        "converted_from_uri": bool(s.get("converted_from_uri")),
    }


def list_subs() -> list[dict[str, Any]]:
    items = _load()
    # 自愈:① V2Ray URI/base64 旧订阅转 Clash YAML;② 节点列表重解析
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


# ---------- V2Ray / SS / Trojan / VLESS URI → Clash YAML 转换 ----------
# 机场默认返 base64(URI 列表),mihomo 跑不了;Clash Verge 内置转过去能用。
# 我们这里也做同样的事:解 URI 一条条变成 Clash 风格的 proxy dict,然后
# yaml.dump 出来作为 yaml_content 落盘,后续 mihomo 直接用就能跑。

def _decode_b64_loose(s: str) -> str:
    """容错 base64 解码,失败返回 ''。"""
    import base64
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


def _name_from_fragment(uri: str, fallback: str) -> str:
    from urllib.parse import unquote
    if "#" in uri:
        return unquote(uri.rsplit("#", 1)[1]).strip() or fallback
    return fallback


def _parse_ss(uri: str) -> dict | None:
    """ss://[base64(method:password)]@server:port#name
    或 ss://base64(method:password@server:port)#name(旧格式)。"""
    from urllib.parse import urlparse
    body = uri[5:]  # 去 ss://
    name_part = body.split("#", 1)[1] if "#" in body else ""
    body = body.split("#", 1)[0]
    # 旧格式:整体 base64
    if "@" not in body:
        dec = _decode_b64_loose(body)
        if "@" in dec:
            body = dec
    if "@" not in body:
        return None
    cred, addr = body.rsplit("@", 1)
    # cred 可能是 base64(method:password)
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
    from urllib.parse import unquote
    return {
        "name": unquote(name_part) or f"ss-{server}",
        "type": "ss",
        "server": server,
        "port": port,
        "cipher": method,
        "password": password,
    }


def _parse_trojan(uri: str) -> dict | None:
    """trojan://password@server:port?security=tls&sni=...#name"""
    from urllib.parse import unquote, urlparse, parse_qs
    try:
        u = urlparse(uri)
        password = unquote(u.username or "")
        server = u.hostname or ""
        port = u.port or 443
        q = parse_qs(u.query)
        name = unquote(u.fragment) if u.fragment else f"trojan-{server}"
        proxy: dict = {
            "name": name, "type": "trojan",
            "server": server, "port": port, "password": password,
            "udp": True,
        }
        sni = q.get("sni", [None])[0] or q.get("peer", [None])[0]
        if sni:
            proxy["sni"] = sni
        skip = q.get("allowInsecure", q.get("insecure", ["0"]))[0]
        if skip in ("1", "true"):
            proxy["skip-cert-verify"] = True
        return proxy if server and password else None
    except Exception:  # noqa: BLE001
        return None


def _parse_vmess(uri: str) -> dict | None:
    """vmess://base64(json)"""
    import json as _json
    body = uri[8:]  # 去 vmess://
    dec = _decode_b64_loose(body)
    try:
        obj = _json.loads(dec)
    except Exception:  # noqa: BLE001
        return None
    name = (obj.get("ps") or obj.get("remarks") or obj.get("name")
             or f"vmess-{obj.get('add', '')}")
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
    tls = obj.get("tls") or ""
    if tls == "tls":
        proxy["tls"] = True
        sni = obj.get("sni") or obj.get("host")
        if sni:
            proxy["servername"] = sni
    # WS / gRPC 选项
    if net == "ws":
        ws_opts: dict = {}
        path = obj.get("path")
        if path:
            ws_opts["path"] = path
        host = obj.get("host")
        if host:
            ws_opts["headers"] = {"Host": host}
        if ws_opts:
            proxy["ws-opts"] = ws_opts
    elif net == "grpc":
        gn = obj.get("path") or obj.get("serviceName")
        if gn:
            proxy["grpc-opts"] = {"grpc-service-name": gn}
    return proxy if proxy["server"] and proxy["uuid"] else None


def _parse_vless(uri: str) -> dict | None:
    """vless://uuid@server:port?security=tls&sni=...&type=ws&path=...#name"""
    from urllib.parse import unquote, urlparse, parse_qs
    try:
        u = urlparse(uri)
        uuid_v = unquote(u.username or "")
        server = u.hostname or ""
        port = u.port or 443
        q = parse_qs(u.query)
        name = unquote(u.fragment) if u.fragment else f"vless-{server}"
        proxy: dict = {
            "name": name, "type": "vless",
            "server": server, "port": port, "uuid": uuid_v,
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
    """V2Ray base64 / URI 列表 → Clash YAML(可用)。失败返 ''。"""
    import yaml as _yaml

    # 先 base64 解一次
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
        # 其它协议(hysteria2/tuic/snell)mihomo 也支持但格式各异,
        # 先跳过免误转;后续再扩
        if not proxy:
            continue
        # 名字去重
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
    # 生成 Clash YAML
    config = {
        "mixed-port": 7890,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
        "proxy-groups": [{
            "name": "PROXY",
            "type": "select",
            "proxies": [p["name"] for p in proxies],
        }],
        "rules": ["MATCH,PROXY"],
    }
    return _yaml.dump(config, allow_unicode=True, sort_keys=False)


def maybe_convert(text: str) -> tuple[str, bool]:
    """订阅原始内容若非 Clash YAML,尝试转。返回 (最终 yaml, 是否转过)。"""
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


def detect_format(yaml_text: str) -> str:
    """诊断订阅内容格式,用于前端预览/排错。"""
    if not yaml_text:
        return "empty"
    # 1) Clash YAML:扫整个文本(proxies 行可能在很后面),
    #    或直接试 PyYAML —— 解出 dict 且含 proxies 键就算
    if re.search(r"^(proxies|Proxy)\s*:", yaml_text, re.M):
        return "clash-yaml"
    try:
        import yaml as _yaml
        d = _yaml.safe_load(yaml_text)
        if isinstance(d, dict) and ("proxies" in d or "Proxy" in d):
            return "clash-yaml"
    except Exception:  # noqa: BLE001
        pass
    # 2) V2Ray URI 裸文本
    if re.search(
        r"^(vmess|vless|ss|ssr|trojan|hysteria2?|tuic|anytls)://",
        yaml_text, re.M | re.I,
    ):
        return "v2ray-uri"
    # 3) Surge 配置(以 #!MANAGED-CONFIG 开头)
    if yaml_text.lstrip().startswith("#!MANAGED-CONFIG"):
        return "surge"
    # 4) 看着像 base64:只含 base64 字符 + 长度合理
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
    """拉取订阅 URL,返回 (yaml_text, sub_info_dict)。
    机场会按 UA gating:旧的 ClashforWindows 给阉割版/空配置,
    给 clash.meta / mihomo / clash-verge 才给完整新协议(anytls/hysteria2…)。
    所以按 UA 优先级链拉,选 proxies 数最多的那次返回。
    同时若发现 Clash YAML 但 proxies 为空且 URL 带了 flag=clash,
    自动剥 flag=clash 再试拿 base64 URI(走转换器)。"""
    # 顺序很关键:先试支持新协议的 UA(给完整内容),再回退旧 UA
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
            with httpx.Client(timeout=20.0, follow_redirects=True,
                              headers={"User-Agent": ua}) as c:
                r = c.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            last_err = e
            continue
        text = r.text
        info = _parse_sub_info(r.headers.get("subscription-userinfo", ""))
        cnt = _proxy_count(text)
        # base64 URI 也算"有内容"(后面 maybe_convert 会处理)
        if cnt == 0 and detect_format(text) in ("base64", "v2ray-uri"):
            cnt = max(1, text.count("://"))
        if cnt > best_count:
            best_count = cnt
            best_text = text
            best_info = info or best_info
            if cnt > 0 and ua == ua_chain[0]:
                # 第一个 UA 就拿到节点,不必再试
                break
    if not best_text:
        if last_err:
            raise last_err
        raise httpx.HTTPError("订阅 URL 所有 UA 都失败")

    # 兜底:Clash YAML 但 proxies 为空且 URL 带了 flag=clash → 剥掉重拉
    if (best_count == 0 and detect_format(best_text) == "clash-yaml"
            and re.search(r"[?&]flag=clash\b", url, re.I)):
        url2 = re.sub(r"[?&]flag=clash\b", "", url, flags=re.I)
        url2 = re.sub(r"\?&", "?", url2).rstrip("?&")
        try:
            with httpx.Client(
                timeout=20.0, follow_redirects=True,
                headers={"User-Agent": "mihomo/1.18.7"},
            ) as c2:
                r2 = c2.get(url2)
            r2.raise_for_status()
            if r2.text and r2.text.strip():
                best_text = r2.text
                info2 = _parse_sub_info(
                    r2.headers.get("subscription-userinfo", ""))
                if info2:
                    best_info = info2
        except httpx.HTTPError:
            pass
    return best_text, best_info


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
    # 机场常返 base64(V2Ray URI),mihomo 跑不了 → 先转 Clash YAML
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
        yaml_text, converted = maybe_convert(yaml_text)
        s["yaml_content"] = yaml_text
        s["updated"] = int(time.time())
        s["converted_from_uri"] = converted
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


# ---------- 节点延迟(TCP 直连,不走 mihomo) ----------
# 走 mihomo 测延迟意味着每测一个节点就要 spawn 一个子进程,32 节点 32 个进程,
# 太重;UI 也会因为 spawn 阻塞而"无加载感"。这里改用纯 TCP connect 到节点
# server:port,毫秒级、并发友好,而且 mihomo 没装也能测。
# 缺点:测的是"能连上节点的入口端口",不等于代理真能跑;但作为"节点是否还活
# 着 + 哪个 latency 最低"的指标足够,真要端到端验证再走 mihomo。

def _node_endpoint(node_dict: dict[str, Any]) -> tuple[str, int] | None:
    """从节点 dict 提 (server, port)。"""
    server = str(node_dict.get("server") or "").strip()
    try:
        port = int(node_dict.get("port") or 0)
    except (ValueError, TypeError):
        return None
    if not server or port <= 0 or port > 65535:
        return None
    return server, port


def tcp_latency(server: str, port: int, timeout: float = 4.0) -> int | None:
    """到 server:port 连一发 TCP,返回毫秒;失败返 None。"""
    import socket
    import time as _t
    t0 = _t.perf_counter()
    try:
        with socket.create_connection((server, port), timeout=timeout):
            return int((_t.perf_counter() - t0) * 1000)
    except (OSError, socket.gaierror, socket.timeout):
        return None


def test_sub_node(sid: str, node_name: str,
                  timeout: float = 4.0) -> dict[str, Any]:
    """测单个节点 TCP 延迟。{ok, ms?, error?, server?, port?}"""
    s = get_sub_internal(sid)
    if not s:
        return {"ok": False, "error": "订阅不存在"}
    # 在 yaml 里找 dict
    try:
        import yaml as _yaml
        data = _yaml.safe_load(s.get("yaml_content", "") or "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"YAML 解析失败: {e}"[:200]}
    if not isinstance(data, dict):
        return {"ok": False, "error": "订阅 YAML 不是 dict"}
    proxies = data.get("proxies") or data.get("Proxy") or []
    target = None
    for p in proxies:
        if isinstance(p, dict) and str(p.get("name", "")).strip() == node_name:
            target = p
            break
    if not target:
        return {"ok": False, "error": f"未找到节点 '{node_name}'"}
    ep = _node_endpoint(target)
    if not ep:
        return {"ok": False, "error": "节点没 server/port"}
    server, port = ep
    ms = tcp_latency(server, port, timeout)
    if ms is None:
        return {"ok": False, "server": server, "port": port,
                "error": f"TCP 连不上 {server}:{port}"}
    return {"ok": True, "ms": ms, "server": server, "port": port,
            "node": node_name}


def test_sub_all(sid: str, timeout: float = 4.0,
                  max_concurrency: int = 16) -> list[dict[str, Any]]:
    """并发测全部节点,返回 [{node, ok, ms?, server?, port?, error?}, ...]。"""
    import concurrent.futures as _cf
    s = get_sub_internal(sid)
    if not s:
        raise ValueError("订阅不存在")
    try:
        import yaml as _yaml
        data = _yaml.safe_load(s.get("yaml_content", "") or "")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"YAML 解析失败: {e}") from e
    proxies = (data or {}).get("proxies") or (data or {}).get("Proxy") or []
    targets: list[tuple[str, tuple[str, int]]] = []
    for p in proxies:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        ep = _node_endpoint(p)
        if name and ep:
            targets.append((name, ep))

    def _do(item: tuple[str, tuple[str, int]]) -> dict[str, Any]:
        name, (server, port) = item
        ms = tcp_latency(server, port, timeout)
        if ms is None:
            return {"node": name, "ok": False, "server": server,
                    "port": port, "error": "TCP 失败"}
        return {"node": name, "ok": True, "ms": ms, "server": server,
                "port": port}

    results: list[dict[str, Any]] = []
    with _cf.ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        for r in pool.map(_do, targets):
            results.append(r)
    return results


def get_sub_internal(sid: str) -> dict[str, Any] | None:
    """供 P4 mihomo 集成读取完整 yaml + nodes。"""
    for s in _load():
        if s["id"] == sid:
            return s
    return None


def update_sub(sid: str, *, name: str | None = None,
               url: str | None = None,
               yaml_content: str | None = None,
               refetch: bool = False) -> dict[str, Any]:
    """编辑现有订阅。name / url / yaml_content 任意子集可改;
    URL 改了 (或 refetch=True) 就重新拉一次,并按需 URI→YAML 转换。"""
    items = _load()
    for s in items:
        if s["id"] != sid:
            continue
        if name is not None:
            s["name"] = name.strip() or s.get("name") or "未命名订阅"
        # URL 改 / yaml 改 / 强制 refetch
        new_url = url.strip() if isinstance(url, str) else None
        url_changed = new_url is not None and new_url != (s.get("url") or "")
        if isinstance(yaml_content, str) and yaml_content.strip():
            # 直接给 yaml 文本 → 切到 yaml 源
            text, converted = maybe_convert(yaml_content)
            if not _parse_yaml_nodes(text):
                # 转完仍提不出节点,基本就是无效内容
                raise ValueError("YAML 内容无法识别为有效订阅")
            s["source"] = "yaml"
            s["url"] = None
            s["yaml_content"] = text
            s["converted_from_uri"] = converted
            s["updated"] = int(time.time())
            s["nodes"] = _parse_yaml_nodes(text)
        elif new_url is not None and new_url:
            # 新 URL → 切到 url 源,立刻拉取
            try:
                text, info = _fetch_url(new_url)
            except (httpx.HTTPError, ValueError, RuntimeError) as e:
                raise ValueError(f"拉取订阅失败:{e}") from e
            text, converted = maybe_convert(text)
            s["source"] = "url"
            s["url"] = new_url
            s["yaml_content"] = text
            s["converted_from_uri"] = converted
            s["updated"] = int(time.time())
            s["nodes"] = _parse_yaml_nodes(text)
            for k in ("upload", "download", "total", "expire"):
                if k in info:
                    s[k] = info[k]
        elif refetch and s.get("source") == "url" and s.get("url"):
            try:
                text, info = _fetch_url(s["url"])
            except (httpx.HTTPError, ValueError, RuntimeError) as e:
                raise ValueError(f"刷新失败:{e}") from e
            text, converted = maybe_convert(text)
            s["yaml_content"] = text
            s["converted_from_uri"] = converted
            s["updated"] = int(time.time())
            s["nodes"] = _parse_yaml_nodes(text)
            for k in ("upload", "download", "total", "expire"):
                if k in info:
                    s[k] = info[k]
        _save(items)
        return _public(s)
    raise ValueError("订阅不存在")


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
