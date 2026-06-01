# aih-balance

一键查询 AstrBot 已配置的 LLM provider 的**账户余额**。支持有公开余额端点的厂商;余额偏低时打 ⚠️。

## 命令

| 命令 | 用途 |
|---|---|
| `/aih-balance` | 查所有已配 provider 的余额,按余额升序排列 |
| `/aih-balance <provider_id>` | 只查指定 provider |
| `/aih-balance-hosts` | 列出支持余额查询的 host |

## 支持的厂商

| 厂商 | 端点 |
|---|---|
| DeepSeek | `api.deepseek.com/user/balance` |
| OpenRouter | `openrouter.ai/api/v1/auth/key` |
| Moonshot(.cn / .ai) | `/v1/users/me/balance` |
| SiliconFlow(硅基流动) | `/v1/user/info` |

> OpenAI / Anthropic / Gemini / Groq 等**没有公开余额端点**,会显示「无公开端点」,这是正常的,不是错误。

## 配置(dashboard → 插件 → 齿轮)

| 卡片 | 项 | 说明 |
|---|---|---|
| 余额预警 | 人民币余额预警线 (元) | 低于此值打 ⚠️,默认 5 |
| 余额预警 | 美元余额预警线 ($) | 默认 1 |
| 余额预警 | 剩余比例预警线 (0-1) | 已知总额时,剩余比例低于此打 ⚠️,默认 0.1 |
| 网络设置 | 查询请求超时 (秒) | 默认 12 |

## 说明

- 出口走 `AIH_PROXY`(aih-vpn 设的);海外厂商(OpenRouter)需先 `/aih-vpn-use` 起代理。
- 无 LLM 工具,纯管理命令。
