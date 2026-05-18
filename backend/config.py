"""配置加载/保存 + 多 API（providers）数据模型。

settings.json 放 data/（gitignore，含远程令牌与各 API 密钥）。
P2：单一 provider 升级为 providers[] + active 指针 + 每 API 的预设(presets)。
预设 = 标签 + 模型 id + 可选额外请求参数(extra_body)，思考/非思考、
不同模型都靠预设表达，provider 层不写任何厂商特例。
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：exe 位于 <resources>/backend/ai-helper-backend.exe
    # → PROJECT_ROOT=<resources>（含 skills/）；data 写到可写的 %APPDATA%
    _EXE = Path(sys.executable).resolve()
    PROJECT_ROOT = _EXE.parent.parent
    DATA_DIR = Path(
        os.environ.get("APPDATA") or str(PROJECT_ROOT)
    ) / "ai-helper"
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"

# DeepSeek 开箱预设（已据官方文档 2026-04：思考靠 thinking 参数，模型 v4-flash/pro）
def _deepseek_presets() -> list[dict[str, Any]]:
    return [
        {"label": "V4 Flash·普通", "model": "deepseek-v4-flash",
         "extra_body": {"thinking": {"type": "disabled"}}},
        {"label": "V4 Flash·思考", "model": "deepseek-v4-flash",
         "extra_body": {"thinking": {"type": "enabled"}}},
        {"label": "V4 Pro·普通", "model": "deepseek-v4-pro",
         "extra_body": {"thinking": {"type": "disabled"}}},
        {"label": "V4 Pro·思考", "model": "deepseek-v4-pro",
         "extra_body": {"thinking": {"type": "enabled"}}},
    ]


def _default_provider() -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "name": "DeepSeek",
        "format": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "capability": "推理、代码、数学较强，长文本与中文表现不错",
        "presets": _deepseek_presets(),
    }


DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8756,
    "remote_enabled": False,
    "token": "",
    "providers": [],
    "active": {"provider_id": "", "preset_label": ""},
    "theme": "dark",  # dark | light | system
    "system_prompt": "",  # 全局系统提示词，对所有对话/所有 API 生效
    "skills_enabled": True,  # 写代码时注入工程 skills（仅编程 Agent）
    "github": {"token": "", "username": ""},  # PAT 仅本机 data/，gitignore
    "workspace": {
        # 默认含本助手根目录；用户在设置页逐条增删（仅本机可改）
        "allowed_roots": [str(PROJECT_ROOT)],
        "cwd": str(PROJECT_ROOT),
        "test_cmd": "",       # 改动后自动跑的测试命令，如 pytest / npm test
    },
    "ollama": {"base_url": "http://localhost:11434", "model": "qwen2.5:3b"},
    "brain": {
        "auto_route": True,    # 本地模型自动选 API
        "local_answer": True,  # 琐碎问题本地直答
        "summary": True,       # 长对话滚动摘要
        "summary_threshold": 20,  # 历史消息数超过则摘要更早的部分
    },
}


def _ensure_token(s: dict[str, Any]) -> bool:
    if not s.get("token"):
        s["token"] = secrets.token_urlsafe(24)
        return True
    return False


def _migrate(s: dict[str, Any], raw: dict[str, Any]) -> bool:
    """旧单 provider → providers[]；空则种一个 DeepSeek 默认条目。
    返回是否发生了迁移（用于判断是否需要落盘）。"""
    if s.get("providers"):
        return False
    old = raw.get("provider")
    if isinstance(old, dict):
        base = old.get("base_url", "")
        if "deepseek" in base:
            presets = _deepseek_presets()
            active_label = presets[0]["label"]
        else:
            m = old.get("model") or "默认"
            presets = [{"label": m, "model": m, "extra_body": {}}]
            active_label = m
        prov = {
            "id": uuid.uuid4().hex[:12],
            "name": "DeepSeek" if "deepseek" in base
            else (base.split("//")[-1].split("/")[0] or "默认"),
            "format": old.get("format", "openai_compat"),
            "base_url": base,
            "api_key": old.get("api_key", ""),
            "capability": ("推理、代码、数学较强，长文本与中文表现不错"
                           if "deepseek" in base else ""),
            "presets": presets,
        }
        s["providers"] = [prov]
        s["active"] = {"provider_id": prov["id"],
                       "preset_label": active_label}
    else:
        prov = _default_provider()
        s["providers"] = [prov]
        s["active"] = {"provider_id": prov["id"],
                       "preset_label": prov["presets"][0]["label"]}
    s.pop("provider", None)
    return True


_BAK_PATH = SETTINGS_PATH.with_suffix(".json.bak")


def _read_raw() -> dict[str, Any] | None:
    """读 settings.json；解析失败回退 .bak；都失败返回 None
    （表示「未知/读取异常」，绝不当成空配置去重置，否则会丢 key）。"""
    for path in (SETTINGS_PATH, _BAK_PATH):
        if not path.exists():
            continue
        try:
            txt = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not txt.strip():
            continue
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            continue  # 可能读到并发写一半，试 .bak
    return None


def load_settings() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = SETTINGS_PATH.exists()
    raw = _read_raw()
    read_failed = raw is None and file_exists   # 文件在但读/解析异常
    truly_first = raw is None and not file_exists
    raw = raw or {}
    s = {**DEFAULTS, **raw}

    dirty = truly_first
    if _ensure_token(s):
        dirty = True
    if _migrate(s, raw):
        dirty = True
    # 回填：早于 P3 迁移过的 provider 没 capability，DeepSeek 给默认值
    for p in s.get("providers", []):
        if "capability" not in p:
            p["capability"] = ""
            dirty = True
        if not p["capability"] and "deepseek" in p.get("base_url", ""):
            p["capability"] = "推理、代码、数学较强，长文本与中文表现不错"
            dirty = True
    # 工作区白名单始终至少含本助手根目录
    ws = s.setdefault("workspace", {})
    roots = ws.setdefault("allowed_roots", [])
    if str(PROJECT_ROOT) not in roots:
        roots.insert(0, str(PROJECT_ROOT))
        dirty = True
    if not ws.get("cwd"):
        ws["cwd"] = str(PROJECT_ROOT)
        dirty = True
    ws.setdefault("test_cmd", "")

    # 读取异常时绝不落盘（防止用默认值覆盖、丢 key）；
    # 否则只有真有变更才写——杜绝「每次读都写」引发的并发损坏。
    if dirty and not read_failed:
        save_settings(s)
    return s


def save_settings(s: dict[str, Any]) -> None:
    """原子写入：先写 .tmp 再 os.replace；替换前备份上一份为 .bak。
    这样并发读取永远只会看到完整的旧文件或完整的新文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = json.dumps(s, ensure_ascii=False, indent=2)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(data, encoding="utf-8")
    if SETTINGS_PATH.exists():
        try:
            _BAK_PATH.write_text(
                SETTINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass
    os.replace(tmp, SETTINGS_PATH)


# ---------- providers ----------

def _find(providers: list[dict], pid: str) -> dict | None:
    return next((p for p in providers if p["id"] == pid), None)


def mask_provider(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"],
        "name": p.get("name", ""),
        "format": p.get("format", "openai_compat"),
        "base_url": p.get("base_url", ""),
        "api_key_set": bool(p.get("api_key")),
        "capability": p.get("capability", ""),
        "presets": p.get("presets", []),
    }


OLLAMA_PID = "__ollama__"


def _ollama_synthetic() -> dict[str, Any]:
    oc = load_settings()["ollama"]
    m = oc.get("model", "qwen2.5:3b")
    return {
        "id": OLLAMA_PID,
        "name": "本地 Ollama",
        "format": "openai_compat",
        "base_url": oc.get("base_url", "http://localhost:11434"),
        "api_key_set": True,  # 本地无需 key
        "capability": "本地小模型：免费、隐私，能力有限，适合简单/离线",
        "presets": [{"label": m, "model": m, "extra_body": {}}],
        "builtin": True,  # 前端据此禁用编辑/删除
    }


def public_state() -> dict[str, Any]:
    s = load_settings()
    provs = [mask_provider(p) for p in s["providers"]]
    provs.append(_ollama_synthetic())  # 合成「本地 Ollama」可选项
    return {"providers": provs, "active": s["active"]}


def _resolve_ollama() -> dict[str, Any]:
    oc = load_settings()["ollama"]
    return {
        "format": "openai_compat",
        "base_url": oc.get("base_url", "http://localhost:11434"),
        "api_key": "ollama",
        "model": oc.get("model", "qwen2.5:3b"),
        "extra_body": {},
        "provider_id": OLLAMA_PID,
        "provider_name": "本地 Ollama",
        "preset_label": oc.get("model", "qwen2.5:3b"),
    }


def get_active_resolved() -> dict[str, Any] | None:
    """供 /api/chat：返回 {format,base_url,api_key,model,extra_body}。"""
    s = load_settings()
    act = s.get("active", {})
    if act.get("provider_id") == OLLAMA_PID:
        return _resolve_ollama()
    prov = _find(s["providers"], act.get("provider_id", ""))
    if not prov:
        return None
    presets = prov.get("presets", [])
    preset = next(
        (x for x in presets if x["label"] == act.get("preset_label")),
        presets[0] if presets else None,
    )
    if not preset:
        return None
    return {
        "format": prov.get("format", "openai_compat"),
        "base_url": prov.get("base_url", ""),
        "api_key": prov.get("api_key", ""),
        "model": preset.get("model", ""),
        "extra_body": preset.get("extra_body", {}) or {},
        "provider_id": prov["id"],
        "provider_name": prov.get("name", ""),
        "preset_label": preset.get("label", ""),
    }


def resolve_choice(provider_id: str, preset_label: str) -> dict[str, Any] | None:
    """按指定 provider+preset 解析（供路由器选择后使用）。"""
    if provider_id == OLLAMA_PID:
        return _resolve_ollama()
    s = load_settings()
    prov = _find(s["providers"], provider_id)
    if not prov:
        return None
    presets = prov.get("presets", [])
    preset = next(
        (x for x in presets if x["label"] == preset_label),
        presets[0] if presets else None,
    )
    if not preset:
        return None
    return {
        "format": prov.get("format", "openai_compat"),
        "base_url": prov.get("base_url", ""),
        "api_key": prov.get("api_key", ""),
        "model": preset.get("model", ""),
        "extra_body": preset.get("extra_body", {}) or {},
        "provider_id": prov["id"],
        "provider_name": prov.get("name", ""),
        "preset_label": preset.get("label", ""),
    }


def providers_for_router() -> list[dict[str, Any]]:
    """给路由器看的精简清单（无密钥）。"""
    s = load_settings()
    out = []
    for p in s["providers"]:
        out.append({
            "id": p["id"],
            "name": p.get("name", ""),
            "capability": p.get("capability", ""),
            "has_key": bool(p.get("api_key")),
            "presets": [x["label"] for x in p.get("presets", [])],
        })
    return out


def get_ollama() -> dict[str, Any]:
    return load_settings()["ollama"]


def set_ollama(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    cur = dict(s.get("ollama", {}))
    for k in ("base_url", "model"):
        if k in patch and patch[k]:
            cur[k] = patch[k]
    s["ollama"] = cur
    save_settings(s)
    return cur


def get_brain() -> dict[str, Any]:
    return load_settings()["brain"]


def set_brain(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    cur = dict(s.get("brain", {}))
    for k in ("auto_route", "local_answer", "summary"):
        if k in patch and patch[k] is not None:
            cur[k] = bool(patch[k])
    if isinstance(patch.get("summary_threshold"), int):
        cur["summary_threshold"] = max(6, patch["summary_threshold"])
    s["brain"] = cur
    save_settings(s)
    return cur


def upsert_provider(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    providers: list[dict] = s["providers"]
    pid = patch.get("id")
    existing = _find(providers, pid) if pid else None
    if existing:
        for k in ("name", "format", "base_url", "capability", "presets"):
            if k in patch and patch[k] is not None:
                existing[k] = patch[k]
        if patch.get("api_key"):  # 空=不改
            existing["api_key"] = patch["api_key"]
        target = existing
    else:
        target = {
            "id": uuid.uuid4().hex[:12],
            "name": patch.get("name", "未命名"),
            "format": patch.get("format", "openai_compat"),
            "base_url": patch.get("base_url", ""),
            "api_key": patch.get("api_key", ""),
            "capability": patch.get("capability", ""),
            "presets": patch.get("presets", []),
        }
        providers.append(target)
        if not s["active"].get("provider_id"):
            s["active"] = {
                "provider_id": target["id"],
                "preset_label": (target["presets"][0]["label"]
                                 if target["presets"] else ""),
            }
    save_settings(s)
    return mask_provider(target)


def delete_provider(pid: str) -> bool:
    s = load_settings()
    before = len(s["providers"])
    s["providers"] = [p for p in s["providers"] if p["id"] != pid]
    if s["active"].get("provider_id") == pid:
        nxt = s["providers"][0] if s["providers"] else None
        s["active"] = {
            "provider_id": nxt["id"] if nxt else "",
            "preset_label": (nxt["presets"][0]["label"]
                             if nxt and nxt["presets"] else ""),
        }
    save_settings(s)
    return len(s["providers"]) < before


def set_active(provider_id: str, preset_label: str) -> dict[str, Any]:
    s = load_settings()
    s["active"] = {"provider_id": provider_id, "preset_label": preset_label}
    save_settings(s)
    return s["active"]


def get_system_prompt() -> str:
    return load_settings().get("system_prompt", "")


def set_system_prompt(text: str) -> str:
    s = load_settings()
    s["system_prompt"] = text or ""
    save_settings(s)
    return s["system_prompt"]


def get_workspace() -> dict[str, Any]:
    return load_settings()["workspace"]


def set_workspace(patch: dict[str, Any]) -> dict[str, Any]:
    s = load_settings()
    cur = dict(s.get("workspace", {}))
    if isinstance(patch.get("allowed_roots"), list):
        # 规范化、去重、只留存在的目录
        roots = []
        for r in patch["allowed_roots"]:
            try:
                rp = str(Path(r).resolve())
            except (OSError, RuntimeError):
                continue
            if rp not in roots:
                roots.append(rp)
        cur["allowed_roots"] = roots
    if "cwd" in patch and patch["cwd"] is not None:
        cur["cwd"] = str(patch["cwd"])
    if "test_cmd" in patch and patch["test_cmd"] is not None:
        cur["test_cmd"] = str(patch["test_cmd"])
    s["workspace"] = cur
    save_settings(s)
    return cur


def path_in_scope(target: str) -> bool:
    """target 解析后必须落在某个 allowed_root 内（防目录穿越）。"""
    roots = load_settings()["workspace"].get("allowed_roots", [])
    if not roots:
        return False
    try:
        p = Path(target).resolve()
    except (OSError, RuntimeError):
        return False
    for r in roots:
        try:
            if p == Path(r) or p.is_relative_to(Path(r)):
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def get_github() -> dict[str, Any]:
    return load_settings().get("github", {"token": "", "username": ""})


def set_github(token: str | None, username: str | None) -> dict[str, Any]:
    s = load_settings()
    g = dict(s.get("github", {}))
    if token:  # 空=不改，保留原 token
        g["token"] = token
    if username is not None:
        g["username"] = username
    s["github"] = g
    save_settings(s)
    return g


def get_skills_enabled() -> bool:
    return bool(load_settings().get("skills_enabled", True))


def set_skills_enabled(v: bool) -> bool:
    s = load_settings()
    s["skills_enabled"] = bool(v)
    save_settings(s)
    return s["skills_enabled"]


def get_theme() -> str:
    return load_settings().get("theme", "dark")


def set_theme(theme: str) -> str:
    s = load_settings()
    s["theme"] = theme if theme in ("dark", "light", "system") else "dark"
    save_settings(s)
    return s["theme"]
