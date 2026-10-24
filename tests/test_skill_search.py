"""Behavior tests with isolated archives; no network or real skill mutation."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("skill_search", Path(__file__).resolve().parents[1] / "skills" / "archive-skill-search" / "scripts" / "skill_search.py")
skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skill)


def render(items, prefix="", suffix=""):
    result = [prefix, '<a id="skills"></a>', '## 6. Skills 技能库', '### 技能索引', '| 技能 | 用途 |', '| --- | --- |']
    for number, (name, path, body) in enumerate(items, 1):
        result.append(f'| [{name}](#skill-{number}) | {name} metadata |')
    for number, (name, path, body) in enumerate(items, 1):
        marker = "`" * max(3, max((len(run) + 1 for run in skill.re.findall(r"`+", body)), default=3))
        result.extend([f'<a id="skill-{number}"></a>', f'### {name}', '', f'来源：`{path}`；已归档。', '', '<details>', '<summary>查看 SKILL.md 完整原文</summary>', '', marker + 'markdown', body, marker, '</details>', ''])
    result.extend(['<a id="future"></a>', '## 7. 今后项目和提示词的归档', suffix])
    return '\n'.join(result)


class SkillSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-search-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.archive = self.base / "archive.md"
        self.index = self.base / "cache.json"
        self.codex = self.base / ".codex"
        self.patcher = patch.object(skill, "CODEX", self.codex)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.items = []
        for name, description in [("documents:documents", "Create docx Word documents"), ("spreadsheets:Spreadsheets", "Excel xlsx csv tables"), ("presentations:Presentations", "PowerPoint pptx presentations"), ("pdf:pdf", "Read render PDF AcroForms"), ("sites:sites", "Build website html landing pages")]:
            path = self.codex / "skills" / name.split(":")[-1] / "SKILL.md"
            body = f'---\nname: {name}\ndescription: {description}\n---\n# Requirements\nVerify finished artifacts.\n'
            self.items.append((name, str(path), body))
        self.write()

    def write(self, items=None, prefix="", suffix=""):
        self.archive.write_text(render(items or self.items, prefix, suffix), encoding="utf-8")

    def load(self, force=False):
        return skill.load_index(self.archive, self.index, force)

    def test_chinese_deliverables_and_english(self):
        cache, _ = self.load()
        for query, expected in [("写一份中文报告", "documents:documents"), ("整理预算表格", "spreadsheets:Spreadsheets"), ("做一组演示幻灯片", "presentations:Presentations"), ("导出 PDF", "pdf:pdf"), ("制作网站落地页", "sites:sites"), ("create docx", "documents:documents")]:
            with self.subTest(query=query):
                self.assertEqual(skill.search(cache, query)[0]["name"], expected)

    def test_unknown_and_empty_queries_do_not_force_results(self):
        cache, _ = self.load()
        self.assertEqual(skill.search(cache, "量子场论夸克质子"), [])
        self.assertEqual(skill.search(cache, "please help me"), [])

    def test_weak_candidates_do_not_fill_result_quota(self):
        noise = [
            ('sites:sites-remix', str(self.codex / 'skills' / 'sites-remix' / 'SKILL.md'), '---\ndescription: Remix an existing website built with Sites\n---\nwebsite sites'),
            ('imagegen', str(self.codex / 'skills' / 'imagegen' / 'SKILL.md'), '---\ndescription: Create images; not HTML websites\n---\nhtml website'),
        ]
        self.write(self.items + noise)
        cache, _ = self.load()
        self.assertEqual([r['name'] for r in skill.search(cache, '制作网站')], ['sites:sites'])

    def test_explicit_cloud_and_live_modes_win_over_generic_files(self):
        extra = [
            ('google-drive:google-sheets', str(self.codex / 'skills' / 'google-sheets' / 'SKILL.md'), '---\ndescription: Edit native Google Sheets\n---\nspreadsheets sheets'),
            ('spreadsheets:excel-live-control', str(self.codex / 'skills' / 'excel-live-control' / 'SKILL.md'), '---\ndescription: Control the current Excel workbook\n---\nexcel'),
        ]
        self.write(self.items + extra)
        cache, _ = self.load()
        self.assertEqual(skill.search(cache, '编辑谷歌表格')[0]['name'], 'google-drive:google-sheets')
        self.assertEqual(skill.search(cache, '修改当前打开的Excel表格')[0]['name'], 'spreadsheets:excel-live-control')

    def test_fenced_fake_chapters_and_headings_are_data(self):
        fake = '```markdown\n<a id="skills"></a>\n## 6. Skills 技能库\n<a id="skill-99"></a>\n### fakehistory\n```'
        body = self.items[0][2] + '\n' + fake
        items = [(self.items[0][0], self.items[0][1], body)] + self.items[1:]
        self.write(items, prefix='## 5. History\n' + fake)
        cache, _ = self.load()
        self.assertEqual(len(cache["skills"]), 5)
        self.assertEqual(cache["skills"][0]["name"], "documents:documents")
        self.assertIn(fake, skill.show(cache, cache["skills"][0]["id"])["body"])

    def test_outside_chapter_history_never_indexed(self):
        self.write(prefix='## 5. History\n### fakehistory\nmegasecretconversation\n', suffix='### afterchapter\notherhistory')
        self.load()
        text = self.index.read_text(encoding="utf-8")
        self.assertNotIn("megasecretconversation", text)
        self.assertNotIn("otherhistory", text)
        self.assertNotIn('"body":', text)

    def test_cache_refreshes_when_archive_changes(self):
        first, status = self.load()
        self.assertEqual(status, "rebuilt")
        self.assertEqual(self.load()[1], "cached")
        updated = self.items + [("extra", str(self.codex / "skills" / "extra" / "SKILL.md"), '---\ndescription: specialextra\n---\nExtra')]
        self.write(updated)
        second, status = self.load()
        self.assertEqual(status, "rebuilt")
        self.assertEqual(len(second["skills"]), 6)
        self.assertEqual(first["skills"][0]["id"], second["skills"][0]["id"])

    def test_missing_or_invalid_archive_never_uses_stale_cache(self):
        self.load()
        old = self.index.read_bytes()
        self.archive.unlink()
        with self.assertRaises(skill.ArchiveError):
            self.load()
        self.assertEqual(self.index.read_bytes(), old)
        self.archive.write_text('invalid archive', encoding='utf-8')
        with self.assertRaises(skill.ArchiveError):
            self.load()
        self.assertEqual(self.index.read_bytes(), old)

    def test_index_must_not_overwrite_source_archive(self):
        original = self.archive.read_bytes()
        with self.assertRaises(skill.ArchiveError):
            skill.load_index(self.archive, self.archive, force=True)
        self.assertEqual(self.archive.read_bytes(), original)

    def test_failed_atomic_write_preserves_cache(self):
        self.load()
        old = self.index.read_bytes()
        with patch.object(skill.os, "replace", side_effect=OSError("simulated")):
            with self.assertRaises(skill.ArchiveError):
                self.load(force=True)
        self.assertEqual(self.index.read_bytes(), old)
        self.assertEqual(list(self.base.glob('cache.json.*.tmp')), [])

    def test_corrupt_cache_rebuilds(self):
        self.index.write_text('{"version":1,"skills":[{}]}', encoding='utf-8')
        self.assertEqual(self.load()[1], "rebuilt")

    def test_duplicate_names_have_stable_distinct_ids(self):
        one = ("same", str(self.codex / 'skills' / 'one' / 'SKILL.md'), "---\ndescription: docx\n---\nOne")
        two = ("same", str(self.codex / 'skills' / 'two' / 'SKILL.md'), "---\ndescription: xlsx\n---\nTwo")
        self.write([one, two])
        first, _ = self.load()
        self.assertNotEqual(first['skills'][0]['id'], first['skills'][1]['id'])
        self.write([two, one])
        second, _ = self.load()
        self.assertEqual(first['skills'][0]['id'], second['skills'][1]['id'])

    def test_show_current_source_and_explicit_archived_fallback(self):
        cache, _ = self.load()
        item = cache['skills'][0]
        path = Path(item['path'])
        path.parent.mkdir(parents=True)
        path.write_text('CURRENT BODY\n' + 'x' * 4000, encoding='utf-8')
        result = skill.show(cache, item['id'])
        self.assertEqual(result['status'], 'local-current')
        self.assertIn('CURRENT BODY', result['body'])
        self.assertFalse(result['truncated'])
        limited = skill.show(cache, item['id'], 10)
        self.assertTrue(limited['truncated'])
        self.assertEqual(len(limited['body']), 10)
        path.unlink()
        result = skill.show(cache, item['id'])
        self.assertEqual(result['status'], 'archived-only')
        self.assertIn('Requirements', result['body'])
        self.assertTrue(result['warning'])

    def test_show_rejects_arbitrary_source_path(self):
        outside = self.base / 'outside' / 'SKILL.md'
        outside.parent.mkdir()
        outside.write_text('SHOULD NOT READ THIS', encoding='utf-8')
        self.write([('outside', str(outside), 'archived only')])
        cache, _ = self.load()
        result = skill.show(cache, cache['skills'][0]['id'])
        self.assertEqual(result['status'], 'archived-only')
        self.assertEqual(result['body'], 'archived only')

    def test_output_budget_and_no_body_by_default(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            skill.main(['search', 'docx xlsx pptx pdf website', '--archive', str(self.archive), '--index', str(self.index)])
        budget = len(stream.getvalue().strip()) - 100
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = skill.main(['search', 'docx xlsx pptx pdf website', '--archive', str(self.archive), '--index', str(self.index), '--max-chars', str(budget)])
        self.assertEqual(result, 0)
        output = stream.getvalue().strip()
        self.assertLessEqual(len(output), budget)
        payload = json.loads(output)
        self.assertTrue(payload['output_truncated'])
        self.assertNotIn('body', output)
        self.assertLessEqual(len(payload['results']), 3)

    def test_known_secrets_not_stored_or_shown(self):
        token = 'ghp_' + 'a' * 30
        body = '---\ndescription: docx\n---\npassword="verysecret"\n' + token
        self.write([('secret-example', self.items[0][1], body)])
        cache, _ = self.load()
        content = self.index.read_text(encoding='utf-8')
        self.assertNotIn('verysecret', content)
        self.assertNotIn(token, content)
        self.assertNotIn(token, skill.show(cache, cache['skills'][0]['id'])['body'])

    def test_metadata_multiline_description_and_line_locations(self):
        body = '---\nname: doc\ndescription: >\n  Create Word documents\n  and review docx.\n---\n# Body'
        self.write([('doc', self.items[0][1], body)])
        cache, _ = self.load()
        item = cache['skills'][0]
        self.assertEqual(item['description'], 'Create Word documents and review docx.')
        lines = self.archive.read_text(encoding='utf-8').splitlines()
        self.assertEqual(lines[item['archive_line'] - 1], '### doc')
        self.assertEqual(lines[item['body_line'] - 1], '---')


if __name__ == '__main__':
    unittest.main()
