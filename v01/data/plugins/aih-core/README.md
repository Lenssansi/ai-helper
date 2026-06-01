# aih-core

ai-helper 插件套件的**基础包**。只做健康自检和路径排障,不碰业务。其它 `aih-*` 插件各自独立,不强依赖本包。

## 命令

| 命令 | 用途 |
|---|---|
| `/aih-hello` | 验证 ai-helper 插件系统正常加载,返回当前版本号 |
| `/aih-version` | 报 ai-helper / AstrBot / Python 三个版本,排障时先发这条 |
| `/aih-where` | 报关键路径(ASTRBOT_ROOT / data 目录 / 本插件路径),确认数据没落到 `~/.astrbot` 而是钉在项目内 |

## 说明

- 无需配置,无 LLM 工具。
- 这三条命令都是给你(管理员)看的诊断命令,不参与对话。
