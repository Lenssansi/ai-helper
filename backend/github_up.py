"""GitHub 上传向导：安全 .gitignore + 预览 + 建库推送（PAT）。

铁律：以下敏感/垃圾项强制写入 .gitignore，用户不可解除——绝不上传
密钥与隐私文件。上传前必须先 preview 让用户确认。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

# 强制排除（密钥/隐私/垃圾/大二进制）——永远进 .gitignore
FORCED = [
    ".env", ".env.*", "*.env", "*.key", "*.pem", "*.pfx",
    "*secret*", "*credentials*", "id_rsa", "id_rsa.*",
    "data/", "node_modules/", ".venv/", "venv/", "__pycache__/",
    "*.pyc", "dist/", "build/", "*.log", ".DS_Store", "Thumbs.db",
    "*.gguf", "*.bin", "*.safetensors",
]
_MARK = "# >>> ai-helper 安全排除（请勿删除：含密钥/隐私/大文件）"


def _run(args: list[str], cwd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=120, errors="replace", **kw)


def ensure_gitignore(path: str) -> list[str]:
    p = Path(path)
    gi = p / ".gitignore"
    existing = ""
    if gi.is_file():
        existing = gi.read_text(encoding="utf-8", errors="replace")
    have = {ln.strip() for ln in existing.splitlines()}
    add = [r for r in FORCED if r not in have]
    if add:
        block = "\n" + _MARK + "\n" + "\n".join(FORCED) + "\n"
        gi.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
    return add


# 疑似临时/脚手架文件：单下划线开头(排除 __dunder__)的 json/log/tmp/txt，
# 编辑器备份(.tmp/.bak/.orig/.rej/.swp/~)、OS 垃圾。命中即拦，要显式确认。
_SUSPECT_RE = re.compile(
    r"(^|/)_(?!_)[^/]*\.(json|log|tmp|txt)$"
    r"|\.(tmp|bak|orig|rej|swp)$"
    r"|~$"
    r"|(^|/)\.DS_Store$"
    r"|(^|/)Thumbs\.db$",
    re.IGNORECASE,
)
_BIG_BYTES = 25 * 1024 * 1024  # 单文件 >25MB 视为异常（疑似漏过的大二进制）
_MANY = 3000  # 待传文件数过多，疑似 .gitignore 失效（要把 venv/node_modules 推上去）


def _pending_files(path: str) -> list[str]:
    """git add -A 的 dry-run：按 .gitignore 过滤后『将被纳入』的文件清单。"""
    r = _run(["git", "add", "-A", "-n"], path)
    will: list[str] = []
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if ln.startswith("add '") and ln.endswith("'"):
            will.append(ln[5:-1])
    return will


def scan_suspects(path: str, files: list[str]) -> dict[str, Any]:
    """挑出疑似临时/超大/数量异常，返回 reasons（空=干净可直传）。"""
    p = Path(path)
    # 私人记忆区绝不该进公开库：即便 .gitignore 被改坏也在此兜底拦下
    private = [f for f in files
               if f.replace("\\", "/").startswith(".private-memory/")
               or "/.private-memory/" in f.replace("\\", "/")]
    temp = [f for f in files if _SUSPECT_RE.search(f)]
    big: list[str] = []
    for f in files:
        try:
            sz = (p / f).stat().st_size
        except OSError:
            continue
        if sz > _BIG_BYTES:
            big.append(f"{f} ({sz // 1024 // 1024}MB)")
    too_many = len(files) > _MANY
    reasons: list[str] = []
    if private:
        reasons.append(
            f"私人记忆 .private-memory/ {len(private)} 个(绝不该进公开库)"
        )
    if temp:
        reasons.append(f"疑似临时/脚手架文件 {len(temp)} 个")
    if big:
        reasons.append(f"超大文件(>25MB) {len(big)} 个")
    if too_many:
        reasons.append(
            f"待传文件数异常({len(files)}>{_MANY})，疑似 .gitignore 失效"
        )
    return {"temp": (private + temp)[:50], "big": big[:50],
            "too_many": too_many, "reasons": reasons}


def preview(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"不是目录：{path}")
    added = ensure_gitignore(path)
    if not (p / ".git").exists():
        r = _run(["git", "init", "-q"], path)
        if r.returncode != 0:
            raise ValueError(f"git init 失败：{r.stderr[:200]}")
    will = _pending_files(path)
    return {
        "path": str(p),
        "gitignore_added": added,
        "forced_excludes": FORCED,
        "will_upload": will[:1000],
        "will_count": len(will),
        "suspects": scan_suspects(path, will),
    }


def upload(path: str, repo: str, private: bool,
           token: str, username: str,
           confirm: bool = False) -> dict[str, Any]:
    """仅上传/更新【源码 + 说明文档】到 GitHub（不含安装包/Release）。

    上传前自检：发现疑似临时/超大/数量异常文件且未显式 confirm 时，
    直接返回 needs_confirm（不 commit/不 push），交由用户确认或清理。
    """
    if not token or not username:
        raise ValueError("未配置 GitHub Token / 用户名")
    # 清洗：用户名/仓库名都只取最后一段、去空白，杜绝拼出
    # github.com/owner/owner/repo.git 这种畸形地址
    username = username.strip().strip("/").split("/")[-1]
    repo = (repo or "").strip().strip("/").split("/")[-1]
    if not repo:
        raise ValueError("缺少仓库名")
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"不是目录：{path}")
    ensure_gitignore(path)
    if not (p / ".git").exists():
        _run(["git", "init", "-q"], path)
    # 提交前自检：脏东西没确认就拦下，绝不 commit/push
    pending = _pending_files(path)
    suspects = scan_suspects(path, pending)
    if suspects["reasons"] and not confirm:
        return {
            "ok": False,
            "needs_confirm": True,
            "suspects": suspects,
            "will_count": len(pending),
            "message": (
                "检测到可能不该上传的文件（"
                + "；".join(suspects["reasons"])
                + "）。请清理后重试，或确认无误后强制上传。"
            ),
        }
    _run(["git", "add", "-A"], path)
    # 确保有提交
    _run(["git", "-c", "user.email=ai-helper@local",
          "-c", "user.name=ai-helper",
          "commit", "-m", "upload via ai-helper", "--allow-empty"], path)
    _run(["git", "branch", "-M", "main"], path)

    # 建远程库（已存在则忽略 422 继续推送）
    try:
        with httpx.Client(timeout=20.0) as c:
            resp = c.post(
                "https://api.github.com/user/repos",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                json={"name": repo, "private": bool(private)},
            )
    except httpx.RequestError as e:
        raise ValueError(f"连接 GitHub 失败：{e}") from e
    if resp.status_code not in (201, 422):
        raise ValueError(
            f"建库失败 {resp.status_code}：{resp.text[:300]}"
        )

    remote = f"https://github.com/{username}/{repo}.git"
    _run(["git", "remote", "remove", "origin"], path)
    _run(["git", "remote", "add", "origin", remote], path)
    # GitHub 的 git-over-HTTPS 要 Basic(用户名:token)，不是 Bearer；
    # 用 extraHeader 传 Basic，token 不写进 .git/config
    import base64 as _b64
    _basic = _b64.b64encode(
        f"{username}:{token}".encode()
    ).decode()
    push = _run(
        ["git", "-c",
         f"http.extraHeader=Authorization: Basic {_basic}",
         "push", "-u", "origin", "main"],
        path,
    )
    if push.returncode != 0:
        raise ValueError(f"推送失败：{push.stderr[:300]}")
    return {
        "ok": True,
        "repo_url": f"https://github.com/{username}/{repo}",
        "created": resp.status_code == 201,
    }
