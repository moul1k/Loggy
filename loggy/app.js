'use strict';
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fields = {experiment:['date','title','attempt','setup','result','next','outcome'], learning:['date','title','lesson','why','experiments']};
const labels = {attempt:'What did you attempt?', setup:'How did you set it up?', result:'What happened? Include failures.', next:'What will you try next?', lesson:'General lesson for a future builder', why:'Why does it hold? Cite the evidence.', experiments:'Supporting experiment IDs (space-separated)'};
let entries = [], trash = [], projects = [], tab = 'experiment', editing = null, projectEditing = null, backup = null;
let currentProject = localStorage.getItem('loggy.project') || 'default';
let refreshSequence = 0;

async function api(path, data) {
  const response = await fetch(path, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
  const body = await response.json();
  if (!response.ok) throw Error(body.error || 'Request failed');
  return body;
}
function url(path, extra = {}) { return path + '?' + new URLSearchParams({project:currentProject, ...extra}); }
function notice(message) { $('#notice').textContent = message; }
async function action(button, fn) {
  button.disabled = true;
  try { await fn(); } catch (error) { notice(error.message); } finally { button.disabled = false; }
}
async function loadProjects(preferred) {
  const result = await api('/api/projects');
  projects = result.projects;
  if (preferred) currentProject = preferred;
  $('#comparisonResult').innerHTML = '';
  if (!projects.some(p => p.id === currentProject)) currentProject = projects[0].id;
  localStorage.setItem('loggy.project', currentProject);
  $('#project').innerHTML = projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
  $('#project').value = currentProject;
  $('#version').textContent = `Loggy ${result.version}`;
  $('#watchStatus').textContent = result.watching ? 'Folder watching is on. Configured folders are checked every 30 seconds while Loggy runs.' : 'Folder watching is off. Use Sync folder or start Loggy with --watch.';
  await refresh();
}
async function refresh() {
  const sequence = ++refreshSequence;
  const [active, deleted] = await Promise.all([api(url('/api/entries')), api(url('/api/entries', {trash:1}))]);
  if (sequence !== refreshSequence) return;
  entries = active; trash = deleted;
  render();
}
function render() {
  $('#experiments').textContent = entries.filter(e => e.kind === 'experiment').length;
  $('#learnings').textContent = entries.filter(e => e.kind === 'learning').length;
  $('#drafts').textContent = entries.filter(e => e.status === 'draft').length;
  $('#welcome').hidden = !!entries.length || !!trash.length;
  document.querySelectorAll('.export').forEach(link => link.href = url(link.dataset.path));
  $('#comparison').hidden = tab !== 'compare';
  $('#journalTools').hidden = tab === 'compare';
  $('#entries').hidden = tab === 'compare';
  document.querySelectorAll('[data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
  const runs = entries.filter(e => e.kind === 'experiment' && Object.keys(e.metrics || {}).length).slice().reverse();
  for (const id of ['baseline', 'candidate']) {
    const prior = $('#' + id).value;
    $('#' + id).innerHTML = runs.map(e => `<option value="${esc(e.id)}">${esc(e.date)} · ${esc(e.title)}</option>`).join('');
    if (runs.some(e => e.id === prior)) $('#' + id).value = prior;
    else if (id === 'candidate' && runs.length > 1) $('#' + id).value = runs[runs.length - 1].id;
  }
  $('#compare').disabled = runs.length < 2;
  if (runs.length < 2) $('#comparisonResult').innerHTML = '<p>Import at least two runs with structured metrics to compare them. The demo project includes a pair.</p>';
  const query = $('#search').value.toLowerCase(), status = $('#statusFilter').value;
  const rows = (tab === 'trash' ? trash : entries.filter(e => e.kind === tab)).filter(e => JSON.stringify(e).toLowerCase().includes(query) && (status === 'all' || e.status === status));
  $('#entries').innerHTML = rows.map(entryCard).join('') || `<article><h2>${tab === 'trash' ? 'Nothing in the trash.' : 'No entries here yet.'}</h2><p>${tab === 'trash' ? 'Removed entries can be restored here.' : 'Import a run, create an entry, or adjust your filters.'}</p></article>`;
}
function entryCard(e) {
  const body = fields[e.kind].slice(2).filter(f => f !== 'outcome').map(f => `<h3 class="field-label">${esc(f)}</h3><p>${esc(e[f])}</p>`).join('');
  return `<article><div class="card-meta"><small>${esc(e.date)} · <code>${esc(e.id)}</code></small><span class="tag ${e.status === 'draft' ? 'draft' : ''}">${esc(e.status)}${e.outcome ? ' / ' + esc(e.outcome) : ''}</span></div><h2>${esc(e.title)}</h2>${body}${e.evidence ? `<details><summary>Source evidence</summary><p>${esc(e.evidence)}</p></details>` : ''}<div class="actions card-actions">${e.deleted ? `<button data-restore="${esc(e.id)}">Restore entry</button>` : `<button data-edit="${esc(e.id)}">Edit / review</button><button data-trash="${esc(e.id)}">Move to trash</button>`}<button data-history="${esc(e.id)}">History · v${e.revision}</button></div></article>`;
}
function drawFields(entry = {}) {
  $('#fields').innerHTML = fields[$('#kind').value].map(field => {
    const label = `<label for="f-${field}">${esc(labels[field] || field[0].toUpperCase() + field.slice(1))}</label>`;
    if (field === 'outcome') return label + `<select name="outcome" id="f-outcome">${['unverified','success','partial','failure'].map(v => `<option ${entry[field] === v ? 'selected' : ''}>${v}</option>`).join('')}</select>`;
    if (field === 'date' || field === 'title') {
      const today = new Date(), localDate = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
      return label + `<input required id="f-${field}" name="${field}" type="${field === 'date' ? 'date' : 'text'}" value="${esc(entry[field] || (field === 'date' ? localDate : ''))}">`;
    }
    const help = field === 'experiments' ? `<div class="reference-list">${entries.filter(e => e.kind === 'experiment').map(e => `<small><code>${esc(e.id)}</code> — ${esc(e.title)}</small>`).join('') || '<small>Create an experiment first.</small>'}</div>` : '';
    return label + `<textarea required maxlength="20000" id="f-${field}" name="${field}">${esc(entry[field] || '')}</textarea>` + help;
  }).join('');
}
function openEditor(entry) {
  editing = entry || null;
  $('#form').reset(); $('#kind').value = entry?.kind || (tab === 'learning' ? 'learning' : 'experiment');
  $('#kind').disabled = !!entry; $('#status').value = entry?.status || 'draft';
  $('#editorTitle').textContent = entry ? 'Review entry' : 'New journal entry';
  $('#formError').textContent = ''; drawFields(entry || {}); $('#editor').showModal();
}
function openProject(project) {
  projectEditing = project || null;
  $('#projectTitle').textContent = project ? 'Project settings' : 'Create a project';
  $('#projectName').value = project?.name || ''; $('#projectFolder').value = project?.folder || '';
  $('#projectError').textContent = ''; $('#projectEditor').showModal();
}
$('#project').onchange = async () => {
  currentProject = $('#project').value; localStorage.setItem('loggy.project', currentProject);
  $('#comparisonResult').innerHTML = ''; $('#search').value = ''; notice('');
  try { await refresh(); } catch (e) { notice(e.message); }
};
$('#settings').onclick = () => openProject(projects.find(p => p.id === currentProject));
$('#addProject').onclick = () => openProject();
$('#new').onclick = () => openEditor();
$('#kind').onchange = () => drawFields();
$('#search').oninput = render; $('#statusFilter').onchange = render;
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => $('#' + b.dataset.close).close());
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { tab = b.dataset.tab; render(); });
$('#form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const data = {...Object.fromEntries(new FormData(event.target)), kind:$('#kind').value, project:currentProject};
    if (editing) { data.id = editing.id; data.revision = editing.revision; }
    await api('/api/entries', data); $('#editor').close(); await refresh(); notice('Entry saved.');
  } catch (error) { $('#formError').textContent = error.message; } finally { button.disabled = false; }
};
$('#projectForm').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const data = {name:$('#projectName').value, folder:$('#projectFolder').value};
    if (projectEditing) data.id = projectEditing.id;
    const project = await api('/api/projects', data); $('#projectEditor').close();
    $('#comparisonResult').innerHTML = ''; await loadProjects(project.id); notice('Project saved.');
  } catch (error) { $('#projectError').textContent = error.message; } finally { button.disabled = false; }
};
$('#entries').onclick = async event => {
  const button = event.target.closest('button'); if (!button) return;
  const id = button.dataset.edit || button.dataset.trash || button.dataset.restore || button.dataset.history;
  const entry = [...entries, ...trash].find(e => e.id === id); if (!entry) return;
  if (button.dataset.edit) { openEditor(entry); return; }
  await action(button, async () => {
    if (button.dataset.history) {
      const history = await api(url('/api/history', {id}));
      $('#historyBody').innerHTML = history.map(h => `<details><summary>Revision ${h.revision} · saved ${esc(h.saved_at.slice(0,19).replace('T',' '))} UTC</summary><h3>${esc(h.entry.title)}</h3>${fields[h.entry.kind].map(f => `<h3 class="field-label">${esc(f)}</h3><p>${esc(h.entry[f])}</p>`).join('')}<p>Status: ${esc(h.entry.status)} · ${h.entry.deleted ? 'Trashed' : 'Active'}</p></details>`).join('') || '<p>This is the first version. Previous versions appear after an edit.</p>';
      $('#historyEditor').showModal();
    } else {
      await api('/api/trash', {project:currentProject, id, revision:entry.revision, deleted:!!button.dataset.trash});
      await refresh(); notice(button.dataset.trash ? 'Moved to trash. You can restore it from the Trash tab.' : 'Entry restored.');
    }
  });
};
$('#sync').onclick = () => action($('#sync'), async () => {
  const result = await api('/api/sync', {project:currentProject});
  await refresh(); notice(`Imported ${result.imported} new draft(s). ${result.errors.join(' ')}`);
});
$('#demo').onclick = () => action($('#demo'), async () => {
  const project = await api('/api/sample', {}); await loadProjects(project.id); notice('Demo loaded. All sample results are synthetic. Try Compare runs.');
});
$('#import').onclick = () => $('#runFile').click();
$('#runFile').onchange = async () => {
  const file = $('#runFile').files[0]; if (!file) return;
  await action($('#import'), async () => {
    if (file.size > 5 * 1024 * 1024) throw Error('Run files must be smaller than 5 MB.');
    const result = await api('/api/import', {project:currentProject, filename:file.name, content:await file.text()});
    await refresh(); notice(result.imported ? 'Run imported as a draft. Review it before publishing.' : 'That run was already imported. No duplicate created.');
  });
  $('#runFile').value = '';
};
$('#restore').onclick = () => $('#backupFile').click();
$('#backupFile').onchange = async () => {
  const file = $('#backupFile').files[0]; if (!file) return;
  try {
    if (file.size > 15 * 1024 * 1024) throw Error('Backup must be smaller than 15 MB.');
    backup = JSON.parse(await file.text());
    if (backup.format !== 'loggy-backup' || backup.version !== 1 || !Array.isArray(backup.projects) || !Array.isArray(backup.entries)) throw Error('Choose a version 1 Loggy backup file.');
    $('#restoreSummary').textContent = `${file.name}: ${backup.projects.length} project(s), ${backup.entries.length} entry/entries.`;
    $('#restoreError').textContent = ''; $('#restoreEditor').showModal();
  } catch (error) { notice(error.message); } finally { $('#backupFile').value = ''; }
};
$('#confirmRestore').onclick = async () => {
  const button = $('#confirmRestore'); button.disabled = true;
  try {
    const result = await api('/api/restore', {backup}); $('#restoreEditor').close();
    await loadProjects(result.project); notice(`Restored ${result.restored_entries} entries into ${result.restored_projects} new project(s).`);
  } catch (error) { $('#restoreError').textContent = error.message; } finally { button.disabled = false; }
};
$('#compare').onclick = () => action($('#compare'), async () => {
  const result = await api(url('/api/compare', {baseline:$('#baseline').value, candidate:$('#candidate').value}));
  const num = value => value === null ? '—' : Number(value.toPrecision(6)).toLocaleString(undefined, {maximumFractionDigits:6});
  $('#comparisonResult').innerHTML = result.warnings.map(w => `<p class="warning">${esc(w)}</p>`).join('') +
    '<div class="table-wrap"><table><thead><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th><th>Signal</th></tr></thead><tbody>' +
    result.rows.map(row => `<tr><th>${esc(row.name)}<small>${row.direction === 'unknown' ? 'Direction unknown' : esc(row.direction) + ' is better'}</small></th><td>${num(row.baseline)}</td><td>${num(row.candidate)}</td><td>${row.delta > 0 ? '+' : ''}${num(row.delta)}</td><td class="${esc(row.trend)}">${esc(row.trend)}</td></tr>`).join('') + '</tbody></table></div>' +
    '<h3>Configuration changes</h3>' + (Object.entries(result.changes).map(([key, value]) => `<p><strong>${esc(key)}</strong><br>${esc(value.baseline)} → ${esc(value.candidate)}</p>`).join('') || '<p>No recorded configuration changes.</p>') +
    '<p class="muted">Signals describe the recorded numbers only. They do not establish statistical significance or explain causality.</p>';
});
loadProjects().catch(error => notice(error.message));
setInterval(() => { if (!document.querySelector('dialog[open]')) refresh().catch(() => {}); }, 30000);
