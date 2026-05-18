"""信任分级 + 权限矩阵。

按请求来源 IP 判定信任级别：
- loopback(127.0.0.1 / ::1) = local  受信，全功能（Electron 外壳 / 本机浏览器）
- 其它（局域网 / ZeroTier 虚拟 IP）= remote  受限

安全基线（已与用户确认的「令牌 + 矩阵」档位）：
1. 远程默认关闭；remote_enabled=False 时，非 loopback 请求一律拒绝。
2. remote_enabled=True 时，非 loopback 请求必须带正确令牌
   （HTTP 头 X-Access-Token 或查询参数 token）。
3. 所有限制在后端强制——前端隐藏只是体验，绝不依赖前端。

P0 只做：分级、令牌校验、/api/whoami 暴露矩阵，以及一个可复用的
require_permission() 依赖，供后续阶段的真实路由（API 管理、设置、Agent…）挂载。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from config import load_settings

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# 权限矩阵：True = 允许。键名是后续路由会引用的能力名。
PERMISSION_MATRIX: dict[str, dict[str, bool]] = {
    "local": {
        "chat": True,
        "api_switch": True,
        "api_manage": True,   # 增删改查 API + 查看密钥
        "file_read": True,
        "file_write": True,
        "agent": True,
        "github_upload": True,
        "settings": True,
    },
    "remote": {
        "chat": True,
        "api_switch": True,   # 仅切换当前使用的 API，不暴露/不可改密钥
        "api_manage": False,  # 密钥绝不出本机
        "file_read": True,
        "file_write": True,   # 高危操作仍走确认护栏（Agent 阶段实现）
        "agent": True,        # 高危操作仍需确认，不可远程绕过
        "github_upload": False,
        "settings": False,    # 远程不能改远程开关/令牌，否则可自我解锁
    },
}


@dataclass
class Caller:
    trust: str           # "local" | "remote"
    client_host: str

    @property
    def permissions(self) -> dict[str, bool]:
        return PERMISSION_MATRIX[self.trust]

    def can(self, capability: str) -> bool:
        return self.permissions.get(capability, False)


def get_caller(request: Request) -> Caller:
    """识别调用方信任级别，并执行远程访问的基线门禁。"""
    client_host = request.client.host if request.client else "unknown"
    is_loopback = client_host in _LOOPBACK

    if is_loopback:
        return Caller(trust="local", client_host=client_host)

    settings = load_settings()
    if not settings.get("remote_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="远程访问未开启（仅本机可用）。如需远程，请在本机设置页开启。",
        )

    presented = request.headers.get("X-Access-Token") or request.query_params.get(
        "token"
    )
    expected = settings.get("token") or ""
    if not expected or presented != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="远程访问需要有效令牌（X-Access-Token）。",
        )

    return Caller(trust="remote", client_host=client_host)


def require_permission(capability: str):
    """路由依赖工厂：后续阶段给受限路由挂上，例如

        @app.post("/api/providers")
        def create_provider(caller: Caller = Depends(require_permission("api_manage"))):
            ...
    """

    def _dep(caller: Caller = Depends(get_caller)) -> Caller:
        if not caller.can(capability):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"当前为「{caller.trust}」访问，无权执行该操作（{capability}）。",
            )
        return caller

    return _dep
