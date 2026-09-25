import copy
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from loggy.app import make_server
from loggy.importers import compare, normalize
from loggy.reports import report_html
from loggy.store import Conflict, Store

SAMPLE = Path(__file__).parents[1] / 'loggy/samples/01-baseline.json'


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'test.db')

    def import_one(self, project='default'):
        self.store.import_run(SAMPLE.read_bytes(), SAMPLE.name, project)
        return self.store.entries(project)[0]

    def test_project_isolation_and_per_project_dedup(self):
        first = self.import_one()
        project = self.store.save_project({'name': 'Second project'})
        second = self.import_one(project['id'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.store.import_run(SAMPLE.read_bytes(), 'renamed.json'), 0)
        with self.assertRaises(ValueError):
            self.store.entry(first['id'], project['id'])
        with self.assertRaises(ValueError):
            self.store.save(first, project=project['id'])

    def test_edit_conflict_history_and_immutable_measurements(self):
        entry = self.import_one()
        original = copy.deepcopy(entry)
        entry.update(title='Edited', metrics={'recall': {'value': 99, 'direction': 'higher'}}, evidence='forged')
        self.store.save(entry)
        saved = self.store.entries()[0]
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(saved['metrics'], original['metrics'])
        self.assertEqual(saved['evidence'], original['evidence'])
        self.assertEqual(self.store.history(saved['id'])[0]['entry']['title'], original['title'])
        with self.assertRaises(Conflict):
            self.store.save(original)

    def test_trash_restore_and_dedup(self):
        entry = self.import_one()
        self.store.trash(entry['id'], 1, True)
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(len(self.store.entries(deleted=True)), 1)
        self.assertEqual(self.store.import_run(SAMPLE.read_bytes(), 'again.json'), 0)
        self.store.trash(entry['id'], 2, False)
        self.assertEqual(self.store.entries()[0]['revision'], 3)

    def test_learning_evidence_publication_and_trash_guards(self):
        project = self.store.sample()
        entries = self.store.entries(project['id'])
        experiment = next(e for e in entries if e['kind'] == 'experiment')
        learning = next(e for e in entries if e['kind'] == 'learning')
        with self.assertRaises(ValueError):
            self.store.trash(experiment['id'], experiment['revision'], True, project['id'])
        experiment['status'] = 'draft'
        with self.assertRaises(ValueError):
            self.store.save(experiment, project=project['id'])
        self.store.trash(learning['id'], learning['revision'], True, project['id'])
        self.store.trash(experiment['id'], experiment['revision'], True, project['id'])
        with self.assertRaises(ValueError):
            self.store.trash(learning['id'], learning['revision'] + 1, False, project['id'])

    def test_backup_roundtrip_keeps_links_history_and_source(self):
        project = self.store.sample()
        backup = self.store.backup(project['id'])
        result = self.store.restore(backup)
        restored = self.store.entries(result['project'])
        self.assertEqual(len(restored), 3)
        experiments = {e['id'] for e in restored if e['kind'] == 'experiment'}
        learning = next(e for e in restored if e['kind'] == 'learning')
        self.assertEqual(set(learning['experiments'].split()), experiments)
        self.assertEqual(self.store.project(result['project'])['folder'], '')
        self.assertEqual(self.store.import_run(SAMPLE.read_bytes(), 'same.json', result['project']), 0)
        for entry in restored:
            if entry['kind'] == 'experiment':
                self.assertEqual(len(self.store.history(entry['id'], result['project'])), 1)

    def test_failed_restore_rolls_back_all_writes(self):
        project = self.store.sample()
        backup = self.store.backup(project['id'])
        backup['history'][0]['revision'] = 999
        before = self.store.backup()
        with self.assertRaises(ValueError):
            self.store.restore(backup)
        after = self.store.backup()
        for key in ('projects', 'entries', 'history'):
            self.assertEqual(before[key], after[key])

    def test_migration_preserves_legacy_and_creates_backup(self):
        legacy_path = self.root / 'legacy.db'
        entry, source = normalize(SAMPLE.read_bytes())
        entry['id'] = 'old-id'
        db = sqlite3.connect(legacy_path)
        db.execute('CREATE TABLE entries (id TEXT PRIMARY KEY, source TEXT UNIQUE, body TEXT NOT NULL)')
        db.execute('INSERT INTO entries VALUES (?, ?, ?)', ('old-id', source, json.dumps(entry)))
        db.commit(); db.close()
        migrated = Store(legacy_path)
        self.assertEqual(migrated.entries()[0]['id'], 'old-id')
        self.assertEqual(migrated.project()['name'], '99p_transcription_agent')
        self.assertTrue(legacy_path.with_suffix('.pre-v01.db').exists())
        self.assertEqual(Store(legacy_path).entries(), migrated.entries())

    def test_report_project_name_and_content_are_escaped(self):
        entry = self.import_one()
        entry.update(status='published', title='<script>unsafe()</script>')
        self.store.save(entry)
        report = report_html(self.store.entries(), '<img src=x>')
        self.assertNotIn('<script>', report)
        self.assertNotIn('<img src=x>', report)
        self.assertIn('&lt;script&gt;', report)


class ImportTests(unittest.TestCase):
    def test_reject_malformed_and_nonfinite(self):
        for value in [True, '0.8', float('nan'), float('inf')]:
            data = json.loads(SAMPLE.read_bytes())
            data['metrics']['recall']['value'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(json.dumps(data).encode())
        for raw in [b'[]', b'{}', b'not-json', b'{"run":{},"aggregate":[],"cases":[]}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize(raw)

    def test_compare_directions_missing_values_and_metadata(self):
        left, _ = normalize(SAMPLE.read_bytes())
        right, _ = normalize(SAMPLE.with_name('02-candidate.json').read_bytes())
        left['id'], right['id'] = 'a', 'b'
        result = compare(left, right)
        signals = {r['name']: r['trend'] for r in result['rows']}
        self.assertEqual(signals, {'recall':'improved', 'precision':'regressed', 'latency_ms':'regressed'})
        del right['metrics']['precision']
        right['metrics']['recall']['direction'] = 'unknown'
        right['metadata']['dataset'] = 'different'
        result = compare(left, right)
        signals = {r['name']: r['trend'] for r in result['rows']}
        self.assertEqual(signals['recall'], 'unclassified')
        self.assertEqual(signals['precision'], 'not comparable')
        self.assertTrue(any('dataset changed' in w for w in result['warnings']))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / 'http.db')
        cls.server = make_server(cls.store, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        cls.tmp.cleanup()

    def request(self, method, path, data=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        body = json.dumps(data) if data is not None else None
        client.request(method, path, body, headers or ({'Content-Type':'application/json'} if body else {}))
        response = client.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        client.close()
        return result

    def test_assets_and_download_headers(self):
        for path in ('/', '/app.js', '/style.css', '/report', '/download/backup.json', '/download/example.json'):
            with self.subTest(path=path):
                status, headers, body = self.request('GET', path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                if path.startswith('/download/'):
                    self.assertIn('attachment', headers['Content-Disposition'])

    def test_reject_cross_origin_and_dns_rebinding(self):
        self.assertEqual(self.request('GET', '/', headers={'Host':'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/sample', {}, {'Content-Type':'application/json','Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/sample', {}, {'Content-Type':'text/plain'})[0], 403)
        self.assertEqual(self.request('POST', '/api/sample', [1, 2])[0], 400)

    def test_complete_project_import_edit_export_restore_flow(self):
        status, _, body = self.request('POST', '/api/projects', {'name':'HTTP project'})
        self.assertEqual(status, 200)
        project = json.loads(body)['id']
        status, _, body = self.request('POST', '/api/import', {'project':project, 'filename':'run.json', 'content':SAMPLE.read_text(encoding='utf-8')})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['imported'], 1)
        entries = json.loads(self.request('GET', '/api/entries?project=' + project)[2])
        entry = entries[0]
        entry['status'] = 'published'
        self.assertEqual(self.request('POST', '/api/entries', entry)[0], 200)
        self.assertEqual(self.request('POST', '/api/entries', entry)[0], 409)
        report = self.request('GET', '/report?project=' + project)[2].decode()
        self.assertIn('HTTP project', report)
        self.assertIn('baseline extraction', report)
        backup = json.loads(self.request('GET', '/download/backup.json')[2])
        self.assertEqual(self.request('POST', '/api/restore', {'backup':backup})[0], 200)


if __name__ == '__main__':
    unittest.main()
