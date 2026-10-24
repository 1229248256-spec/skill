"""Installer tests run only in temporary Codex homes and fixture repositories."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
INSTALLER = Path(__file__).resolve().parents[1] / "install.py"
spec = importlib.util.spec_from_file_location("archive_skill_installer", INSTALLER)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="archive-skill-install-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "本机 配置" / ".codex"
        self.repo = self.base / "repo"
        self.source = self.repo / "skills" / installer.NAME
        fixtures = {"SKILL.md": "---\nname: archive-skill-search\ndescription: Search skills\n---\n# Fixture\n",
                    "agents/openai.yaml": "interface:\n  display_name: Skill search\n",
                    "scripts/skill_search.py": "# fixture search\n",
                    "scripts/catalog.py": "# fixture catalog\n",
                    "references/notes.txt": "supporting resource 中文\n"}
        for relative, text in fixtures.items():
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.destination = self.home / "skills" / installer.NAME
        self.config = self.home / "skill-archive-search" / "config.json"
        self.agents = self.home / "AGENTS.md"

    def install(self, **kwargs):
        return installer.install(self.home, source=self.source, **kwargs)

    def test_install_copies_complete_skill_and_enables_default(self):
        result = self.install()
        self.assertEqual(result["action"], "installed")
        self.assertEqual(installer.manifest(self.destination), installer.manifest(self.source))
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), {"archive": None, "skill_roots": []})
        rules = self.agents.read_text(encoding="utf-8")
        self.assertIn(installer.BEGIN, rules)
        self.assertIn(str(self.destination / "scripts" / "skill_search.py"), rules)
        self.assertIn(sys.executable.replace("'", "''") if installer.os.name == "nt" else sys.executable, rules)

    def test_identical_install_is_idempotent(self):
        self.install()
        before = {str(path.relative_to(self.home)): (path.read_bytes(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        result = self.install()
        after = {str(path.relative_to(self.home)): (path.read_bytes(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(result["backups"], [])
        self.assertEqual(before, after)

    def test_different_skill_requires_update_and_preserves_everything(self):
        self.install()
        custom = self.destination / "my-notes.md"
        custom.write_text("personal", encoding="utf-8")
        config_before = self.config.read_bytes()
        rules_before = self.agents.read_bytes()
        with self.assertRaisesRegex(installer.InstallError, "--update"):
            self.install()
        self.assertEqual(custom.read_text(encoding="utf-8"), "personal")
        self.assertEqual(config_before, self.config.read_bytes())
        self.assertEqual(rules_before, self.agents.read_bytes())
        result = self.install(update=True)
        self.assertEqual(result["action"], "updated")
        skill_backups = [Path(path) for path in result["backups"] if Path(path).is_dir()]
        self.assertEqual(len(skill_backups), 1)
        self.assertEqual((skill_backups[0] / "my-notes.md").read_text(encoding="utf-8"), "personal")
        self.assertFalse(custom.exists())
        self.assertEqual(installer.manifest(self.destination), installer.manifest(self.source))

    def test_existing_rules_bom_and_newline_are_preserved_and_backed_up(self):
        self.home.mkdir(parents=True)
        original = installer.BOM + "# 原有规则\r\n\r\n用户手写。\r\n".encode("utf-8")
        self.agents.write_bytes(original)
        result = self.install()
        new = self.agents.read_bytes()
        self.assertTrue(new.startswith(original))
        self.assertEqual(new.count(installer.BOM), 1)
        self.assertIn(b"\r\n", new)
        backup = next(Path(path) for path in result["backups"] if Path(path).name.startswith("AGENTS.md."))
        self.assertEqual(backup.read_bytes(), original)

    def test_managed_block_replacement_keeps_surrounding_text(self):
        self.home.mkdir(parents=True)
        old = "BEFORE\n" + installer.BEGIN + "\nold rule\n" + installer.END + "\nAFTER\n"
        self.agents.write_text(old, encoding="utf-8")
        self.install()
        text = self.agents.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("BEFORE\n"))
        self.assertTrue(text.endswith("\nAFTER\n"))
        self.assertNotIn("old rule", text)
        self.assertEqual(text.count(installer.BEGIN), 1)

    def test_configuration_supports_spaces_chinese_and_preserves_existing(self):
        archive = self.base / "我的 项目档案.md"
        archive.write_text("资料", encoding="utf-8")
        root = self.base / "附加 技能"
        root.mkdir()
        self.install(archive=archive, skill_roots=[root, root])
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(config["archive"], str(archive.resolve()))
        self.assertEqual(config["skill_roots"], [str(root.resolve())])
        config["custom-setting"] = {"keep": True}
        self.config.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        raw = self.config.read_bytes()
        self.install()
        self.assertEqual(self.config.read_bytes(), raw)
        second = self.base / "extra"
        second.mkdir()
        result = self.install(skill_roots=[second])
        updated = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(updated["skill_roots"], [str(root.resolve()), str(second.resolve())])
        self.assertEqual(updated["archive"], str(archive.resolve()))
        self.assertEqual(updated["custom-setting"], {"keep": True})
        self.assertTrue(any(Path(path).name.startswith("config.json.") for path in result["backups"]))

    def test_bad_archive_has_no_side_effects(self):
        with self.assertRaises(installer.InstallError):
            self.install(archive=self.base / "missing.md")
        self.assertFalse(self.home.exists())

    def test_bad_extra_root_has_no_side_effects(self):
        with self.assertRaises(installer.InstallError):
            self.install(skill_roots=[self.base / "missing"])
        self.assertFalse(self.home.exists())

    def test_nonempty_override_is_the_effective_guidance_target(self):
        self.home.mkdir(parents=True)
        self.agents.write_text("base unchanged", encoding="utf-8")
        override = self.home / "AGENTS.override.md"
        original = installer.BOM + "# 已启用覆盖规则\n".encode("utf-8")
        override.write_bytes(original)
        result = self.install()
        self.assertEqual(result["guidance"], str(override))
        self.assertEqual(self.agents.read_text(encoding="utf-8"), "base unchanged")
        self.assertTrue(override.read_bytes().startswith(original))
        self.assertIn(installer.BEGIN, override.read_text(encoding="utf-8-sig"))

    def test_empty_override_leaves_override_and_updates_agents(self):
        self.home.mkdir(parents=True)
        override = self.home / "AGENTS.override.md"
        original = installer.BOM + b" \r\n"
        override.write_bytes(original)
        result = self.install()
        self.assertEqual(result["guidance"], str(self.agents))
        self.assertEqual(override.read_bytes(), original)

    def test_no_default_does_not_touch_guidance(self):
        self.home.mkdir(parents=True)
        self.agents.write_bytes(b"manual rules\n")
        result = self.install(enable_default=False)
        self.assertIsNone(result["guidance"])
        self.assertEqual(self.agents.read_bytes(), b"manual rules\n")
        self.assertTrue(self.destination.is_dir())

    def test_invalid_managed_block_fails_before_copying(self):
        self.home.mkdir(parents=True)
        self.agents.write_text(installer.BEGIN + "\nbroken", encoding="utf-8")
        old = self.agents.read_bytes()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.config.exists())
        self.assertEqual(self.agents.read_bytes(), old)

    def test_config_failure_rolls_back_updated_skill(self):
        self.install()
        custom = self.destination / "my-notes.md"
        custom.write_text("keep me", encoding="utf-8")
        old_config = self.config.read_bytes()
        archive = self.base / "archive.md"
        archive.write_text("fixture", encoding="utf-8")
        real_write = installer.atomic_write

        def fail_config(path, content):
            if path == self.config:
                raise OSError("simulated write failure")
            return real_write(path, content)

        with patch.object(installer, "atomic_write", side_effect=fail_config):
            with self.assertRaises(installer.InstallError):
                self.install(update=True, archive=archive)
        self.assertEqual(custom.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(self.config.read_bytes(), old_config)

    def test_platform_commands_quote_apostrophes_and_spaces(self):
        windows = installer.command("C:/O'Brien/我的 技能.py", "C:/Python Tools/python.exe", "nt")
        self.assertEqual(windows, "& 'C:/Python Tools/python.exe' 'C:/O''Brien/我的 技能.py' 'search' '任务目标与产物关键词'")
        posix = installer.command("/tmp/O'Brien/skill.py", "/tmp/Python Tools/python", "posix")
        self.assertEqual(installer.shlex.split(posix), ["/tmp/Python Tools/python", "/tmp/O'Brien/skill.py", "search", "任务目标与产物关键词"])

    def test_incomplete_source_has_no_side_effects(self):
        (self.source / "scripts" / "catalog.py").unlink()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse(self.home.exists())

    def test_cli_uses_relative_repository_source_and_explicit_home(self):
        shutil.copy2(INSTALLER, self.repo / "install.py")
        result = subprocess.run([sys.executable, str(self.repo / "install.py"), "--codex-home", str(self.home), "--no-default"], capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["skill"], str(self.destination))
        self.assertFalse(self.agents.exists())


if __name__ == "__main__":
    unittest.main()
