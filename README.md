# ai-helper

本地部署的个人 AI 助手。**v0.1.x 起以 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 为内核**,Electron 单窗外壳,VPN / 编程 / 订阅余额查询等独有能力通过插件接入。数据全本机,凭证不上云。

**当前版本:[v0.1.1](https://github.com/Lenssansi/ai-helper/releases)** —— 重构完成,6 个 aih-* 插件就绪。
**回退兜底:[v0.0.5](https://github.com/Lenssansi/ai-helper/releases/tag/v0.0.5)** —— 自研 FastAPI + React 架构,仍可双击 `backup-v0.0.5/ai-helper Setup 0.0.5.exe` 装。

---

## 装

下载最新 release 的 `ai-helper Setup 0.1.1.exe`(156 MB),双击安装。
- NSIS 中文界面,选目录,创桌面 + 开始菜单快捷方式
- 用户数据落 `%LOCALAPPDATA%\ai-helper\v01\`(SQLite + 凭证 + 订阅 + 配置)
- **覆盖安装保留所有数据**(`deleteAppDataOnUninstall=false`)

## 第一次启动

1. 桌面双击 ai-helper → 等 ~10s 启动 → 出登录页
2. 用户名 `astrbot`,初始密码看 `%LOCALAPPDATA%\ai-helper\v01\cmd_config.json` 里 `dashboard.password_hash` 旁的明文(首次启动生成,登录后**立刻改密**)
3. 登入后:左侧菜单 → **提供商** → 加至少一个 LLM(DeepSeek / OpenRouter / 通义 / Claude…)
4. 左侧 → **WebChat / 对话** → 像 ChatGPT 一样开聊

## 自带 6 个插件(aih-*)

| 插件 | 命令 / LLM 工具 | 干啥 |
|---|---|---|
| **aih-core** | `/aih-hello` `/aih-version` `/aih-where` | 自检 + 路径排障 |
| **aih-search** | LLM tool: `aih_web_search` | Tavily 联网搜索,LLM 函数调用,可走 AIH_PROXY |
| **aih-persona** | `/aih-persona-bind PID VID` | 给人格绑专属 LLM provider,@on_agent_begin 钩子自动切 |
| **aih-vpn** | `/aih-vpn-install` `/aih-vpn-sub-add` `/aih-vpn-test` `/aih-vpn-use` | mihomo 内核 + 订阅(自动 V2Ray→Clash YAML)+ 并发测速 + 节点切换 |
| **aih-balance** | `/aih-balance` | DeepSeek / OpenRouter / Moonshot / SiliconFlow 余额一键查 |
| **aih-coding** | tools: `aih_read_file` `aih_write_file` `aih_bash` `aih_list_dir` `aih_search_text` `aih_user_dirs`<br/>cmd: `/aih-coding-allow` `/aih-skill-list` | 给 LLM 编程能力 + 28 个 skills 自动激活(含 grill-with-docs 盘问 skill) |

## 自家 LLM 工具配置

| 工具 | 配置文件 / 命令 | 必填字段 |
|---|---|---|
| Tavily 搜索 | `%LOCALAPPDATA%\ai-helper\v01\aih-config.json` | `{"tavily_api_key": "tvly-..."}` |
| VPN 订阅 | WebChat 输 `/aih-vpn-sub-add 名字 https://订阅URL` | 自动拉取 |
| 编程工作目录 | WebChat 输 `/aih-coding-allow D:\绝对路径` | 加白名单 |

## 开发运行(从源码)

要求:Windows 10+,Python 3.12,Node 22+,git

```bash
git clone https://github.com/Lenssansi/ai-helper.git
cd ai-helper
# Python 依赖(uv 已自带在 v01/.tools/uv/)
v01\.tools\uv\uv.exe sync --project v01
# Node 依赖(app/ 装一次,v0.1.x electron 复用)
cd app && npm install && cd ..
# 启动
start-v01.bat
```

## 安全基线

- dashboard 锁 `127.0.0.1:6185`,**不开局域网**
- LLM 编程工具(`aih_bash` / `aih_write_file` 等)走 `%LOCALAPPDATA%\ai-helper\v01\aih-config.json` 的 `coding_roots[]` 白名单,**路径穿越拒绝**
- mihomo 配置 `external-controller=""`,关闭 mihomo 自身 API,**不开放控制接口**
- mihomo 内核下载走官方 GitHub 镜像 + 三镜像兜底,**SHA256 锁死**(`e4bc371cd44...`)
- 凭证 / 订阅 / 对话历史全本机存(`%LOCALAPPDATA%`),不上云、不分析、不上报

## 目录结构

```
ai-helper/
├── v01/                          v0.1.x 主体
│   ├── bootstrap.py              入口脚本(spawn 后通过 AIH_PACKED env 切 dev/packed)
│   ├── electron/                 Electron 单窗外壳 + electron-builder 配置
│   ├── .tools/uv/                uv standalone 二进制(自携)
│   ├── .venv/                    uv 建的 Python 3.12 + AstrBot 4.25.2
│   └── data/
│       ├── plugins/aih-*/        6 个自家插件源码(进仓库)
│       ├── plugins/astrbot_*     第三方插件(从插件市场装,不进仓库)
│       ├── dist/                 AstrBot dashboard 静态(运行时下载,不进仓库)
│       └── data_v4.db            SQLite(不进仓库)
├── mihomo/mihomo.exe             VPN 内核(不进仓库,SHA256 验证后下载)
├── skills/                       克隆的 skills 仓(不进仓库)
├── backup-v0.0.5/                v0.0.5 安装包备份(回退兜底)
├── app/                          v0.0.5 老前端 + 复用其 electron-builder
├── backend/                      v0.0.5 老 FastAPI 后端
└── README.md
```

## 路线图

| 版本 | 内容 | 状态 |
|---|---|---|
| v0.0.x | FastAPI + React + 自研 Agent,107 MB | ✅ 锁定 v0.0.5 |
| **v0.1.1** | AstrBot 内核 + 6 个 aih-* 插件 + Electron 套壳,156 MB | ✅ **当前** |
| v0.2.x | 视实际使用反馈 | 📅 待定 |

## License

AGPL-3.0(继承自 AstrBot)。个人本地自用 + 朋友间分享不受影响;商业部署或对外提供网络服务需开源衍生作品。
