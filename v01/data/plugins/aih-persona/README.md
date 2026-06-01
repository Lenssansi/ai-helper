# aih-persona

给每个**人格绑定专属的 LLM provider**。绑定后,只要对话切到该人格,就自动用绑定的模型——不动 AstrBot 的全局默认。

## 工作原理

- 在 `@on_agent_begin` 钩子里(AstrBot 选 provider 之前)触发,读当前会话的 `persona_id`,查绑定表,调 `provider_manager.set_provider(..., umo=...)` 做**会话级**临时切换,不污染全局默认。
- 绑定关系存在主库 `data_v4.db` 的独立小表 `aih_persona_provider`,**不改 AstrBot 自家的 personas 表**(避免它升级时冲突)。

## 命令

| 命令 | 用途 |
|---|---|
| `/aih-persona-list` | 列出所有人格 + 各自当前绑定,并列出可选的 provider_id |
| `/aih-persona-bind <persona_id> <provider_id>` | 把某人格绑定到某 provider |
| `/aih-persona-unbind <persona_id>` | 解除绑定,该人格回到全局默认 provider |
| `/aih-persona-check` | 自检:报当前会话的人格、应切的 provider、实际在用的 provider |

## 说明

- 无需配置,无 LLM 工具。
- 用法:先 `/aih-persona-list` 看 persona_id 和 provider_id,再 `bind`。
- 绑定的 provider 若之后被删,钩子会跳过切换(留 warning),不报错中断对话。
