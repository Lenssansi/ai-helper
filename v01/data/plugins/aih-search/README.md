# aih-search

给 LLM 注册一个**联网搜索工具**(`aih_web_search`),后端 Tavily,出口走 ai-helper 自带 VPN(`AIH_PROXY`)。

> ⚠️ **是否还需要本插件?** AstrBot 4.25 **原生**已自带联网搜索(设置 → 服务商配置 → 联网搜索,支持 Tavily / 博查 / Brave / Firecrawl / 百度 共 5 个后端)。
> - 想要**多后端 + 官方维护** → 用原生那个,**关掉本插件**(避免 LLM 同时看到两个搜索工具而困惑)。
> - 想要搜索出口**自动跟随 aih-vpn 的节点切换** → 留着本插件(原生搜索走的是全局 `http_proxy` 环境变量,不读 aih-vpn 设的 `AIH_PROXY`)。
> - 国内直连不想开 VPN → 原生搜索选**博查 / 百度**后端最省事(本插件的 Tavily 是海外的,必须走 VPN)。

## LLM 工具

| 工具 | 用途 |
|---|---|
| `aih_web_search(query, max_results)` | LLM 函数调用:联网搜索,返回带标题/链接/摘要的结果。出口优先读 `AIH_PROXY`(aih-vpn `/aih-vpn-use` 成功后自动设),其次环境变量,最后直连。 |

## 命令

| 命令 | 用途 |
|---|---|
| `/aih-search-check` | 自检:报 API key 是否已配、当前走代理还是直连、Tavily 端点 |

## 配置(dashboard → 插件 → 齿轮)

| 卡片 | 项 | 说明 |
|---|---|---|
| Tavily 联网搜索 | API Key | 在 tavily.com 注册的 key |
| Tavily 联网搜索 | 默认最大结果数 | 1–10,默认 7 |
| Tavily 联网搜索 | 搜索深度 | basic(快/省额度)/ advanced(深) |
| 代理设置 | 代理 URL 强制覆盖 | 留空 = 跟随 `AIH_PROXY`;手填则强制走该代理 |
