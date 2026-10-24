"""Portable catalog integration tests; all fixtures live in temporary folders."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "archive-skill-search" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import catalog
import skill_search


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill-catalog-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "skills"
        self.root.mkdir()
        self.target = self.base / "state" / "skills.md"

    def make_skill(self, folder="one", name="one", description="Make Word documents", extra=""):
        path = self.root / folder / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n# Instructions\n{extra}\n", encoding="utf-8")
        return path

    def refresh(self, roots=None):
        return catalog.refresh_catalog(self.target, roots if roots is not None else [self.root])

    def parsed(self):
        return skill_search.parse_archive(self.target.read_text(encoding="utf-8"))

    def test_add_delete_and_modify_refresh(self):
        first = self.make_skill()
        self.assertEqual(self.refresh(), {"skills": 1, "changed": True, "errors": []})
        self.assertFalse(self.refresh()["changed"])
        second = self.make_skill("two", "two", "Excel spreadsheets")
        self.assertEqual(self.refresh()["skills"], 2)
        first.unlink()
        self.assertEqual(self.refresh()["skills"], 1)
        second.write_text(second.read_text(encoding="utf-8") + "\nNew required verification.\n", encoding="utf-8")
        self.assertTrue(self.refresh()["changed"])
        self.assertIn("New required verification.", self.parsed()[0]["body"])

    def test_cache_does_not_read_skill_or_catalog_bodies(self):
        self.make_skill()
        self.refresh()
        original = Path.read_text

        def guarded(path, *args, **kwargs):
            if path.name.casefold() == "skill.md" or path == self.target:
                raise AssertionError("unchanged source bodies must not be reread")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded):
            self.assertFalse(self.refresh()["changed"])

    def test_same_name_different_paths_and_overlapping_roots(self):
        self.make_skill("first", "same")
        self.make_skill("second", "same")
        result = self.refresh([self.root, self.root / "first", self.root / "."])
        self.assertEqual(result["skills"], 2)
        parsed = self.parsed()
        self.assertEqual([item["name"] for item in parsed], ["same", "same"])
        self.assertEqual(len({item["id"] for item in parsed}), 2)
        self.assertTrue(all(Path(item["path"]).is_absolute() for item in parsed))

    def test_nested_fences_and_fake_chapter_stay_inside_body(self):
        body = '````markdown\n<a id="skills"></a>\n## 9. Skills 技能库\n```\n<a id="skill-9"></a>\n````\n'
        self.make_skill(extra=body)
        self.refresh()
        parsed = self.parsed()
        self.assertEqual(len(parsed), 1)
        self.assertIn(body.strip(), parsed[0]["body"])
        self.assertIn("`````markdown", self.target.read_text(encoding="utf-8"))

    def test_frontmatter_quotes_and_multiline(self):
        self.make_skill("quoted", "'quoted-name'", '"Make \\"careful\\" documents"')
        self.make_skill("folded", "folded", ">-\n  Budget tables\n  with formulas")
        self.make_skill("literal", "literal", "|\n  Line one\n  Line two")
        self.refresh()
        parsed = {item["name"]: item for item in self.parsed()}
        self.assertEqual(parsed["folded"]["description"], "Budget tables with formulas")
        self.assertEqual(parsed["literal"]["description"], "Line one Line two")
        self.assertIn("quoted-name", parsed)
        self.assertIn('Make "careful" documents', self.target.read_text(encoding="utf-8"))

    def test_missing_roots_and_excluded_directories(self):
        self.make_skill()
        self.make_skill("node_modules/hidden", "dependency")
        self.make_skill(".git/hidden", "git-data")
        self.make_skill("secrets/hidden", "secret-data")
        self.make_skill("credentials/hidden", "credential-data")
        result = self.refresh([self.root, self.base / "missing"])
        self.assertEqual(result["skills"], 1)

    def test_symlink_files_and_directories_are_skipped(self):
        self.make_skill()
        outside = self.base / "outside"
        outside.mkdir()
        external = outside / "SKILL.md"
        external.write_text("---\nname: outside\n---\nDo not follow links", encoding="utf-8")
        file_parent = self.root / "linked-file"
        file_parent.mkdir()
        directory_link = self.root / "linked-dir"
        file_link = file_parent / "SKILL.md"
        try:
            directory_link.symlink_to(outside, target_is_directory=True)
            file_link.symlink_to(external)
        except OSError:
            # Windows may require privileges to create links; emulate their lstat.
            if not directory_link.exists():
                directory_link.mkdir()
            if not file_link.exists():
                file_link.write_text(external.read_text(encoding="utf-8"), encoding="utf-8")
            original = Path.lstat

            def linked_stat(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path in {directory_link, file_link}:
                    fields = list(value)
                    fields[0] = catalog.stat.S_IFLNK | 0o777
                    return catalog.os.stat_result(fields)
                return value

            with patch.object(Path, "lstat", linked_stat):
                self.assertEqual(self.refresh()["skills"], 1)
        else:
            self.assertEqual(self.refresh([self.root, directory_link])["skills"], 1)

    def test_read_failure_preserves_existing_catalog(self):
        path = self.make_skill()
        self.refresh()
        before = self.target.read_bytes()
        path.write_bytes(b"\xff\xfe invalid UTF-8")
        with self.assertRaises(ValueError):
            self.refresh()
        self.assertEqual(self.target.read_bytes(), before)

    def test_scan_failure_preserves_existing_catalog(self):
        self.make_skill()
        self.refresh()
        before = self.target.read_bytes()
        with patch.object(catalog.os, "walk", side_effect=PermissionError("denied")):
            with self.assertRaises(ValueError):
                self.refresh()
        self.assertEqual(self.target.read_bytes(), before)

    def test_atomic_replace_failure_preserves_old_and_recovers(self):
        self.make_skill()
        self.refresh()
        before = self.target.read_bytes()
        self.make_skill("second", "second")
        original = catalog.os.replace

        def refuse_catalog(source, destination):
            if Path(destination) == self.target:
                raise PermissionError("catalog locked")
            return original(source, destination)

        with patch.object(catalog.os, "replace", refuse_catalog):
            with self.assertRaises(ValueError):
                self.refresh()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.target.parent.glob("*.tmp")), [])
        self.assertEqual(self.refresh()["skills"], 2)

    def test_zero_skills_preserves_existing_catalog(self):
        path = self.make_skill()
        self.refresh()
        before = self.target.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            self.refresh([self.root, self.base / "missing"])
        self.assertEqual(self.target.read_bytes(), before)

    def test_sanitizes_body_and_metadata_and_ignores_other_files(self):
        token = "ghp_" + "x" * 28
        self.make_skill(description="API key=" + token, extra='password="short"\n' + token)
        private = self.root / "one" / "private.txt"
        private.write_text("NON_SKILL_SENTINEL", encoding="utf-8")
        self.refresh()
        text = self.target.read_text(encoding="utf-8")
        self.assertNotIn(token, text)
        self.assertNotIn('"short"', text)
        self.assertNotIn("NON_SKILL_SENTINEL", text)
        self.assertIn("[REDACTED]", text)
        self.assertEqual(len(self.parsed()), 1)

    def test_catalog_cannot_overwrite_skill_source(self):
        path = self.make_skill()
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            catalog.refresh_catalog(path, [self.root])
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
