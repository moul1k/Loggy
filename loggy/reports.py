import html

from .store import FIELDS


def markdown(entries, kind, project_name="My project"):
    title = 'Experiments Log' if kind == 'experiment' else 'Learnings Field Guide'
    lines = [f'# {title}', '', f'Project: {project_name}', '']
    for e in reversed(entries):
        if e['kind'] != kind or e['status'] != 'published':
            continue
        lines += [f"## {e['date']} — {e['title']} ({e['id']})", '']
        for field in FIELDS[kind][2:] + (['evidence'] if e.get('evidence') else []):
            lines += [f"**{field.title()}:** {e[field]}", '']
    if len(lines) == 4:
        lines += ['No reviewed entries yet. Publish reviewed drafts in Loggy.', '']
    return '\n'.join(lines)


def report_html(entries, project_name="My project"):
    sections = ''
    for kind, title in [('experiment', 'Experiments Log'), ('learning', 'Learnings Field Guide')]:
        sections += '<section><h1>' + title + '</h1><p>Project: ' + html.escape(project_name) + '</p>'
        reviewed = [e for e in reversed(entries) if e['kind'] == kind and e['status'] == 'published']
        for e in reviewed:
            sections += f"<article><small>{html.escape(e['date'])} · {html.escape(e['id'])}</small><h2>{html.escape(e['title'])}</h2>"
            for field in FIELDS[kind][2:] + (['evidence'] if e.get('evidence') else []):
                sections += f'<h3>{field.title()}</h3><p>{html.escape(e[field])}</p>'
            sections += '</article>'
        if not reviewed:
            sections += '<p>No reviewed entries yet. Publish reviewed drafts in Loggy.</p>'
        sections += '</section>'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Loggy · Project report</title><style>body{max-width:900px;margin:50px auto;padding:24px;color:#16342f;font:16px system-ui}p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}h1{font-size:32px;letter-spacing:-1px}h2{font-size:23px}small{color:#647770}h3{font-size:13px;text-transform:uppercase;color:#536c60}article{border-top:1px solid #ddd;padding:24px 0}section+section{break-before:page;border-top:1px solid #ccc;margin-top:40px}@media print{button{display:none}body{margin:0}}</style><button onclick="print()">Print / Save PDF</button>' + sections + '</html>'

