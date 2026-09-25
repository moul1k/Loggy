"""Versioned SQLite persistence and transactional portable backups."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from .importers import MAX_IMPORT_BYTES, metrics, normalize, text

FIELDS = {
    'experiment': ['date', 'title', 'attempt', 'setup', 'result', 'next', 'outcome'],
    'learning': ['date', 'title', 'lesson', 'why', 'experiments'],
}


def uid():
    return uuid.uuid4().hex[:12]


def now():
    return datetime.now(timezone.utc).isoformat()


class Conflict(ValueError):
    pass


def clean_entry(entry):
    if not isinstance(entry, dict) or entry.get('kind') not in FIELDS:
        raise ValueError('Choose an experiment or learning.')
    kind = entry['kind']
    result = {k: text(entry.get(k), k) for k in FIELDS[kind]}
    date.fromisoformat(result['date'])
    if entry.get('status') not in ('draft', 'published'):
        raise ValueError('Choose draft or published.')
    if kind == 'experiment' and result['outcome'] not in ('success', 'partial', 'failure', 'unverified'):
        raise ValueError('Choose a valid outcome.')
    result.update(kind=kind, status=entry['status'])
    if 'evidence' in entry:
        result['evidence'] = text(entry['evidence'], 'evidence')
    if 'metrics' in entry:
        result['metrics'] = metrics(entry['metrics']) if entry['metrics'] else {}
    if 'metadata' in entry:
        meta = entry['metadata']
        if not isinstance(meta, dict):
            raise ValueError('metadata must be an object.')
        result['metadata'] = {k: text(meta.get(k), k, 'unknown') for k in ('provider', 'model', 'prompt_version', 'dataset', 'git_sha')}
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise ValueError('This database was created by a newer Loggy version.')
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='entries'").fetchone()
            legacy = bool(exists and 'project' not in [r[1] for r in db.execute('PRAGMA table_info(entries)')])
            if legacy:
                backup = self.path.with_suffix('.pre-v01.db')
                if not backup.exists():
                    dest = sqlite3.connect(backup)
                    try:
                        db.backup(dest)
                    finally:
                        dest.close()
                db.execute('BEGIN IMMEDIATE')
                db.execute('ALTER TABLE entries RENAME TO legacy_entries')
            else:
                db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT NOT NULL, created TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id), source TEXT, body TEXT NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, UNIQUE(project, source))')
            db.execute('CREATE TABLE IF NOT EXISTS history (entry_id TEXT NOT NULL REFERENCES entries(id), revision INTEGER NOT NULL, body TEXT NOT NULL, saved_at TEXT NOT NULL, PRIMARY KEY(entry_id, revision))')
            if not db.execute('SELECT 1 FROM projects').fetchone():
                db.execute('INSERT INTO projects VALUES (?, ?, ?, ?)', ('default', '99p_transcription_agent' if legacy else 'My first project', '', now()))
            if legacy:
                for row in db.execute('SELECT id,source,body FROM legacy_entries').fetchall():
                    db.execute('INSERT INTO entries VALUES (?, ?, ?, ?, 1, 0)', (row['id'], 'default', row['source'], row['body']))
                db.execute('DROP TABLE legacy_entries')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def projects(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM projects ORDER BY created, id')]

    def project(self, project='default'):
        with self.connect() as db:
            row = db.execute('SELECT * FROM projects WHERE id=?', (project,)).fetchone()
            if not row:
                raise ValueError('Project not found.')
            return dict(row)

    def save_project(self, data):
        name = text(data.get('name'), 'Project name')
        if len(name) > 120:
            raise ValueError('Project name must be at most 120 characters.')
        folder = data.get('folder', '')
        if not isinstance(folder, str):
            raise ValueError('Folder must be text.')
        folder = str(Path(folder).expanduser().resolve()) if folder.strip() else ''
        if folder and not Path(folder).is_dir():
            raise ValueError('Folder does not exist on this computer.')
        project_id = data.get('id') or uid()
        with self.connect() as db:
            if data.get('id'):
                if not db.execute('UPDATE projects SET name=?,folder=? WHERE id=?', (name, folder, project_id)).rowcount:
                    raise ValueError('Project not found.')
            else:
                db.execute('INSERT INTO projects VALUES (?, ?, ?, ?)', (project_id, name, folder, now()))
        return self.project(project_id)

    @staticmethod
    def unpack(row):
        return dict(json.loads(row['body']), id=row['id'], project=row['project'], revision=row['revision'], deleted=bool(row['deleted']))

    def entries(self, project='default', deleted=False):
        self.project(project)
        with self.connect() as db:
            return sorted([self.unpack(row) for row in db.execute('SELECT * FROM entries WHERE project=? AND deleted=?', (project, int(deleted)))], key=lambda e: (e['date'], e['id']), reverse=True)

    def entry(self, entry_id, project='default'):
        with self.connect() as db:
            row = db.execute('SELECT * FROM entries WHERE id=? AND project=?', (entry_id, project)).fetchone()
            if not row:
                raise ValueError('Entry not found in this project.')
            return self.unpack(row)

    def save(self, entry, source=None, project='default'):
        self.project(project)
        body = clean_entry(entry)
        entry_id = entry.get('id') or uid()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if source and db.execute('SELECT 1 FROM entries WHERE project=? AND source=?', (project, source)).fetchone():
                return 0
            old = db.execute('SELECT * FROM entries WHERE id=?', (entry_id,)).fetchone()
            if old:
                if old['project'] != project or old['deleted']:
                    raise ValueError('Cannot edit this entry.')
                if entry.get('revision') != old['revision']:
                    raise Conflict('This entry changed in another window. Reopen it before saving.')
                old_body = json.loads(old['body'])
                if body['kind'] != old_body['kind']:
                    raise ValueError('An existing entry cannot change type.')
                # Imported measurements and provenance are immutable during manual edits.
                for key in ('metrics', 'metadata', 'evidence'):
                    if key in old_body:
                        body[key] = old_body[key]
            if body['kind'] == 'learning':
                linked = set(body['experiments'].split())
                for link in linked:
                    row = db.execute('SELECT body,deleted FROM entries WHERE id=? AND project=?', (link, project)).fetchone()
                    if not row or row['deleted'] or json.loads(row['body'])['kind'] != 'experiment':
                        raise ValueError('Link active experiment IDs from this project, separated by spaces.')
                    if body['status'] == 'published' and json.loads(row['body'])['status'] != 'published':
                        raise ValueError('Publish the supporting experiments before publishing this learning.')
            if old and body['kind'] == 'experiment' and body['status'] == 'draft':
                for row in db.execute('SELECT body FROM entries WHERE project=? AND deleted=0', (project,)):
                    linked = json.loads(row['body'])
                    if linked['kind'] == 'learning' and linked['status'] == 'published' and entry_id in linked['experiments'].split():
                        raise ValueError('Move linked published learnings to draft before unpublishing this experiment.')
            revision = old['revision'] + 1 if old else 1
            if old:
                self.snapshot(db, old)
                db.execute('UPDATE entries SET body=?,revision=? WHERE id=?', (json.dumps(body), revision, entry_id))
            else:
                db.execute('INSERT INTO entries VALUES (?, ?, ?, ?, 1, 0)', (entry_id, project, source, json.dumps(body)))
        return 1

    @staticmethod
    def snapshot(db, row):
        snapshot = dict(json.loads(row['body']), deleted=bool(row['deleted']))
        db.execute('INSERT INTO history VALUES (?, ?, ?, ?)', (row['id'], row['revision'], json.dumps(snapshot), now()))

    def trash(self, entry_id, revision, deleted, project='default'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM entries WHERE id=? AND project=?', (entry_id, project)).fetchone()
            if not old:
                raise ValueError('Entry not found.')
            if old['revision'] != revision:
                raise Conflict('This entry changed. Refresh before trying again.')
            body = json.loads(old['body'])
            if deleted and body['kind'] == 'experiment':
                for row in db.execute('SELECT body FROM entries WHERE project=? AND deleted=0', (project,)):
                    learning = json.loads(row['body'])
                    if learning['kind'] == 'learning' and entry_id in learning['experiments'].split():
                        raise ValueError('This experiment supports a learning. Update or trash that learning first.')
            if not deleted and body['kind'] == 'learning':
                for link in body['experiments'].split():
                    if not db.execute('SELECT 1 FROM entries WHERE id=? AND project=? AND deleted=0', (link, project)).fetchone():
                        raise ValueError('Restore the supporting experiments first.')
            self.snapshot(db, old)
            db.execute('UPDATE entries SET deleted=?,revision=revision+1 WHERE id=?', (int(deleted), entry_id))

    def history(self, entry_id, project='default'):
        self.entry(entry_id, project)
        with self.connect() as db:
            return [dict(revision=r['revision'], saved_at=r['saved_at'], entry=json.loads(r['body'])) for r in db.execute('SELECT * FROM history WHERE entry_id=? ORDER BY revision DESC', (entry_id,))]

    def import_run(self, raw, filename, project='default'):
        entry, digest = normalize(raw, Path(filename.replace('\\', '/')).name)
        return self.save(entry, source=digest, project=project)

    def sync(self, folder=None, project='default'):
        configured = folder or self.project(project)['folder']
        if not configured:
            raise ValueError('Set a project folder in Project settings, or upload a run JSON file.')
        root = Path(configured)
        folder = root / 'artifacts' / 'runs' if (root / 'artifacts' / 'runs').is_dir() else root
        if not folder.is_dir():
            raise ValueError('The configured project folder is unavailable.')
        count, errors = 0, []
        for path in sorted(folder.glob('*.json')):
            try:
                if path.stat().st_size > MAX_IMPORT_BYTES:
                    raise ValueError('Run file exceeds the 5 MB limit.')
                count += self.import_run(path.read_bytes(), path.name, project)
            except (ValueError, OSError) as exc:
                errors.append(f'{path.name}: {exc}')
        return dict(imported=count, errors=errors)

    def backup(self, project=None):
        with self.connect() as db:
            db.execute('BEGIN')
            projects = [dict(r) for r in db.execute('SELECT * FROM projects') if project is None or r['id'] == project]
            ids = {p['id'] for p in projects}
            entries = [dict(r) for r in db.execute('SELECT * FROM entries') if r['project'] in ids]
            entry_ids = {r['id'] for r in entries}
            history = [dict(r) for r in db.execute('SELECT * FROM history') if r['entry_id'] in entry_ids]
        return dict(format='loggy-backup', version=1, created_at=now(), projects=projects, entries=entries, history=history)

    def restore(self, data):
        if not isinstance(data, dict) or data.get('format') != 'loggy-backup' or data.get('version') != 1:
            raise ValueError('Use a version 1 Loggy backup. Legacy entry-only JSON is not a restorable backup.')
        projects, entries, history = data.get('projects'), data.get('entries'), data.get('history', [])
        if not isinstance(projects, list) or not projects or not isinstance(entries, list) or not isinstance(history, list):
            raise ValueError('Backup needs projects, entries, and history arrays.')
        project_map, entry_map = {}, {}
        for p in projects:
            old_id = text(p.get('id'), 'project id')
            if old_id in project_map:
                raise ValueError('Duplicate project ID in backup.')
            text(p.get('name'), 'project name')
            project_map[old_id] = uid()
        bodies = {}
        for e in entries:
            old_id = text(e.get('id'), 'entry id')
            if old_id in entry_map or e.get('project') not in project_map:
                raise ValueError('Invalid or duplicate entry ID/project in backup.')
            body = clean_entry(json.loads(e['body']))
            if type(e.get('revision')) is not int or e['revision'] < 1 or e.get('deleted') not in (0, 1):
                raise ValueError('Invalid backup entry revision or deleted flag.')
            if e.get('source') is not None:
                text(e['source'], 'source')
            entry_map[old_id], bodies[old_id] = uid(), body
        entry_index = {e['id']: e for e in entries}
        for e in entries:
            body = bodies[e['id']]
            if body['kind'] == 'learning' and not e['deleted']:
                for link in body['experiments'].split():
                    target = entry_index.get(link)
                    if not target or target['deleted']:
                        raise ValueError('Active learning references a missing or trashed experiment.')
                    if body['status'] == 'published' and bodies[link]['status'] != 'published':
                        raise ValueError('Published learning references an unpublished experiment.')

        def remap(body, owner):
            if body['kind'] == 'learning':
                links = body['experiments'].split()
                if any(link not in entry_map or entry_index[link]['project'] != owner['project'] or bodies[link]['kind'] != 'experiment' for link in links):
                    raise ValueError('Backup has a missing or invalid experiment reference.')
                body['experiments'] = ' '.join(entry_map[link] for link in links)
            return body

        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for p in projects:
                # Restore to separate projects and never resume watching a path from a downloaded backup.
                db.execute('INSERT INTO projects VALUES (?, ?, ?, ?)', (project_map[p['id']], text(p['name'], 'name')[:109] + ' (restored)', '', now()))
            for e in entries:
                body = remap(dict(bodies[e['id']]), e)
                db.execute('INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)', (entry_map[e['id']], project_map[e['project']], e.get('source'), json.dumps(body), e['revision'], e['deleted']))
            for h in history:
                owner = entry_index.get(h.get('entry_id'))
                if not owner or type(h.get('revision')) is not int or not 1 <= h['revision'] < owner['revision']:
                    raise ValueError('Invalid backup history reference or revision.')
                snapshot = json.loads(h['body'])
                body = remap(clean_entry(snapshot), owner)
                body['deleted'] = bool(snapshot.get('deleted', False))
                db.execute('INSERT INTO history VALUES (?, ?, ?, ?)', (entry_map[owner['id']], h['revision'], json.dumps(body), text(h.get('saved_at'), 'saved_at')))
        return dict(restored_projects=len(projects), restored_entries=len(entries), project=next(iter(project_map.values())))

    def sample(self):
        project = self.save_project({'name': 'Demo · Meeting assistant', 'folder': ''})
        for path in sorted(Path(__file__).with_name('samples').glob('*.json')):
            self.import_run(path.read_bytes(), path.name, project['id'])
        for entry in self.entries(project['id']):
            entry['status'] = 'published'
            entry['outcome'] = 'partial'
            self.save(entry, project=project['id'])
        entries = self.entries(project['id'])
        if entries:
            self.save(dict(kind='learning', status='published', date='2026-09-21',
                           title='Check quality and latency together',
                           lesson='A quality improvement can come with a latency regression.',
                           why='In these synthetic demo runs, recall increases from 0.72 to 0.86 while latency rises from 900 to 1250 ms. This illustrates a tradeoff; it is not a real model result.',
                           experiments=' '.join(e['id'] for e in entries)), project=project['id'])
        return project
