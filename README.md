# ai-helper

本地部署的个人 AI 助手:对话 + 多 API 管理 + 编程 Agent + VPN 转发。自用。

**当前版本:[v0.0.3](https://github.com/Lenssansi/ai-helper/releases) ·**
后续版本变动同步更新本节。

## 使用说明

### 安装

1. 下载 `release/v0.0.3/ai-helper Setup 0.0.3.exe` 双击安装(NSIS,无需 Python/Node)
2. 桌面快捷方式启动;首次启动自动拉起内置后端 `127.0.0.1:8756`
3. 用户数据落 `%APPDATA%/ai-helper/`(API key / 对话历史 / VPN 订阅)

### 配置一个 API(以国内 DeepSeek 为例)

1. 左栏「API 管理」→「＋ 添加 API」
2. 名称 `DeepSeek` / format `openai_compat` / base_url `https://api.deepseek.com` / api_key `sk-...`
3. 点「自动发现模型」拉模型列表 → 常用模型行点 ☆ 变 ★(**置顶**)
4. 顶部模型切换器选这个 → 去对话页发条消息验证
5. 若 host 支持(DeepSeek / OpenRouter / Moonshot / SiliconFlow),卡片会显示**余量进度条**

### 走 VPN 调境外 API(Gemini / Claude / OpenRouter / Mistral / Groq…)

> 国内必走代理。下载 [mihomo.exe](https://github.com/MetaCubeX/mihomo/releases) 放 `D:\ai-helper\mihomo\mihomo.exe`(或加 PATH)。

1. 左栏「VPN 订阅」→「＋ 添加订阅」→ 粘 Clash 订阅 URL,或文件导入 YAML
   - 机场返的是 base64 V2Ray URI?**系统自动转 Clash YAML**
2. 点「测全部延迟」→ 看节点排序(绿<200ms / 黄<500 / 红失败)
3. 回「API 管理」→ 编辑要走 VPN 的 provider:
   - 勾「走 VPN」→ 选订阅 → 勾候选节点 → 选最快的设为活跃
4. 保存。**API 卡片上会出现节点 chip 列表,一键切换**;调用此 API 时自动启 mihomo 子代理,关 app 一起停

> ⚠ **Gemini 个人 key 对出口 IP 严格风控**,机场数据中心 IP 多数被 `User location is not supported` 拒。推荐改用 OpenRouter 反代(`https://openrouter.ai/api/v1`),IP 限制宽松。

### 五大页面

| 页面 | 用途 | 亮点 |
|---|---|---|
| **对话** | 跟选定模型聊天 | 流式 · Markdown · Mermaid · 代码高亮 · 文件读写 · 联网搜索 |
| **编程** | Agent 多轮工具循环 | TODO 面板 · 高危确认 · Git 自动回滚 · 改后跑测试 |
| **API 管理** | 管 provider / 余量 / 节点 | 余量条 · 节点 chip 切换 · 测连通 · 自动发现模型 |
| **VPN 订阅** | 管订阅 / 节点 / 规则 | 自动转 YAML · TCP 批量测延迟 · 流量到期信息 |
| **设置** | 主题 / 远程 / 联网 / 日志 / 全局提示 | 三向主题(亮/暗/跟随) · 滚动日志 |

### 统一模型切换器(任意页面右上角)

- 顶部搜索框过滤(模型 ID / 标签 / 描述)
- 每个 API 一个**折叠组**(标题 sticky,滚到中途也能点)
- **聚合器**(OpenRouter / Together 等)内部按 `vendor/` **二级嵌套**
- 每行 **★/☆** 一键置顶 / 取消(置顶项在组顶)

### 自动路由(本地小模型当调度,需 Ollama)

设置里开「本地直答」+「自动路由」:
- 打招呼 / 闲聊 / 小问答 → **本地 Ollama 直答**(免费,无 token 消耗)
- 写代码 / 复杂推理 / 长文 → 选最适合的云端 API(读 capability + preset.description)
- **聚合器仅 ★ 置顶预设参与候选**(避免乱选几百个模型)

### GitHub 上传

设置 → 填 PAT + 仓库 → 「上传到 GitHub」 → 一键推**源码 + AI 生成说明文档**;data/ / mihomo/ / 安装包等自动 .gitignore 拦截,绝不外泄。

### 数据隐私

API key / 对话历史 / VPN 订阅全部存本机 `%APPDATA%/ai-helper/`,不上云、不分析、不上报。远程访问默认关,开启后非本机请求强制令牌。

## 已锁定决策

| 项 | 选择 |
|---|---|
| 技术栈 | Electron 薄外壳 + Python(FastAPI) 后端；后端为唯一核心，同时服务本机 Electron 与浏览器 |
| 前端 | Vite + React + TypeScript |
| 本地小模型 | Ollama（路由 + 摘要 + 简单对话；P3 接入） |
| API 适配 | OpenAI 兼容(优先) → Anthropic → Gemini → 通用自定义 |
| 图标 | assets/robot.png（机器人终端图），夏莱.png 留作备选 |
| 远程访问 | 默认关闭；开启需令牌；权限按来源分级（见下） |

## 访问分级（安全基线：令牌 + 权限矩阵）

- **本地**（loopback / Electron / 本机浏览器）= 全功能。
- **远程**（局域网 / ZeroTier）= 受限：可对话、切换 API、读写文件、用 Agent（高危仍弹窗确认）；
  **不可**增删改 API/看密钥、不可改设置、不可一键传 GitHub。
- 远程默认关闭；开启后非本机请求必须带令牌（`X-Access-Token`）。
- 所有限制在后端强制，前端隐藏只是体验。

## 目录结构

```
app/        Electron + React 前端
backend/    Python FastAPI 后端（providers / agent / skills 后续阶段填充）
assets/     图标资源
data/       本地配置/历史（gitignore，含远程令牌、未来 API 密钥）
```

## 本地模型 (Ollama)

为省 C 盘，Ollama 已整体迁到 D：模型在 `ollama/models`（环境变量
`OLLAMA_MODELS`），程序在 `ollama/app`（C 原安装路径以 junction 透明指向）。
应用启动时若 Ollama 未运行会自动拉起，关闭时只停「自己拉起的」那个。

## 开发运行

前置：已装 Node、Python 3.11、git（本机均已具备）。

双击 `开发启动.bat`：自动建 venv 装后端依赖 → 新窗口起后端(127.0.0.1:8756)
→ npm install → Vite(5173) + Electron 窗口。

浏览器访问同一界面：开发期开 `http://127.0.0.1:5173`。

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 脚手架 + 三页导航 + 图标 + 访问分级骨架 | ✅ 完成 |
| P1 | 对话页打通一个 OpenAI 兼容 API + 富渲染 + 对话持久化 | ✅ 完成 |
| P2 | 多 API 管理（预设=模型+模式）+ 双下拉切换 + 三向主题 | ✅ 完成 |
| P3 | Ollama 本地模型 + 任务路由 + 本地直答 + 滚动摘要 + 起停联动 | ✅ 完成 |
| P4 | 编程 Agent：逐步工具循环 / 作用域护栏 / Git 检查点回滚 / 高危确认 / 改后测试 / 迭代多轮 / 会话持久化 | ✅ 完成 |
| 增强 | 对话改删消息 · 联网搜索(方案A+日期注入·绑会话) · 普通对话全盘文件(读查自动/增删改确认/远程禁) · 本地 Ollama 选项 | ✅ 完成 |
| P5 | mattpocock/skills 仅编程注入(盘问置顶) + GitHub 上传向导(PAT·安全.gitignore·预览) | ✅ 完成 |
| 桌面启动 | Electron 单窗(隐藏拉起后端/Vite/Ollama)+ 带图标桌面快捷方式 | ✅ 完成 |
| 打包 | `打包.bat`(PyInstaller 冻结后端 + electron-builder NSIS),安装包产物在**项目根 `release/`**,自行分享 | ✅ 完成 |
| GitHub | 统一上传(白名单下拉,含本程序)**仅源码+说明文档**(不含安装包/Release)+ 安全.gitignore + AI 生成「说明文档(项目描述)」;打包分发版自动隐藏 | ✅ 完成 |
| E2 | 对话指令改设置(除全局提示词,高危确认,仅本机)+ 对话一句话 `github_push_source`/`make_description`(只更新源码与说明文档) | ✅ 完成 |
| v0.0.3 | VPN 订阅 + mihomo 子代理 + V2Ray URI 自动转 Clash YAML + 批量 TCP 测延迟 + 余量条(DS/OR/Moonshot/SF) + 统一 ModelSwitcher + ★ 置顶 + 描述 + 自动路由聚合器过滤 | ✅ 完成 |
