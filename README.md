# ai-helper

本地部署的个人 AI 助手：对话 + 多 API 管理 + 编程 Agent。自用。

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
