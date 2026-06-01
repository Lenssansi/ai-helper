"""ai-helper v0.1.x 启动入口 —— 以 AstrBot 为后端核心。

设计原则(配合 ai-helper 项目铁律):
- 全部本地化:ASTRBOT_ROOT 钉在本项目 v01/ 子目录,
  数据进 v01/data/,不落 ~/.astrbot,不污染系统。
- v0.0.5 老后端(backend/.venv + main.py)和老前端(app/electron)
  完全不动,继续可用作回退兜底(git tag v0.0.5)。
- 这个脚本被 Electron 主进程(v0.1.x 的 main.cjs 重写后)拉起。

dashboard 默认端口 6185(可通过 DASHBOARD_PORT 环境变量改)。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # D:\ai-helper\v01\

# 1) 数据根:钉在 v01/ 内 ── 不落系统目录,跟 v0.0.5 的 %APPDATA% 隔离
os.environ.setdefault("ASTRBOT_ROOT", str(HERE))

# 2) Dashboard 默认锁 127.0.0.1 —— AstrBot 默认 0.0.0.0(对局域网开放),
#    跟 ai-helper 的安全基线冲突。这里强制单本机;有需要再用环境变量覆盖。
os.environ.setdefault("DASHBOARD_HOST", "127.0.0.1")

# 3) 强制 UTF-8 stdout —— Windows 控制台默认 GBK,AstrBot 日志含 ✨ 等
#    Unicode,loguru 会 UnicodeEncodeError 失败(不影响 dashboard,但烦人)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass  # 重定向到文件等场景没 reconfigure 也无所谓

# 4) 兜底确保结构存在(避免首次跑时找不到目录而崩)
(HERE / ".astrbot").touch(exist_ok=True)
for sub in ("data", "data/config", "data/plugins", "data/temp"):
    (HERE / sub).mkdir(parents=True, exist_ok=True)

print(f"[ai-helper-v01] ASTRBOT_ROOT  = {HERE}")
print(f"[ai-helper-v01] DASHBOARD_HOST = {os.environ['DASHBOARD_HOST']}")
print(f"[ai-helper-v01] Python        = {sys.version.split()[0]}")


async def ensure_dashboard() -> None:
    """确保 dashboard 静态资源就位。

    AstrBot wheel 不打包 dashboard/dist,首次启动需要下载。
    官方 CLI 用 click.confirm 交互式询问,本进程是无人值守的 Electron 子进程,
    所以这里直接调底层 download_dashboard,跳过交互。

    幂等:dist/index.html 存在就直接跳过,不重复下载。
    """
    dist_index = HERE / "data" / "dist" / "index.html"
    if dist_index.exists():
        print(f"[ai-helper-v01] Dashboard already installed at {dist_index.parent}")
        return

    from astrbot.core.config.default import VERSION
    from astrbot.core.utils.io import download_dashboard

    print(f"[ai-helper-v01] Dashboard 缺失,首次下载 v{VERSION}…")
    zip_path = HERE / "data" / "dashboard.zip"
    try:
        await download_dashboard(
            path=str(zip_path),
            extract_path=str(HERE / "data"),
            version=f"v{VERSION}",
            latest=False,
        )
    except Exception as e:
        print(f"[ai-helper-v01] Dashboard 下载失败:{e}")
        print(f"[ai-helper-v01] 可手动从 https://github.com/AstrBotDevs/AstrBot/releases")
        print(f"[ai-helper-v01] 下载 AstrBot-v{VERSION}-dashboard.zip,解压到 {HERE / 'data'}/")
        raise

    # 下完清掉 zip,省 30+MB 空间
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    if dist_index.exists():
        print(f"[ai-helper-v01] Dashboard 安装完成 -> {dist_index.parent}")
    else:
        print(f"[ai-helper-v01] 警告:下载完成但 {dist_index} 仍不存在,zip 结构异常?")


async def main() -> None:
    """启 AstrBot 核心 + Dashboard,跑到 Ctrl+C / 外部 kill。"""
    from astrbot.core import LogBroker, LogManager, db_helper, logger
    from astrbot.core.initial_loader import InitialLoader

    log_broker = LogBroker()
    LogManager.set_queue_handler(logger, log_broker)

    # InitialLoader.start() 之前先备好 dashboard 静态文件,否则 6185 端口
    # 会返回 404(因为 wheel 不打包 dist,需要从 soulter CDN 拉)
    await ensure_dashboard()

    loader = InitialLoader(db_helper, log_broker)
    print("[ai-helper-v01] Starting AstrBot…")
    await loader.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[ai-helper-v01] 收到 Ctrl+C,退出")
