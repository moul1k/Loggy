import argparse
import hashlib
import html
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

FIELDS = {
    'experiment': ['date', 'title', 'attempt', 'setup', 'result', 'next', 'outcome'],
    'learning': ['date', 'title', 'lesson', 'why', 'experiments'],
}


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, source TEXT UNIQUE, body TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def entries(self):
        with self.connect() as db:
            return sorted([json.loads(r[0]) for r in db.execute('SELECT body FROM entries')], key=lambda e: (e['date'], e['id']), reverse=True)

    def save(self, entry, source=None):
        kind = entry.get('kind')
        if kind not in FIELDS:
            raise ValueError('Choose an experiment or learning.')
        if any(not isinstance(entry.get(f), str) or not entry[f].strip() for f in FIELDS[kind]):
            raise ValueError('Complete every field before saving.')
        date.fromisoformat(entry['date'])
        if entry.get('status') not in ('draft', 'published'):
            raise ValueError('Invalid review status.')
        if kind == 'experiment' and entry['outcome'] not in ('success', 'partial', 'failure', 'unverified'):
            raise ValueError('Invalid outcome.')
        if kind == 'learning':
            ids = {e['id'] for e in self.entries() if e['kind'] == 'experiment'}
            if not set(entry['experiments'].split()).issubset(ids):
                raise ValueError('Link valid experiment IDs, separated by spaces.')
        entry = {k: entry[k] for k in FIELDS[kind] + ['kind', 'status'] + [k for k in ('id', 'evidence') if k in entry]}
        entry.setdefault('id', str(uuid.uuid4())[:8])
        with self.connect() as db:
            if source:
                return db.execute('INSERT OR IGNORE INTO entries VALUES (?, ?, ?)', (entry['id'], source, json.dumps(entry))).rowcount
            old = db.execute('SELECT source FROM entries WHERE id=?', (entry['id'],)).fetchone()
            db.execute('INSERT OR REPLACE INTO entries VALUES (?, ?, ?)', (entry['id'], old[0] if old else None, json.dumps(entry)))
        return 1

    def sync(self, project):
        folder = Path(project) / 'artifacts' / 'runs'
        if not folder.is_dir():
            raise ValueError('Project must contain an artifacts/runs directory.')
        count, errors = 0, []
        for path in sorted(folder.glob('*.json')):
            try:
                raw = path.read_bytes()
                report = json.loads(raw)
                run, metrics, cases = report['run'], report['aggregate'], report['cases']
                fixture = run['provider'] == 'fixture'
                failed = sum(bool(c.get('error')) or any(float(s['value']) < 1 for s in c.get('scores', [])) for c in cases)
                result = '; '.join(f'{k}: {v}' for k, v in metrics.items()) or 'No aggregate metrics recorded.'
                result += f'\nCases with an error or score below 1: {failed}/{len(cases)}.'
                if fixture:
                    result += '\nFixture provider copies expected outputs. These scores validate the harness, not model quality.'
                digest = hashlib.sha256(raw).hexdigest()
                count += self.save(dict(kind='experiment', status='draft', date=run['created_at'][:10],
                    title=f"{run['provider']} benchmark · {run.get('prompt_version', 'unknown prompt')}",
                    attempt='Run the meeting-agent evaluation benchmark.',
                    setup=f"Provider: {run['provider']}; model metadata: {run.get('model', 'unknown')}; cases: {len(cases)}; commit: {run.get('git_sha', 'unknown')}.",
                    result=result, next='Review failed cases and choose the next experiment.', outcome='unverified',
                    evidence=f'{path.name} | SHA-256 {digest}'), source=digest)
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                errors.append(f'{path.name}: {exc}')
        return {'imported': count, 'errors': errors}


def markdown(entries, kind):
    title = 'Experiments Log' if kind == 'experiment' else 'Learnings Field Guide'
    lines = [f'# {title}', '', 'Project: 99p_transcription_agent', '']
    for e in reversed(entries):
        if e['kind'] != kind or e['status'] != 'published':
            continue
        lines += [f"## {e['date']} — {e['title']} ({e['id']})", '']
        for field in FIELDS[kind][2:] + (['evidence'] if e.get('evidence') else []):
            lines += [f"**{field.title()}:** {e[field]}", '']
    if len(lines) == 4:
        lines += ['No reviewed entries yet. Publish reviewed drafts in Loggy.', '']
    return '\n'.join(lines)


