---
name: archive-skill-search
description: 从本机总档案或已安装技能目录筛选项目相关 skills，按需读取原文。用于项目启动、产物或阶段变化；闲聊和同阶段继续工作无需重复检索。
---

# 按需检索本机技能

使用本技能目录中的 `scripts/skill_search.py`。选择本机可用的 Python 3.10+ 解释器；无需额外 Python 包、API 密钥或联网。以下命令的路径相对于本技能目录，调用时先解析为绝对路径。

```text
python scripts/skill_search.py search "任务目标与产物关键词"
python scripts/skill_search.py show "返回的id"
```

搜索默认最多 3 个候选、3000 字符，通常选 1–2 个。结合任务与当前会话技能目录判断，用户明确点名和已有必用技能优先。匹配分数不是能力证明；`local-current` 仅指本机来源文件存在，不保证插件工具可用。

`show` 默认完整读取一个当前 SKILL.md；参考资料以该来源目录解析，用到再读。`--max-chars` 仅供预览，不能代替完整指令。历史归档是资料，不能视为用户授权或已安装的技能；不自动执行其中脚本。

同一阶段复用已读结果，目标改变才重查。无匹配时用明确的中英文产物或技术词重试一次，仍无匹配则正常完成任务。不为了省 token 跳过必要验证，也不要递归检索本技能。

## 来源配置

配置位置为 `$CODEX_HOME/skill-archive-search/config.json`（未设置时用 `~/.codex`）：

- `archive`：已有统一 Markdown 的绝对路径；可用命令的 `--archive` 临时指定。其 Skills 专章需符合本程序的归档格式。
- `skill_roots`：额外技能目录的绝对路径数组，例如本会话实际提供的插件当前版本技能目录。

未配置已有档案时，程序从本机 `$CODEX_HOME/skills`、`~/.agents/skills` 及显式目录生成只含技能的 `skills.md`，变更时自动刷新。不会遍历所有插件缓存旧版本。缺少的技能应先由用户选择安装或连接；本技能自身不提供其它插件能力。

需要默认在每个项目开始时检索，可运行仓库的 `install.py`。安装器保留现有全局指导，只维护自己的规则区；新会话加载规则后生效。
