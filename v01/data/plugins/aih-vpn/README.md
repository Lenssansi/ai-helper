# aih-vpn

用本机 **mihomo(Clash.Meta)** 内核给 ai-helper 提供按需 VPN:内核安装、订阅管理、节点测速、起停代理。`/aih-vpn-use` 成功后会写入 `AIH_PROXY` 环境变量,aih-search 等插件自动走这条出口。

> 多数命令的「订阅」参数可以用**订阅名字**代替那串 ID(单条订阅时甚至能省略)。

## 命令

| 命令 | 用途 |
|---|---|
| `/aih-vpn-install [force]` | 下载/更新/重装 mihomo 内核(官方源 + 镜像兜底 + SHA256 验签)。加 `force` 强制重下 |
| `/aih-vpn-version` | 报已装内核版本 + 目标版本 |
| `/aih-vpn-status` | 报当前 `AIH_PROXY` 值 + 在跑的 mihomo 实例 |
| `/aih-vpn-stop` | 关掉所有 mihomo 实例并清空 `AIH_PROXY` |
| `/aih-vpn-sub-add <名字> <订阅URL>` | 导入一条订阅(自动把 V2Ray/base64 转 Clash YAML) |
| `/aih-vpn-sub-list` | 列出所有订阅(名字 + 节点数 + 更新时间 + ID) |
| `/aih-vpn-sub-del <ID\|名字>` | 删除一条订阅 |
| `/aih-vpn-sub-refresh <ID\|名字>` | 从原 URL 重新拉取该订阅 |
| `/aih-vpn-test <ID\|名字>` | 并发 TCP 测速,按延迟排序展示前 10 个节点 |
| `/aih-vpn-use <ID\|名字> <节点名>` | 启动该节点的 mihomo,把 `AIH_PROXY` 设为它的本地代理 URL |

## 典型流程

```
/aih-vpn-install            # 首次装内核(已装则跳过)
/aih-vpn-sub-add 主订阅 https://…   # 导入订阅
/aih-vpn-test 主订阅         # 测速找最快节点
/aih-vpn-use 主订阅 香港01    # 启动,拿到 http://127.0.0.1:79XX
```
拿到代理 URL 后:让某个 **LLM provider 也走 VPN** → dashboard → 提供商 → 编辑 → proxy 字段填那个 URL。

## 配置(dashboard → 插件 → 齿轮)

| 卡片 | 项 | 说明 |
|---|---|---|
| Mihomo 内核 | mihomo 目录路径覆盖 | 留空自动定位;换位置才填 |
| Mihomo 内核 | 节点 TCP 测速超时 (秒) | 默认 4,慢机场调 8 |
| Mihomo 内核 | 节点测速并发数 | 默认 16 |
| 订阅 | 启动时自动导入的订阅 URL 列表 | 每行一个;重启时已存在同 URL 则跳过 |

## 安全基线

- mihomo 配置 `bind-address: 127.0.0.1` + `allow-lan: false` + `external-controller: ""`——**只服务本机、不开局域网、关掉 mihomo 自身 API**。
- 内核下载锁死官方 GitHub + 镜像,字节过 SHA256 校验才落盘,再 `-v` 实跑自检。