def report_html(entries):
    sections = ''
    for kind, title in [('experiment', 'Experiments Log'), ('learning', 'Learnings Field Guide')]:
        sections += '<section><h1>' + title + '</h1><p>Project: 99p_transcription_agent</p>'
        reviewed = [e for e in reversed(entries) if e['kind'] == kind and e['status'] == 'published']
        for e in reviewed:
            sections += f"<article><small>{html.escape(e['date'])} · {html.escape(e['id'])}</small><h2>{html.escape(e['title'])}</h2>"
            for field in FIELDS[kind][2:] + (['evidence'] if e.get('evidence') else []):
                sections += f'<h3>{field.title()}</h3><p>{html.escape(e[field])}</p>'
            sections += '</article>'
        if not reviewed:
            sections += '<p>No reviewed entries yet. Publish reviewed drafts in Loggy.</p>'
        sections += '</section>'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Loggy · Capstone report</title><style>body{max-width:900px;margin:50px auto;padding:24px;color:#16342f;font:16px system-ui}p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}h3{font-size:13px;text-transform:uppercase;color:#536c60}article{border-top:1px solid #ddd;padding:24px 0}section+section{break-before:page;border-top:1px solid #ccc;margin-top:40px}@media print{button{display:none}body{margin:0}}</style><button onclick="print()">Print / Save PDF</button>' + sections + '</html>'


def serve(store, project, port, watch):
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, content_type='application/json', status=200, filename=None):
            payload = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            route = urlparse(self.path).path
            if route == '/':
                self.send(Path(__file__).with_name('index.html').read_text(encoding='utf-8'), 'text/html')
            elif route == '/api/entries':
                self.send(json.dumps(store.entries()))
            elif route == '/report':
                self.send(report_html(store.entries()), 'text/html')
            elif route == '/download/report.html':
                self.send(report_html(store.entries()), 'text/html', filename='capstone-report.html')
            elif route in ('/download/experiments.md', '/download/learnings.md'):
                kind = 'experiment' if 'experiments' in route else 'learning'
                self.send(markdown(store.entries(), kind), 'text/markdown', filename=route.split('/')[-1])
            elif route == '/download/backup.json':
                self.send(json.dumps(store.entries(), indent=2), filename='loggy-backup.json')
            else:
                self.send('{"error":"Not found"}', status=404)

        def do_POST(self):
            # Only same-origin JSON writes; no permissive CORS or form requests.
            if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{port}', f'http://localhost:{port}') or self.headers.get('Content-Type') != 'application/json':
                self.send('{"error":"Same-origin JSON required"}', status=403)
                return
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 100000:
                    raise ValueError('Invalid request size.')
                data = json.loads(self.rfile.read(length))
                if self.path == '/api/entries':
                    store.save(data)
                    result = {'saved': True}
                elif self.path == '/api/sync':
                    result = store.sync(project)
                else:
                    self.send('{"error":"Not found"}', status=404)
                    return
                self.send(json.dumps(result))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                self.send(json.dumps({'error': str(exc)}), status=400)

    if watch:
        def poll():
            while True:
                try:
                    result = store.sync(project)
                    if result['imported'] or result['errors']:
                        print(result, flush=True)
                except ValueError as exc:
                    print(exc, flush=True)
                threading.Event().wait(30)
        threading.Thread(target=poll, daemon=True).start()
    print(f'Loggy: http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description='Loggy: evidence-based capstone logging agent')
    parser.add_argument('command', choices=['serve', 'sync', 'export'], nargs='?', default='serve')
    parser.add_argument('--project', default='../99p_transcription_agent 2')
    parser.add_argument('--db', default='.loggy/loggy.db')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--output', default='output')
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == 'sync':
        print(json.dumps(store.sync(args.project), indent=2))
    elif args.command == 'export':
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        for kind, name in [('experiment', 'experiments.md'), ('learning', 'learnings.md')]:
            (out / name).write_text(markdown(store.entries(), kind), encoding='utf-8')
        (out / 'capstone-report.html').write_text(report_html(store.entries()), encoding='utf-8')
        (out / 'loggy-backup.json').write_text(json.dumps(store.entries(), indent=2), encoding='utf-8')
        print(f'Exported to {out.resolve()}')
    else:
        serve(store, args.project, args.port, args.watch)
