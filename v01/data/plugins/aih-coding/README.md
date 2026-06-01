# aih-coding

给 LLM 一套**编程能力工具**(读写文件 / 跑命令 / 搜代码),全部受授权目录白名单约束。另外提供一个把克隆仓里的 **skill 搬进 AstrBot 原生 skills 目录**的桥。

## LLM 工具(LLM 按需自动调用)

| 工具 | 用途 |
|---|---|
| `aih_user_dirs()` | 取本机真实 用户名/主目录/桌面/下载/文档 绝对路径(避免 LLM 瞎猜 `admin`) |
| `aih_list_dir(path)` | 列目录(限白名单内) |
| `aih_read_file(path)` | 读文件(限白名单,编码兜底,200KB 上限) |
| `aih_write_file(path, content)` | 写/覆盖文件(限白名单,10MB 上限) |
| `aih_bash(command, cwd)` | 跑 shell 命令(cwd 必须在白名单内,120s 超时) |
| `aih_search_text(query, path)` | 目录树内搜文本(深度/文件数有上限) |

## 命令(管理用)

| 命令 | 用途 |
|---|---|
| `/aih-coding-allow <绝对路径>` | 把一个根目录加进白名单,LLM 工具才能在里面读写 |
| `/aih-coding-roots` | 列出已授权的根目录 |
| `/aih-coding-revoke <绝对路径>` | 撤销某根目录的授权 |
| `/aih-skill-list` | 列克隆仓里的 skills,标注哪些已导入 AstrBot(✅) |
| `/aih-skill-import <name>` | 把克隆仓里的 skill 复制进 AstrBot `data/skills/`,使其在人格编辑器里可选 |
| `/aih-skill-show <name>` | 预览某 skill 的正文 |

## 关于 skill(重要)

**skill 的注入由 AstrBot 原生负责**,不是本插件:
- AstrBot 从 `data/skills/<name>/SKILL.md` 扫描 skill,在**人格编辑器**里列为可选项;
- 对话时由原生 `_ensure_persona_and_skills()` 把人格选中的 skill 内容拼进 system_prompt。
- 本插件只做**搬运**:`/aih-skill-import` 把你克隆仓(`D:\ai-helper\skills`)里的 skill 复制进 `data/skills/`,这样原生系统才看得到。导入后**重启 AstrBot** 刷新列表,再去人格里勾选。

## 配置(dashboard → 插件 → 齿轮)

| 卡片 | 项 | 说明 |
|---|---|---|
| 工作目录授权 | 启动时自动加白名单的根目录列表 | 每行一个绝对路径 |
| Skills 仓库 | Skills 目录路径覆盖 | 留空自动定位克隆仓 |
| 安全限制 | 单文件写入上限 (MB) | 默认 10 |
| 安全限制 | shell 命令超时 (秒) | 默认 120 |
| 安全限制 | 递归搜索最大深度 / 最大文件数 | 默认 15 / 10000 |

## 安全基线

- 所有路径工具都过白名单校验,且用双重 `resolve`(`Path.resolve` + `os.path.realpath`)跟随 symlink/junction,防"白名单内放软链指向白名单外"的越界。
- `aih_bash` 的 cwd 必须落在白名单内才执行。
