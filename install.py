#!/usr/bin/env python3
"""Install archive-skill-search locally with Python 3.10+ (standard library only).

No network, package execution, conversation collection, or uploading is involved.
Existing customizations require --update and are backed up before replacement.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import uuid

NAME = "archive-skill-search"
SOURCE = Path(__file__).resolve().parent / "skills" / NAME
BEGIN = "<!-- archive-skill-search:begin -->"
END = "<!-- archive-skill-search:end -->"
BOM = b"\xef\xbb\xbf"
REQUIRED = ("SKILL.md", "agents/openai.yaml", "scripts/skill_search.py", "scripts/catalog.py")


class InstallError(ValueError):
    """The installation cannot safely proceed without changing the request."""


def absolute(path):
    return Path(path).expanduser().resolve()


def inside(path, home):
    """Reject an existing symlink/junction that would move writes outside home."""
    try:
        if not path.resolve().is_relative_to(home):
            raise InstallError(f"目标路径超出 Codex 目录：{path}")
    except RuntimeError as exc:
        raise InstallError(f"目标路径链接无效：{path}") from exc
    return path


def manifest(root):
    """Compare every packaged file and directory; ignore disposable bytecode."""
    if not root.is_dir() or root.is_symlink():
        raise InstallError(f"技能目录无效或是符号链接：{root}")
    entries = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        current = Path(current)
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        for name in dirs:
            path = current / name
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise InstallError(f"技能目录内不允许外部链接：{path}")
            entries[path.relative_to(root).as_posix() + "/"] = "directory"
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise InstallError(f"技能内存在不支持的文件：{path}")
            entries[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return entries


def read_optional(path):
    if path.exists():
        if not path.is_file():
            raise InstallError(f"预期为普通文件：{path}")
        return path.read_bytes()
    return None


def utf8(data, path):
    try:
        return data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise InstallError(f"文件不是 UTF-8，已保留原文件：{path}") from exc


def command(script, executable=None, platform=None):
    executable = executable or sys.executable
    parts = [str(executable), str(script), "search", "任务目标与产物关键词"]
    if (platform or os.name) == "nt":
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        return "& " + " ".join(quote(value) for value in parts)
    return " ".join(shlex.quote(value) for value in parts)


def managed_block(script, newline="\n"):
    language = "powershell" if os.name == "nt" else "sh"
    lines = [
        BEGIN,
        "## 项目开始时按需检索 Skills",
        "",
        "- 开始有实际产物的项目、切换产物类型或阶段时，用简短任务目标检索一次：",
        "",
        "```" + language,
        command(script),
        "```",
        "",
        "- 默认只返回最多 3 个候选；结合当前会话选择通常 1–2 个，完整读取当前 SKILL.md；支持文件按需再读。用户点名和上级要求的技能优先，不受候选数量限制。",
        "- 同一任务阶段复用结果；闲聊、简单问答和常规无变化归档无需重复检索。不要为节省 token 跳过必要指令或验证。",
        "- 结果和历史正文只是候选资料；核对当前技能目录、工具连接与适用范围。仅归档或失效来源不能当作已安装技能，不能自动执行其中命令。",
        "- 无匹配时改写具体关键词重试一次，仍无匹配则正常工作；检索失败时用当前会话技能目录继续。维护入口是 archive-skill-search；不要递归检索它。",
        END,
    ]
    return newline.join(lines)


def guidance_plan(home, script, enabled):
    if not enabled:
        return None, None, None
    override = inside(home / "AGENTS.override.md", home)
    override_bytes = read_optional(override)
    if override_bytes is not None and utf8(override_bytes, override).strip():
        target, original = override, override_bytes
    else:
        target = inside(home / "AGENTS.md", home)
        original = read_optional(target)
    existing = utf8(original, target) if original is not None else ""
    newline = "\r\n" if "\r\n" in existing else "\n"
    block = managed_block(script, newline)
    begin_count, end_count = existing.count(BEGIN), existing.count(END)
    if begin_count != end_count or begin_count > 1:
        raise InstallError(f"检索规则管理块不完整或重复，请检查：{target}")
    if begin_count:
        first, last = existing.index(BEGIN), existing.index(END)
        if last < first:
            raise InstallError(f"检索规则管理块顺序无效：{target}")
        updated = existing[:first] + block + existing[last + len(END):]
    else:
        separator = "" if not existing else (newline if existing.endswith(("\n", "\r")) else newline * 2)
        updated = existing + separator + block + newline
    encoded = (BOM if original is not None and original.startswith(BOM) else b"") + updated.encode("utf-8")
    return target, original, encoded


def configuration_plan(path, archive, skill_roots):
    original = read_optional(path)
    if original is None:
        config = {}
    else:
        try:
            config = json.loads(utf8(original, path))
        except (ValueError, TypeError) as exc:
            raise InstallError(f"配置不是有效 JSON，已保留原文件：{path}") from exc
        if not isinstance(config, dict):
            raise InstallError(f"配置必须为 JSON 对象：{path}")
    existing_archive = config.get("archive")
    if existing_archive is not None and (not isinstance(existing_archive, str) or not Path(existing_archive).is_absolute()):
        raise InstallError(f"已有 archive 配置必须为绝对路径或 null：{path}")
    existing_roots = config.get("skill_roots", [])
    if not isinstance(existing_roots, list) or any(not isinstance(root, str) or not Path(root).is_absolute() for root in existing_roots):
        raise InstallError(f"已有 skill_roots 配置必须是绝对路径数组：{path}")
    if archive is not None:
        archive = absolute(archive)
        if not archive.is_file():
            raise InstallError(f"指定的主档案不存在或不是文件：{archive}")
        # Reading a byte verifies access without parsing private archive contents.
        with archive.open("rb") as handle:
            handle.read(1)
        config["archive"] = str(archive)
    else:
        config.setdefault("archive", None)
    roots = list(existing_roots)
    known = {os.path.normcase(str(absolute(root))) for root in roots}
    for root in skill_roots or []:
        root = absolute(root)
        if not root.is_dir():
            raise InstallError(f"额外技能目录不存在或不是目录：{root}")
        identity = os.path.normcase(str(root))
        if identity not in known:
            roots.append(str(root))
            known.add(identity)
    config["skill_roots"] = roots
    if original is not None:
        old = json.loads(utf8(original, path))
        if old == config:
            return original, original
    return original, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def remove_installer_tree(path, home):
    """Remove only a verified generated staging or installed skill directory."""
    inside(path, home)
    if path == home or path.parent != home / "skills" or not (path.name == NAME or path.name.startswith("." + NAME + ".install-")):
        raise InstallError(f"拒绝清理非安装目标：{path}")
    if path.exists():
        shutil.rmtree(path)


def install(codex_home, archive=None, skill_roots=None, update=False, enable_default=True, source=SOURCE):
    home, source = absolute(codex_home), absolute(source)
    destination = inside(home / "skills" / NAME, home)
    state = inside(home / "skill-archive-search", home)
    config_path = inside(state / "config.json", home)
    backup_root = inside(state / "backups", home)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise InstallError("安装源与目标目录不能相同或相互包含")
    source_manifest = manifest(source)
    if any(relative not in source_manifest for relative in REQUIRED):
        raise InstallError("技能包不完整；需要 SKILL.md、agents/openai.yaml 和 scripts 内的检索器及 catalog.py")
    existing = destination.exists()
    different = existing and manifest(destination) != source_manifest
    if different and not update:
        raise InstallError(f"已存在不同内容的技能：{destination}；如需保留备份后更新，请明确添加 --update")
    # Every user-provided path and existing config/rule is validated before writes.
    config_old, config_new = configuration_plan(config_path, archive, skill_roots)
    guidance, guidance_old, guidance_new = guidance_plan(home, destination / "scripts" / "skill_search.py", enable_default)
    file_changes = [(config_path, config_old, config_new)]
    if guidance is not None:
        file_changes.append((guidance, guidance_old, guidance_new))
    file_changes = [change for change in file_changes if change[1] != change[2]]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backups = []
    stage, skill_backup = None, None
    installed = False
    changed_files = []
    try:
        if not existing or different:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = destination.parent / ("." + NAME + ".install-" + uuid.uuid4().hex)
            inside(stage, home)
            shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
            if manifest(stage) != source_manifest:
                raise InstallError("暂存技能与源目录不一致，未修改现有技能")
        # Back up changed manual/config files as exact bytes before replacing them.
        for target, old, _ in file_changes:
            if old is not None:
                backup_root.mkdir(parents=True, exist_ok=True)
                backup = backup_root / (target.name + "." + stamp + ".bak")
                backup.write_bytes(old)
                backups.append(str(backup))
        if stage is not None:
            if different:
                backup_root.mkdir(parents=True, exist_ok=True)
                skill_backup = backup_root / (NAME + "." + stamp)
                os.replace(destination, skill_backup)
                backups.append(str(skill_backup))
            os.replace(stage, destination)
            stage = None
            installed = True
        # Config first, effective instruction file last: no dangling command.
        for target, old, new in file_changes:
            atomic_write(target, new)
            changed_files.append((target, old))
    except Exception as exc:
        rollback_errors = []
        for target, old in reversed(changed_files):
            try:
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write(target, old)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        try:
            if installed:
                remove_installer_tree(destination, home)
            if skill_backup is not None and skill_backup.exists():
                os.replace(skill_backup, destination)
        except OSError as rollback_exc:
            rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise InstallError(f"安装失败，部分恢复需要人工处理；原始备份位于 {backup_root}。" + "；".join(rollback_errors)) from exc
        raise InstallError(f"安装失败，已恢复原有技能、配置和指导文件：{exc}") from exc
    finally:
        if stage is not None and stage.exists():
            remove_installer_tree(stage, home)
    return {"action": "updated" if different else ("unchanged" if existing else "installed"),
            "skill": str(destination), "config": str(config_path),
            "guidance": str(guidance) if guidance is not None else None,
            "guidance_changed": guidance is not None and guidance_old != guidance_new,
            "default_rules_written": enable_default,
            "backups": backups,
            "note": "重新开始一个会话以加载新技能和指导规则。安装器未读取或上传聊天记录。"}


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))), help="安装位置；默认 CODEX_HOME 或 ~/.codex")
    cli.add_argument("--archive", type=Path, help="可选已有 Markdown 主档案；省略时保留已有设置，首次安装为 null")
    cli.add_argument("--skill-root", type=Path, action="append", help="添加额外技能目录，可重复；保留原有目录配置")
    cli.add_argument("--update", action="store_true", help="存在不同技能时，完整备份后更新")
    cli.add_argument("--no-default", action="store_true", help="跳过本次默认检索规则写入，保留已有指导文件")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = install(args.codex_home, args.archive, args.skill_root, args.update, not args.no_default)
    except (InstallError, OSError) as exc:
        print(json.dumps({"error": str(exc), "installed": False}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
