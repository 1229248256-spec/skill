# 项目 Skill 检索

在项目开始时，从本机档案中找出相关 skill，先返回少量候选，再按需要读取完整指令。同一阶段复用结果，减少无关上下文；保留必要验证，不承诺固定 token 节省比例。

Python 3.10+，仅标准库。支持 Windows、macOS 和 Linux。没有模型 API 调用。

## 另一台电脑直接安装

可以把这段发送给那台电脑的 Codex：

> 请从 https://github.com/1229248256-spec/skill 获取 archive-skill-search 工程，阅读 README，找到可用的 Python 3.10+，运行仓库 install.py，安装并启用项目开始时的默认技能检索。如果本机已有统一项目总档案，用 --archive 指向它；否则使用本机已安装技能生成的技能档案。保留现有规则和技能，完成后验证一次 search 和 show。

也可手动执行：

```text
git clone https://github.com/1229248256-spec/skill.git
cd skill
python install.py
```

Windows 若系统使用 `py` 启动器，将 `python` 换为 `py -3`；macOS/Linux 通常用 `python3`。如果只有 Codex 自带 Python，可让 Codex 找到其解释器路径再运行安装器。

安装器把技能放到 `$CODEX_HOME/skills/archive-skill-search`，默认 `~/.codex/skills/archive-skill-search`，并在实际生效的全局 `AGENTS.md` 或 `AGENTS.override.md` 中增加项目开始时检索的规则。新会话加载后采用此默认流程。已有同名但不同内容的技能需要显式 `--update`，安装器会先备份；内容相同可重复执行。

若只安装技能，不启用默认规则：

```text
python install.py --no-default
```

## 接入本机档案或插件技能

```text
python install.py --archive "本机总档案的绝对路径.md"
python install.py --skill-root "本机额外技能目录的绝对路径"
```

`--skill-root` 可重复传入。已有统一档案必须包含本项目规定格式的 Skills 专章；格式可参考程序自动生成的 `skills.md`。命令行也可临时指定 `search "关键词" --archive "路径"`。

未指定已有档案时，检索器从本机 `~/.codex/skills`（或 `$CODEX_HOME/skills`）、`~/.agents/skills` 与明确指定的额外目录生成最小技能档案。对插件技能，请让 Codex 用当前会话提供的实际版本路径作为额外目录；程序不扫描整份插件缓存的旧版本。

仓库提供检索能力和安装配置。另一台电脑需要有相应的技能文件和已连接插件；没有匹配时程序会明确返回空结果。原电脑的项目记录、历史对话、完整个人总档案和凭据属于本机资料，不包含在这个可移植包中。

## 使用

在安装后的技能目录运行（或把脚本路径写成绝对路径）：

```text
python scripts/skill_search.py search "制作中文演示文稿 PPT"
python scripts/skill_search.py show "上一步返回的id"
python scripts/skill_search.py rebuild
```

搜索最多 3 个候选、默认 3000 字符，通常选 1–2 个。`show` 默认返回完整单个技能；`--max-chars` 只生成预览。`local-current` 表示来源文件存在，工具权限仍以当前会话为准。

数据与配置仅存本机 `$CODEX_HOME/skill-archive-search`，检索词不保存。不执行检索到的代码，失败时不拿旧缓存冒充新结果。

## 更新与测试

```text
git pull --ff-only
python install.py --update
python -m unittest discover -s tests -v
```

通过当前用户可读写的本机目录运行，不需要管理员权限。测试使用临时目录，不修改真实技能或全局规则。

实现参考 [OpenAI Skills 按需加载](https://learn.chatgpt.com/docs/build-skills)、[全局指导文件](https://learn.chatgpt.com/docs/agent-configuration/agents-md) 与 [官方 skill-creator](https://github.com/openai/skills/tree/main/skills/.system/skill-creator)。
