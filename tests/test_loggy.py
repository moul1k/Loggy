import json
import tempfile
import unittest
from pathlib import Path
from loggy.app import Store, markdown, report_html


class LoggyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'test.db')
        self.runs = self.root / 'artifacts/runs'
        self.runs.mkdir(parents=True)

    def seed(self):
        report = dict(run=dict(created_at='2026-09-20', provider='fixture'), aggregate={'precision': 1}, cases=[])
        (self.runs / 'run.json').write_text(json.dumps(report))
        return self.store.sync(self.root)

    def test_import_dedup_fixture_and_review(self):
        self.assertEqual(self.seed()['imported'], 1)
        self.assertEqual(self.store.sync(self.root)['imported'], 0)
        entry = self.store.entries()[0]
        self.assertIn('not model quality', entry['result'])
        self.assertNotIn('fixture benchmark', markdown(self.store.entries(), 'experiment'))
        entry.update(status='published', title='<script>alert(1)</script>')
        self.store.save(entry)
        self.assertIn('precision', markdown(self.store.entries(), 'experiment'))
        self.assertNotIn('<script>', report_html(self.store.entries()))
        self.assertEqual(self.store.sync(self.root)['imported'], 0)

    def test_malformed_file_does_not_block_other_runs(self):
        (self.runs / 'bad.json').write_text('{}')
        result = self.seed()
        self.assertEqual(result['imported'], 1)
        self.assertEqual(len(result['errors']), 1)

    def test_learning_requires_real_evidence(self):
        entry = dict(kind='learning', status='published', date='2026-09-20', title='Lesson', lesson='Takeaway', why='Evidence', experiments='missing')
        with self.assertRaises(ValueError):
            self.store.save(entry)
        self.seed()
        entry['experiments'] = self.store.entries()[0]['id']
        self.store.save(entry)
        self.assertEqual(len(self.store.entries()), 2)


if __name__ == '__main__':
    unittest.main()
