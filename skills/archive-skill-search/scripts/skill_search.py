"""Offline, bounded skill discovery from the archive's dedicated Skills chapter.

Search never loads skill bodies into the model or runs skill code. `show ID` reads
one current local SKILL.md (if allowed), otherwise the explicitly historical copy.
The cache contains sanitized metadata and word sets, never conversation history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

_package = Path(__file__).resolve().parents[1]
_installed_home = _package.parent.parent
_default_home = (_installed_home if _package.parent.name == "skills" and
                 (_installed_home / "skill-archive-search" / "config.json").is_file()
                 else Path.home() / ".codex")
CODEX = Path(os.environ.get("CODEX_HOME", str(_default_home)))
STATE = CODEX / "skill-archive-search"
ARCHIVE = STATE / "skills.md"
INDEX = STATE / "index.json"
CONFIGURED_ROOTS = []
VERSION = 1
STOP = set("a an and are as at be been but by can could create do edit for from has have help how i if in into is it its make me my of on or our please read task that the their them then these they this to use user using want we what when where which will with work would you your skill skills file files".split())
# Keep these explicit, inspectable and local: task aliases, retrieval words, names.
# They are discovery hints, not an instruction to apply every matching skill.
ALIASES = [
    (r"文档|报告|论文|公文|合同|简历|排版|\b(?:docx|word)\b", "docx word documents", {"documents"}),
    (r"表格|电子表|预算表|数据表|\b(?:xlsx|xls|csv|excel|spreadsheet)\b", "xlsx excel spreadsheets", {"spreadsheets"}),
    (r"幻灯|演示|路演|讲稿|汇报|\b(?:pptx?|powerpoint|presentation|slides)\b", "pptx powerpoint presentations", {"presentations"}),
    (r"\bpdf\b|便携式文档", "pdf acroforms", {"pdf"}),
    (r"网站|网页|落地页|前端|页面|建站|\b(?:website|landing|html)\b", "website sites html", {"sites"}),
    (r"生成图片|绘图|画图|插画|透明背景|图像生成|\bimagegen\b", "imagegen image illustration", {"imagegen"}),
    (r"可视化|交互图|模拟器|\bvisualization\b", "visualize interactive simulation", {"visualize"}),
    (r"创建技能|新建技能|编写技能|修改技能|制作技能|\bskill.creator\b", "skill creator", {"skill-creator"}),
    (r"安装技能|下载技能|\bskill.installer\b", "skill installer github", {"skill-installer"}),
    (r"模板|\btemplate\b", "template creator reusable", {"template-creator"}),
    (r"命令行|终端|脚本调用|\b(?:powershell|pwsh)\b", "powershell invocation", {"powershell-safe-invocation"}),
    (r"插件|连接器|\bplugin\b", "plugin management", {"plugin-management"}),
    (r"\b(?:codex|openai|chatgpt)\b", "openai codex", {"openai-docs"}),
    (r"代码审查|审查代码|\bcode.review\b", "review code", {"review-agent"}),
    (r"谷歌文档|google docs", "google docs", {"google-docs"}),
    (r"谷歌表格|google sheets", "google sheets", {"google-sheets"}),
    (r"现有谷歌幻灯|google slides template", "google slides", {"google-slides"}),
    (r"改编网站|复刻网站|\b(?:sites-remix|remix)\b", "sites-remix remix", {"sites-remix"}),
    (r"(?:当前|已打开|打开的).*excel|excel.*(?:当前|已打开)|\blive excel\b", "excel-live-control", {"excel-live-control"}),
]


class ArchiveError(ValueError):
    """Invalid or unavailable source: never silently substitute stale cache."""


def read_settings():
    path = STATE / "config.json"
    if not path.exists():
        return {"archive": None, "skill_roots": []}
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        archive = config.get("archive")
        roots = config.get("skill_roots", [])
        if not isinstance(roots, list) or any(not isinstance(p, str) or not Path(p).is_absolute() for p in roots):
            raise ValueError("skill_roots must contain absolute paths")
        if archive is not None and (not isinstance(archive, str) or not Path(archive).is_absolute()):
            raise ValueError("archive must be an absolute path")
        return {"archive": archive, "skill_roots": roots}
    except (OSError, ValueError, AttributeError) as exc:
        raise ArchiveError("本机检索配置无效，请重新运行安装器或检查 config.json") from exc


def sanitize(text):
    """Defense in depth; archive generation already removes known credentials."""
    text = str(text)
    text = re.sub(r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----", "[REDACTED]", text, flags=re.S)
    text = re.sub(r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|0x[a-fA-F0-9]{64})\b", "[REDACTED]", text)
    text = re.sub(r'''(?im)((?:["']?)(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|private[_ -]?key|password|密码|私钥|密钥)["']?\s*(?:[=:：]|就是|是|为)\s*)(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;{}，。；]+)''', r"\1[REDACTED]", text)
    return text.replace("\x00", "")


def words(text):
    text = sanitize(text).casefold()
    result = {x for x in re.findall(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", text) if len(x) > 1 and x not in STOP and x != "redacted"}
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(run[n:n + 2] for n in range(len(run) - 1))
    return result


def fingerprint(path):
    try:
        stat = path.stat()
        if not path.is_file():
            raise ArchiveError("档案不是普通文件")
        return {"path": str(path.resolve()), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
    except OSError as exc:
        raise ArchiveError("档案不可读取；保留旧索引，但本次不使用旧索引") from exc


def outside_lines(lines):
    """Yield Markdown outside fences, supporting long backticks and tildes."""
    fence = None
    for number, line in enumerate(lines, 1):
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*", line):
                fence = None
            continue
        match = re.match(r" {0,3}(`{3,}|~{3,})([^\r\n]*)$", line)
        if match:
            fence = (match[1][0], len(match[1]))
        else:
            yield number, line
    if fence:
        raise ArchiveError("档案存在未闭合代码围栏")


def front_description(body):
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        return ""
    for i, line in enumerate(lines[1:end], 1):
        if line.startswith("description:"):
            value = line.partition(":")[2].strip()
            if value in {"|", ">", "|-", ">-"}:
                result = []
                for following in lines[i + 1:end]:
                    if following and not following[0].isspace():
                        break
                    result.append(following.strip())
                value = " ".join(result)
            return value.strip("\"'")
    return ""


def stable_id(name, path):
    # Normalize separators so slash changes don't create another identity.
    key = name.casefold() + "\n" + path.replace("\\", "/").casefold()
    return "skill-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_archive(text):
    lines = text.splitlines()
    visible = list(outside_lines(lines))
    starts = [n for n, line in visible if line == '<a id="skills"></a>']
    if len(starts) != 1:
        raise ArchiveError("需要唯一的顶层 Skills 专章锚点")
    start = starts[0]
    headings = [(n, line) for n, line in visible if n > start and line.startswith("## ")]
    if not headings or not re.fullmatch(r"## \d+\. Skills 技能库", headings[0][1]):
        raise ArchiveError("Skills 专章标题无效")
    chapter_start = headings[0][0]
    if len(headings) < 2:
        raise ArchiveError("Skills 专章缺少结束章节")
    chapter_end = headings[1][0]
    chapter_visible = [(n, line) for n, line in visible if chapter_start < n < chapter_end]
    descriptions = {}
    for _, line in chapter_visible:
        match = re.fullmatch(r"\| \[(.*?)\]\(#(skill-\d+)\) \| (.*) \|", line)
        if match:
            descriptions[match[2]] = match[3].replace("\\|", "|")
    anchors = [(n, re.fullmatch(r'<a id="(skill-\d+)"></a>', line)[1]) for n, line in chapter_visible if re.fullmatch(r'<a id="(skill-\d+)"></a>', line)]
    if not anchors or len(anchors) != len(descriptions):
        raise ArchiveError("Skills 索引与正文数量不一致或没有技能")
    result = []
    for index, (line_number, anchor) in enumerate(anchors):
        end = anchors[index + 1][0] if index + 1 < len(anchors) else chapter_end
        block = lines[line_number:end - 1]
        if not block or not block[0].startswith("### "):
            raise ArchiveError("技能标题缺失")
        name = sanitize(block[0][4:].strip())
        source = next((re.fullmatch(r"来源：`([^`]+)`；(.*)", line) for line in block if line.startswith("来源：`")), None)
        if source is None:
            raise ArchiveError("技能来源路径缺失")
        opening = next(((i, re.fullmatch(r"(`{3,}|~{3,})markdown\s*", line)) for i, line in enumerate(block) if re.fullmatch(r"(`{3,}|~{3,})markdown\s*", line)), None)
        if opening is None:
            raise ArchiveError("技能原文围栏缺失")
        offset, fence = opening
        marker = fence[1]
        closing = next((i for i in range(offset + 1, len(block)) if re.fullmatch(re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", block[i])), None)
        if closing is None:
            raise ArchiveError("技能原文围栏未闭合")
        body = sanitize("\n".join(block[offset + 1:closing]))
        if not body.strip():
            raise ArchiveError("技能原文为空")
        path = sanitize(source[1])
        description = sanitize(front_description(body) or descriptions.get(anchor, ""))
        result.append({"id": stable_id(name, path), "name": name, "description": description,
                       "path": path, "archive_line": line_number + 1,
                       "body_line": line_number + offset + 2, "body_end_line": line_number + closing,
                       "archive_source_status": sanitize(source[2]),
                       "name_words": sorted(words(name)), "meta_words": sorted(words(name + " " + description)),
                       "body_words": sorted(words(body)), "body": body})
    if len({row["id"] for row in result}) != len(result):
        raise ArchiveError("重复的技能名称和来源路径，无法唯一选择")
    return result


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def validate_index(cache):
    required = {"id", "name", "description", "path", "archive_line", "body_line", "body_end_line", "name_words", "meta_words", "body_words"}
    if not isinstance(cache, dict) or cache.get("version") != VERSION or not isinstance(cache.get("skills"), list) or not cache["skills"]:
        return False
    for item in cache["skills"]:
        if not isinstance(item, dict) or not required <= item.keys():
            return False
        if any(not isinstance(item[key], str) for key in ("id", "name", "description", "path")):
            return False
        if item["id"] != stable_id(item["name"], item["path"]):
            return False
        if any(not isinstance(item[key], int) or item[key] < 1 for key in ("archive_line", "body_line", "body_end_line")):
            return False
        if any(not isinstance(item[key], list) or not all(isinstance(word, str) for word in item[key]) for key in ("name_words", "meta_words", "body_words")):
            return False
    return len({item["id"] for item in cache["skills"]}) == len(cache["skills"])


def load_index(archive=ARCHIVE, index=INDEX, force=False):
    archive, index = Path(archive), Path(index)
    if archive.resolve() == index.resolve() or (index.exists() and archive.exists() and index.samefile(archive)):
        raise ArchiveError("索引路径不能与原档案相同")
    before = fingerprint(archive)  # Always verify source exists, even on a cache hit.
    if not force:
        try:
            cache = json.loads(index.read_text(encoding="utf-8"))
            if validate_index(cache) and cache.get("archive") == before:
                return cache, "cached"
        except (OSError, ValueError, TypeError):
            pass
    try:
        parsed = parse_archive(archive.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        raise ArchiveError("档案读取失败；旧索引保留，本次不返回旧结果") from exc
    if fingerprint(archive) != before:
        raise ArchiveError("读取期间档案发生变化，请重新执行检索")
    cache = {"version": VERSION, "archive": before, "skills": [{key: value for key, value in row.items() if key != "body"} for row in parsed]}
    try:
        atomic_json(index, cache)
    except OSError as exc:
        raise ArchiveError("新索引写入失败；旧索引保留，本次未使用旧结果") from exc
    return cache, "rebuilt"


def current_source(path):
    """Read only local SKILL.md under approved Codex directories, no UNC paths."""
    if path.startswith(("\\\\", "//")) or "\x00" in path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.name.casefold() != "skill.md":
        return None
    allowed = [CODEX / "skills", CODEX / "plugins" / "cache", Path.home() / ".agents" / "skills", *CONFIGURED_ROOTS]
    # First lexical check prevents probing arbitrary/remote paths during resolve.
    if not any(candidate.is_relative_to(root) for root in allowed):
        return None
    try:
        resolved = candidate.resolve(strict=True)
        if any(resolved.is_relative_to(root.resolve()) for root in allowed) and resolved.is_file():
            return resolved
    except (OSError, RuntimeError):
        pass
    return None


def search(cache, query, limit=3):
    terms = words(query)
    preferred = set()
    expanded = set()
    for pattern, extra, names in ALIASES:
        if re.search(pattern, query, re.I):
            expanded.update(words(extra))
            preferred.update(names)
    # Specific requested services/modes take precedence over generic file types.
    if "google-docs" in preferred:
        preferred.discard("documents")
    if "google-sheets" in preferred or "excel-live-control" in preferred:
        preferred.discard("spreadsheets")
    if "google-slides" in preferred:
        preferred.discard("presentations")
    if "sites-remix" in preferred:
        preferred.discard("sites")
    terms |= expanded
    if not terms:
        return []
    candidates = []
    for item in cache["skills"]:
        meta = terms & set(item["meta_words"])
        body = terms & set(item["body_words"])
        named = terms & set(item["name_words"])
        preferred_name = item["name"].casefold().split(":")[-1] in preferred
        # A solitary incidental body word is too weak to recommend a skill.
        if not (meta or preferred_name or len(body) >= 2):
            continue
        score = 30 * preferred_name + 8 * len(named) + 4 * len(meta) + min(3, len(body))
        mode = item["name"].casefold().split(":")[-1]
        if mode in {"excel-live-control", "sites-remix", "google-slides"} and not (preferred_name or mode in words(query)):
            score *= 0.25  # These modes require an explicit live/template/remix context.
        matched = sorted(named | meta or body)
        reason = ("本地任务词映射；" if preferred_name else "") + "关键词：" + ", ".join(matched[:8])
        row = {key: item[key] for key in ("id", "name", "description", "path", "archive_line")}
        row.update(status="local-current" if current_source(item["path"]) else "archived-only", reason=reason, score=score)
        row["description"] = row["description"][:450]
        row["metadata_truncated"] = len(item["description"]) > 450
        candidates.append(row)
    ranked = sorted(candidates, key=lambda row: (-row["score"], row["name"], row["id"]))
    if not ranked:
        return []
    # Three is a maximum, not a quota. Drop incidental matches far below the
    # leading candidate (often a word in another skill's exclusion clause).
    threshold = max(8, ranked[0]["score"] * 0.45)
    return [row for row in ranked if row["score"] >= threshold][:limit]


def show(cache, identity, max_chars=None):
    item = next((row for row in cache["skills"] if row["id"] == identity), None)
    if item is None:
        raise ArchiveError("找不到该技能 ID；请重新 search 后选择当前结果")
    archive_path = Path(cache["archive"]["path"])
    if fingerprint(archive_path) != cache["archive"]:
        raise ArchiveError("档案已变化，请重新检索")
    source = current_source(item["path"])
    warning = None
    if source:
        try:
            body = sanitize(source.read_text(encoding="utf-8-sig"))
            status = "local-current"
            actual_path = str(source)
            actual_line = 1
        except (OSError, UnicodeError):
            source = None
            warning = "当前来源读取失败；显示历史归档，不能视为当前技能"
    if source is None:
        try:
            rows = parse_archive(archive_path.read_text(encoding="utf-8-sig"))
            archived = next(row for row in rows if row["id"] == identity)
        except (OSError, UnicodeError, StopIteration) as exc:
            raise ArchiveError("无法读取该技能的历史原文") from exc
        if fingerprint(archive_path) != cache["archive"]:
            raise ArchiveError("读取期间档案发生变化，请重新检索")
        body = archived["body"]
        status, actual_path, actual_line = "archived-only", str(archive_path), archived["body_line"]
        warning = warning or "当前本机来源不可用或不在允许目录内；这是历史归档，不能自动当作可用技能"
    full_length = len(body)
    truncated = max_chars is not None and full_length > max_chars
    if truncated:
        body = body[:max_chars]
    return {"id": identity, "name": item["name"], "status": status, "path": actual_path, "line": actual_line,
            "archive_path": str(archive_path), "archive_line": item["archive_line"],
            "warning": warning, "truncated": truncated, "full_chars": full_length, "body": body}


def encode(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def bounded_search_output(payload, max_chars):
    """Always emit valid JSON within budget; state omitted rows explicitly."""
    payload["output_truncated"] = False
    while len(encode(payload)) > max_chars and payload["results"]:
        payload["output_truncated"] = True
        last = payload["results"][-1]
        if len(last["description"]) > 80:
            last["description"] = last["description"][:80]
            last["metadata_truncated"] = True
        else:
            payload["results"].pop()
    if len(encode(payload)) > max_chars:
        payload = {"error": "输出预算不足，请增大 --max-chars", "output_truncated": True}
    return encode(payload)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--archive", type=Path, help="已有总档案；默认使用安装配置或本机技能目录生成的档案")
    common.add_argument("--index", type=Path, default=INDEX)
    commands = root.add_subparsers(dest="command", required=True)
    search_parser = commands.add_parser("search", parents=[common])
    search_parser.add_argument("query", help="简短任务关键词；不传递密码或整份聊天记录")
    search_parser.add_argument("--limit", type=int, default=3)
    search_parser.add_argument("--max-chars", type=int, default=3000)
    show_parser = commands.add_parser("show", parents=[common])
    show_parser.add_argument("id")
    show_parser.add_argument("--max-chars", type=int, help="可选正文截断长度；默认读取完整原文")
    commands.add_parser("rebuild", parents=[common])
    return root


def main(argv=None):
    global CONFIGURED_ROOTS
    args = parser().parse_args(argv)
    if args.command == "search" and (not 1 <= args.limit <= 10 or args.max_chars < 256):
        parser().error("--limit 必须为 1 到 10；search --max-chars 至少 256")
    if args.command == "show" and args.max_chars is not None and args.max_chars < 1:
        parser().error("show --max-chars 必须大于 0")
    try:
        settings = read_settings()
        CONFIGURED_ROOTS = [Path(p) for p in settings["skill_roots"]]
        explicit_archive = args.archive is not None or settings["archive"] is not None
        args.archive = args.archive or (Path(settings["archive"]) if settings["archive"] else ARCHIVE)
        if not explicit_archive:
            from catalog import refresh_catalog
            roots = [CODEX / "skills", Path.home() / ".agents" / "skills", *CONFIGURED_ROOTS]
            refresh_catalog(args.archive, roots)
        cache, cache_status = load_index(args.archive, args.index, force=args.command == "rebuild")
        if args.command == "search":
            result = search(cache, args.query, args.limit)
            payload = {"search_basis": "archive", "archive": str(args.archive.resolve()), "cache": cache_status,
                       "candidate_count": len(result), "results": result,
                       "note": "候选仅用于发现；local-current 仅表示本机来源文件存在，工具可用性仍须核对当前会话。采用前读取完整当前 SKILL.md。" if result else "没有足够匹配的技能；可换用具体交付物或技术关键词。"}
            print(bounded_search_output(payload, args.max_chars))
        elif args.command == "show":
            print(encode(show(cache, args.id, args.max_chars)))
        else:
            print(encode({"cache": cache_status, "skills": len(cache["skills"]), "index": str(args.index.resolve()), "archive": str(args.archive.resolve())}))
    except (ValueError, OSError) as exc:
        print(encode({"error": str(exc), "results": [], "stale_cache_used": False}))
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
