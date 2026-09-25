"""Validate and normalize run artifacts without copying transcripts or raw outputs."""

import hashlib
import json
import math
from datetime import date

MAX_IMPORT_BYTES = 5 * 1024 * 1024


def text(value, name, default=None):
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip() or len(value) > 20000:
        raise ValueError(f'{name} must be nonempty text (at most 20,000 characters).')
    return value.strip()


def metrics(value):
    if not isinstance(value, dict) or not value or len(value) > 200:
        raise ValueError('metrics must contain 1–200 named measurements.')
    result = {}
    for name, metric in value.items():
        text(name, 'Metric name')
        if not isinstance(metric, dict):
            raise ValueError(f'Metric {name} needs value and direction fields.')
        number, direction = metric.get('value'), metric.get('direction', 'unknown')
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number):
            raise ValueError(f'Metric {name} must have a finite numeric value.')
        if direction not in ('higher', 'lower', 'unknown'):
            raise ValueError(f'Metric {name}: direction must be higher, lower, or unknown.')
        result[name] = {'value': number, 'direction': direction}
    return result


def normalize(raw, filename='uploaded-run.json'):
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError('Run file exceeds the 5 MB limit.')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Run file must be valid UTF-8 JSON.') from exc
    if not isinstance(data, dict):
        raise ValueError('A run must be a JSON object.')
    if data.get('schema_version') == 1:
        created = text(data.get('date'), 'date')
        date.fromisoformat(created)
        measurement = metrics(data.get('metrics'))
        meta = data.get('metadata', {})
        if not isinstance(meta, dict):
            raise ValueError('metadata must be an object.')
        metadata = {k: text(meta.get(k), k, 'unknown') for k in ('provider', 'model', 'prompt_version', 'dataset', 'git_sha')}
        entry = dict(date=created, title=text(data.get('title'), 'title'),
                     attempt=text(data.get('attempt'), 'attempt'), setup=text(data.get('setup'), 'setup'),
                     result=text(data.get('result'), 'result', 'Measurements imported; interpretation needs review.'),
                     next=text(data.get('next'), 'next', 'Review the measurements and record the next experiment.'))
    elif all(k in data for k in ('run', 'aggregate', 'cases')):
        run, aggregate, cases = data['run'], data['aggregate'], data['cases']
        if not isinstance(run, dict) or not isinstance(aggregate, dict) or not isinstance(cases, list):
            raise ValueError('Benchmark needs run/aggregate objects and a cases array.')
        created = text(run.get('created_at'), 'run.created_at')[:10]
        date.fromisoformat(created)
        metadata = {k: text(run.get(k), k, 'unknown') for k in ('provider', 'model', 'prompt_version', 'dataset', 'git_sha')}
        # Only the known meeting-eval score names carry a direction. Other metrics remain unknown.
        known = {'no_hallucinations', 'action_owner_accuracy', 'action_due_at_accuracy', 'event_start_at_accuracy'}
        known.update(f'{feature}_{metric}' for feature in ('action_items', 'calendar_events', 'decisions', 'follow_ups') for metric in ('precision', 'recall'))
        measurement = metrics({k: {'value': v, 'direction': 'higher' if k in known else 'unknown'} for k, v in aggregate.items()}) if aggregate else {}
        failures = 0
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get('scores', []), list):
                raise ValueError('Each benchmark case must be an object with a scores array.')
            scores = case.get('scores', [])
            for score in scores:
                if not isinstance(score, dict) or isinstance(score.get('value'), bool) or not isinstance(score.get('value'), (float, int)) or not math.isfinite(score['value']):
                    raise ValueError('Case score values must be finite numbers.')
            failures += bool(case.get('error')) or any(s['value'] < 1 for s in scores)
        entry = dict(date=created, title=f"{metadata['provider']} benchmark · {metadata['prompt_version']}",
                     attempt='Run the meeting-agent evaluation benchmark.',
                     setup=f"Provider: {metadata['provider']}; model metadata: {metadata['model']}; cases: {len(cases)}; commit: {metadata['git_sha']}.",
                     result=f'Cases with an error or score below 1: {failures}/{len(cases)}.',
                     next='Review the recorded results and choose the next experiment.')
    else:
        raise ValueError('Unsupported run format. Use Loggy schema_version 1 or a meeting-eval artifact.')
    summary = '; '.join(f'{k}: {v["value"]}' for k, v in measurement.items())
    entry['result'] += ('\n' + summary) if summary else '\nNo aggregate metrics recorded.'
    if metadata['provider'] == 'fixture':
        entry['result'] += '\nFixture provider copies expected outputs. These scores validate the harness, not model quality.'
    digest = hashlib.sha256(raw).hexdigest()
    entry.update(kind='experiment', status='draft', outcome='unverified',
                 evidence=f'{filename} | SHA-256 {digest}', metrics=measurement, metadata=metadata)
    return entry, digest


def compare(baseline, candidate):
    if baseline['id'] == candidate['id']:
        raise ValueError('Choose two different experiments.')
    if any(e['kind'] != 'experiment' for e in (baseline, candidate)):
        raise ValueError('Only experiments can be compared.')
    warnings = []
    a_meta, b_meta = baseline.get('metadata', {}), candidate.get('metadata', {})
    if 'fixture' in (a_meta.get('provider'), b_meta.get('provider')):
        warnings.append('A fixture run is included. Fixture scores are harness checks, not evidence of model quality.')
    if a_meta.get('dataset', 'unknown') == 'unknown' or b_meta.get('dataset', 'unknown') == 'unknown':
        warnings.append('Dataset identity is missing. Confirm the runs used the same evaluation set.')
    changes = {k: {'baseline': a_meta.get(k, 'unknown'), 'candidate': b_meta.get(k, 'unknown')}
               for k in ('provider', 'model', 'prompt_version', 'dataset', 'git_sha')
               if a_meta.get(k, 'unknown') != b_meta.get(k, 'unknown')}
    if 'dataset' in changes:
        warnings.append('The dataset changed; metric differences may not measure a model improvement.')
    rows = []
    a, b = baseline.get('metrics', {}), candidate.get('metrics', {})
    for name in sorted(a.keys() | b.keys()):
        left, right = a.get(name), b.get(name)
        delta = right['value'] - left['value'] if left and right else None
        direction = left['direction'] if left and right and left['direction'] == right['direction'] else 'unknown'
        trend = 'not comparable' if delta is None else 'unchanged' if delta == 0 else 'unclassified'
        if delta and direction != 'unknown':
            trend = 'improved' if (delta > 0) == (direction == 'higher') else 'regressed'
        rows.append(dict(name=name, baseline=left['value'] if left else None, candidate=right['value'] if right else None,
                         delta=delta, direction=direction, trend=trend))
    if not rows:
        warnings.append('These entries have no structured metrics. Import run files to compare measurements.')
    return dict(baseline=baseline['title'], candidate=candidate['title'], rows=rows, changes=changes, warnings=warnings)
