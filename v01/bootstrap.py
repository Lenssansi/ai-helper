"""ai-helper v0.1.x 启动入口 —— 以 AstrBot 为后端核心。

两种运行模式:
  dev 模式(直接 python bootstrap.py 或 start-v01.bat):
    ASTRBOT_ROOT = HERE (D:\\ai-helper\\v01\\)
    数据写在源码目录,便于开发迭代
  packed 模式(Electron 安装包,AIH_PACKED=1):
    ASTRBOT_ROOT = %LOCALAPPDATA%\\ai-helper\\v01\\
    install 目录 read-only,upgrade-overwrite 不丢用户数据
    首启把 install/data/plugins/aih-* 和 install/data/dist sync 到用户目录

dashboard 默认端口 6185(可通过 DASHBOARD_PORT 环境变量改)。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # dev: D:\ai-helper\v01\, packed: install/resources/
PACKED = os.environ.get("AIH_PACKED") == "1"


def _choose_user_root() -> Path:
    """packed 模式下用户数据放哪。Windows 通用做法:%LOCALAPPDATA%/<App>/。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ai-helper" / "v01"


def _sync_bundled(install_root: Path, user_root: Path) -> None:
    """把 install_root 下的 read-only 资源刷到 user_root。

    覆盖式 sync(以 install 为准):
    - data/plugins/aih-*/  我们自家插件,每次启动以 install 为权威
    - data/dist/           dashboard 静态,有 install 版本就用 install 版本
    用户加的第三方插件(非 aih-*)不动。
    """
    # plugins
    src_plugins = install_root / "data" / "plugins"
    dst_plugins = user_root / "data" / "plugins"
    dst_plugins.mkdir(parents=True, exist_ok=True)
    if src_plugins.exists():
        for src in src_plugins.glob("aih-*"):
            dst = dst_plugins / src.name
            try:
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            except OSError as e:
                print(f"[ai-helper-v01] WARN: sync 插件 {src.name} 失败:{e}")

    # dashboard dist —— 只在用户侧不存在或 index.html 缺失时刷,省 22MB IO
    src_dist = install_root / "data" / "dist"
    dst_dist = user_root / "data" / "dist"
    if src_dist.exists() and not (dst_dist / "index.html").exists():
        try:
            if dst_dist.exists():
                shutil.rmtree(dst_dist, ignore_errors=True)
            shutil.copytree(src_dist, dst_dist)
        except OSError as e:
            print(f"[ai-helper-v01] WARN: sync dashboard 失败:{e}")


# ---------------------------------------------------- 路径决策

if PACKED:
    INSTALL_ROOT = HERE  # install/resources/
    USER_ROOT = _choose_user_root()
    USER_ROOT.mkdir(parents=True, exist_ok=True)
    (USER_ROOT / ".astrbot").touch(exist_ok=True)
    for sub in ("data", "data/config", "data/plugins", "data/temp"):
        (USER_ROOT / sub).mkdir(parents=True, exist_ok=True)
    _sync_bundled(INSTALL_ROOT, USER_ROOT)
    os.environ.setdefault("ASTRBOT_ROOT", str(USER_ROOT))
    # 让 aih-vpn 找到 install 里的 mihomo,aih-coding 找到 install 里的 skills
    os.environ.setdefault("AIH_MIHOMO_DIR", str(INSTALL_ROOT / "mihomo"))
    os.environ.setdefault("AIH_SKILLS_DIR", str(INSTALL_ROOT / "skills"))
    print(f"[ai-helper-v01] PACKED mode")
    print(f"[ai-helper-v01] INSTALL_ROOT = {INSTALL_ROOT}")
    print(f"[ai-helper-v01] USER_ROOT    = {USER_ROOT}")
else:
    # dev:数据钉在源码目录,跟 v0.0.5 完全隔离
    (HERE / ".astrbot").touch(exist_ok=True)
    for sub in ("data", "data/config", "data/plugins", "data/temp"):
        (HERE / sub).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ASTRBOT_ROOT", str(HERE))

# Dashboard 默认锁 127.0.0.1
os.environ.setdefault("DASHBOARD_HOST", "127.0.0.1")

# Windows GBK 控制台 ↔ AstrBot 含 Unicode 日志的兼容兜底
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

print(f"[ai-helper-v01] ASTRBOT_ROOT  = {os.environ['ASTRBOT_ROOT']}")
print(f"[ai-helper-v01] DASHBOARD_HOST = {os.environ['DASHBOARD_HOST']}")
print(f"[ai-helper-v01] Python        = {sys.version.split()[0]}")


async def ensure_dashboard() -> None:
    """确保 dashboard 静态资源就位。

    AstrBot wheel 不打包 dashboard/dist。
    dev:首启从 soulter CDN 下;packed:bootstrap 启动时已从 install 同步过来。

    幂等:dist/index.html 存在就直接跳过。
    """
    root = Path(os.environ["ASTRBOT_ROOT"])
    dist_index = root / "data" / "dist" / "index.html"
    if dist_index.exists():
        print(f"[ai-helper-v01] Dashboard already installed at {dist_index.parent}")
        return

    if PACKED:
        # packed 模式下 dist 应该从 install sync 过来了;若仍缺,说明 install 内也没有
        print(f"[ai-helper-v01] 警告:packed 模式 dist 缺失,install 包结构异常")
        return

    from astrbot.core.config.default import VERSION
    from astrbot.core.utils.io import download_dashboard

    print(f"[ai-helper-v01] Dashboard 缺失,首次下载 v{VERSION}…")
    zip_path = root / "data" / "dashboard.zip"
    try:
        await download_dashboard(
            path=str(zip_path),
            extract_path=str(root / "data"),
            version=f"v{VERSION}",
            latest=False,
        )
    except Exception as e:
        print(f"[ai-helper-v01] Dashboard 下载失败:{e}")
        raise

    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    if dist_index.exists():
        print(f"[ai-helper-v01] Dashboard 安装完成 -> {dist_index.parent}")


async def main() -> None:
    from astrbot.core import LogBroker, LogManager, db_helper, logger
    from astrbot.core.initial_loader import InitialLoader

    log_broker = LogBroker()
    LogManager.set_queue_handler(logger, log_broker)

    await ensure_dashboard()

    loader = InitialLoader(db_helper, log_broker)
    print("[ai-helper-v01] Starting AstrBot…")
    await loader.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[ai-helper-v01] 收到 Ctrl+C,退出")
