"""Local HTTP application and command-line entry point."""

import argparse
import json
import sqlite3
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .importers import MAX_IMPORT_BYTES, compare
from .reports import markdown, report_html
from .store import Conflict, Store


def make_server(store, port=8765, watch=False):
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, content_type='application/json', status=200, filename=None):
            payload = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(payload)

        def valid_host(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                self.send('{"error":"Local host required"}', status=403)
                return False
            return True

        def do_GET(self):
            if not self.valid_host():
                return
            parsed = urlparse(self.path)
            route, query = parsed.path, parse_qs(parsed.query)
            project = query.get('project', ['default'])[0]
            try:
                if route in ('/', '/app.js', '/style.css'):
                    name, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'application/javascript'), '/style.css': ('style.css', 'text/css')}[route]
                    self.send(Path(__file__).with_name(name).read_text(encoding='utf-8'), mime)
                elif route == '/api/projects':
                    self.send(json.dumps(dict(projects=store.projects(), version=__version__, watching=watch)))
                elif route == '/api/entries':
                    self.send(json.dumps(store.entries(project, deleted=query.get('trash') == ['1'])))
                elif route == '/api/history':
                    self.send(json.dumps(store.history(query.get('id', [''])[0], project)))
                elif route == '/api/compare':
                    left = store.entry(query.get('baseline', [''])[0], project)
                    right = store.entry(query.get('candidate', [''])[0], project)
                    self.send(json.dumps(compare(left, right)))
                elif route in ('/report', '/download/report.html', '/download/experiments.md', '/download/learnings.md'):
                    entries = store.entries(project)
                    if 'ids' in query:
                        wanted = set(query['ids'][0].split(','))
                        entries = [e for e in entries if e['id'] in wanted]
                    name = store.project(project)['name']
                    if route.endswith('.md'):
                        kind = 'experiment' if 'experiments' in route else 'learning'
                        self.send(markdown(entries, kind, name), 'text/markdown', filename=route.split('/')[-1])
                    else:
                        self.send(report_html(entries, name), 'text/html', filename='loggy-report.html' if route.startswith('/download') else None)
                elif route == '/download/backup.json':
                    self.send(json.dumps(store.backup(), indent=2), filename='loggy-backup.json')
                elif route == '/download/example.json':
                    self.send(Path(__file__).with_name('samples').joinpath('01-baseline.json').read_text(encoding='utf-8'), filename='loggy-example-run.json')
                else:
                    self.send('{"error":"Not found"}', status=404)
            except (ValueError, TypeError, KeyError) as exc:
                self.send(json.dumps({'error': str(exc)}), status=400)

        def do_POST(self):
            if not self.valid_host():
                return
            allowed = (None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
            if self.headers.get('Origin') not in allowed or self.headers.get('Content-Type') != 'application/json':
                self.send('{"error":"Same-origin JSON required"}', status=403)
                return
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 20 * 1024 * 1024:
                    raise ValueError('Request must be between 1 byte and 20 MB.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Request must be a JSON object.')
                project = data.get('project', 'default')
                route = urlparse(self.path).path
                if route == '/api/entries':
                    store.save(data, project=project)
                    result = {'saved': True}
                elif route == '/api/projects':
                    result = store.save_project(data)
                elif route == '/api/sync':
                    result = store.sync(project=project)
                elif route == '/api/import':
                    content = data.get('content')
                    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_IMPORT_BYTES:
                        raise ValueError('Upload a run JSON file smaller than 5 MB.')
                    result = {'imported': store.import_run(content.encode('utf-8'), data.get('filename', 'uploaded-run.json'), project)}
                elif route == '/api/trash':
                    if type(data.get('deleted')) is not bool:
                        raise ValueError('deleted must be true or false.')
                    store.trash(data['id'], data['revision'], data['deleted'], project)
                    result = {'saved': True}
                elif route == '/api/restore':
                    result = store.restore(data.get('backup'))
                elif route == '/api/sample':
                    result = store.sample()
                else:
                    self.send('{"error":"Not found"}', status=404)
                    return
                self.send(json.dumps(result))
            except Conflict as exc:
                self.send(json.dumps({'error': str(exc)}), status=409)
            except (ValueError, TypeError, KeyError, AttributeError, sqlite3.IntegrityError) as exc:
                self.send(json.dumps({'error': str(exc)}), status=400)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def watch_projects(store, stop):
    while not stop.is_set():
        for project in store.projects():
            if not project['folder']:
                continue
            try:
                result = store.sync(project=project['id'])
                if result['imported'] or result['errors']:
                    print(project['name'], result, flush=True)
            except (ValueError, OSError, sqlite3.Error) as exc:
                print(f"Watch error ({project['name']}): {exc}", flush=True)
        stop.wait(30)


def main():
    parser = argparse.ArgumentParser(description='Loggy: an evidence-backed experiment journal')
    parser.add_argument('command', choices=['serve', 'sync', 'export'], nargs='?', default='serve')
    parser.add_argument('--version', action='version', version=f'Loggy {__version__}')
    parser.add_argument('--project', help='Folder to link to the default project (or the selected --project-id)')
    parser.add_argument('--project-id', default='default')
    legacy = Path('.loggy/loggy.db')
    parser.add_argument('--db', default=str(legacy if legacy.exists() else Path.home() / '.loggy/loggy.db'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--watch', action='store_true', help='Check configured project folders every 30 seconds')
    parser.add_argument('--open', action='store_true', help='Open the app in your default browser')
    parser.add_argument('--output', default='output')
    args = parser.parse_args()
    try:
        store = Store(args.db)
        project = store.project(args.project_id)
        if args.project:
            project = store.save_project(dict(id=args.project_id, name=project['name'], folder=args.project))
        if args.command == 'sync':
            print(json.dumps(store.sync(project=args.project_id), indent=2))
        elif args.command == 'export':
            out = Path(args.output)
            out.mkdir(parents=True, exist_ok=True)
            entries = store.entries(args.project_id)
            for kind, name in [('experiment', 'experiments.md'), ('learning', 'learnings.md')]:
                (out / name).write_text(markdown(entries, kind, project['name']), encoding='utf-8')
            (out / 'loggy-report.html').write_text(report_html(entries, project['name']), encoding='utf-8')
            (out / 'loggy-backup.json').write_text(json.dumps(store.backup(), indent=2), encoding='utf-8')
            print(f'Exported to {out.resolve()}')
        else:
            server = make_server(store, args.port, args.watch)
            stop = threading.Event()
            if args.watch:
                threading.Thread(target=watch_projects, args=(store, stop), daemon=True).start()
            url = f'http://127.0.0.1:{server.server_port}'
            print(f'Loggy {__version__}: {url}\nDatabase: {store.path.resolve()}\nPress Ctrl+C to stop.', flush=True)
            if args.open:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                stop.set()
                server.server_close()
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'Loggy: {exc}\n')
