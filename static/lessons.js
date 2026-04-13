async function getJSON(url) {
  const r = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
  const j = await r.json();
  return { ok: r.ok, status: r.status, data: j };
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data: j };
}

function $(id){ return document.getElementById(id); }

let LESSONS = [];
let CLASSES = [];
const DEFAULT_ROBOT_TYPE = 'turbopi';
let ACTIVE_ROBOT_TYPE = DEFAULT_ROBOT_TYPE;
const LESSON_ROBOT_TYPES = ['turbopi', 'spiderpi', 'tonypi'];

function optionKind(opt){
  const k = (opt && opt.kind ? String(opt.kind) : '').toLowerCase();
  if (k) return k;
  const id = String((opt && opt.id) || '').toLowerCase();
  const title = String((opt && opt.title) || '').toLowerCase();
  if (id.startsWith('level_') || id.startsWith('level-')) return 'level';
  if (id.includes('demo') || title.includes('demo')) return 'demo';
  return 'option';
}

function levelUiMode(levels){
  const kinds = new Set((levels || []).map(optionKind));
  if (kinds.has('level') && kinds.has('demo')) return 'mixed';
  if (kinds.has('level')) return 'level';
  if (kinds.has('demo')) return 'demo';
  return 'option';
}

function short(s, n) {
  if (!s) return '';
  s = String(s);
  return s.length > n ? s.slice(0, n) + '…' : s;
}

function setRepoUI(robotType, repo){
  const badge = $(`repoBadge-${robotType}`);
  const details = $(`repoDetails-${robotType}`);
  if (!badge || !details) return;

  if (!repo || !repo.cloned){
    badge.className = 'badge bg-secondary';
    badge.textContent = 'Not cloned';
    details.textContent = 'Set the lessons repo and click Sync repo.';
    return;
  }
  const up = repo.update_available;
  badge.className = 'badge ' + (up ? 'bg-warning text-dark' : 'bg-success');
  badge.textContent = up ? 'Update available' : 'Up to date';

  const lc = repo.local_commit ? repo.local_commit.slice(0,7) : 'n/a';
  const rc = repo.remote_commit ? repo.remote_commit.slice(0,7) : 'n/a';
  details.textContent = `Ref: ${repo.ref} · Local: ${lc} · Remote: ${rc}` + (repo.dirty ? ' · Working tree dirty' : '');
}

function setBundleUI(robotType, state, text) {
  const badge = $(`repoBadge-${robotType}`);
  const meta = $(`bundleMeta-${robotType}`);
  if (!badge || !meta) return;
  if (state === 'bundle-loading') {
    meta.textContent = 'Loading bundle…';
    return;
  }
  if (state === 'bundle-error') {
    meta.textContent = String(text || 'Bundle error');
    return;
  }
  if (state === 'bundle-none') {
    meta.textContent = '(no bundle found)';
    return;
  }
  meta.textContent = text;
}

async function refreshLessonSourceCard(robotType) {
  setRepoUI(robotType, null);
  setBundleUI(robotType, 'bundle-loading');

  let repo = null;
  try {
    const repoRes = await getJSON(`/api/lessons/repo_status?robot_type=${encodeURIComponent(robotType)}`);
    repo = repoRes.ok ? (repoRes.data.repo || {}) : null;
    setRepoUI(robotType, repo);
  } catch (e) {
    const details = $(`repoDetails-${robotType}`);
    if (details) details.textContent = String(e);
  }

  try {
    const bundleRes = await getJSON(`/api/lessons/bundle?robot_type=${encodeURIComponent(robotType)}`);
    const bundle = bundleRes.ok ? (bundleRes.data || {}) : {};
    if (!bundle.ok || !bundle.filename) {
      setBundleUI(robotType, 'bundle-none');
      return;
    }
    const parts = [bundle.filename];
    if (bundle.sha256) parts.push(`sha256 ${short(bundle.sha256, 12)}`);
    if (bundle.commit) parts.push(`commit ${short(bundle.commit, 10)}`);
    setBundleUI(robotType, 'bundle-ok', parts.join(' · '));
  } catch (e) {
    setBundleUI(robotType, 'bundle-error', String(e));
  }
}

async function refreshAllLessonSourceCards() {
  await Promise.all(LESSON_ROBOT_TYPES.map(refreshLessonSourceCard));
}

function renderCurrent(selected){
  const title = $('currentLessonTitle');
  const meta  = $('currentLessonMeta');
  const upd   = $('currentLessonUpdated');
  if (!title || !meta || !upd) return;

  if (!selected || !selected.lesson_id){
    title.textContent = 'No current lesson set';
    meta.textContent = 'Use “Set current lesson” below.';
    upd.textContent = '';
    return;
  }

  // Title line
  const lessonTxt = selected.lesson_title || selected.lesson_id;
  const rawLevel = selected.level_title || selected.level_id || '';
  const hideLevel = String(rawLevel).toLowerCase() === 'default';
  const levelTxt = hideLevel ? '' : rawLevel;
  const nice = levelTxt ? `${lessonTxt} · ${levelTxt}` : `${lessonTxt}`;
  title.textContent = nice;

  // Meta line
  const bits = [];
  if (selected.lesson_path) bits.push(`Path: ${selected.lesson_path}`);
  if (selected.entry) bits.push(`Entry: ${selected.entry}`);
  if (selected.overwrite_student_files) bits.push("Overwrite: ON");
  if (selected.preferred_robot_type) bits.push(`Robot: ${selected.preferred_robot_type}`);
  // If you later add class assignment into selected, we’ll display it automatically:
  if (selected.class_code) bits.push(`Class: ${selected.class_code}`);
  meta.textContent = bits.join(' · ');

  // Updated
  if (selected.updated_at) {
    upd.textContent = `Updated: ${selected.updated_at}`;
  } else {
    upd.textContent = '';
  }
}

function currentRobotTypeForClass(){
  const classCode = $('classSelect')?.value || '';
  const cls = CLASSES.find((c) => (c.code || '') === classCode) || {};
  return (cls.preferred_robot_type || DEFAULT_ROBOT_TYPE);
}

function currentRobotType(){
  return $('robotTypeSelect')?.value || ACTIVE_ROBOT_TYPE || currentRobotTypeForClass() || DEFAULT_ROBOT_TYPE;
}

function syncRobotTypeSelect(selected){
  const robotSel = $('robotTypeSelect');
  if (!robotSel) return;
  const picked = ACTIVE_ROBOT_TYPE || (selected && selected.preferred_robot_type) || currentRobotTypeForClass() || DEFAULT_ROBOT_TYPE;
  robotSel.value = picked;
}

function populateClassSelect(selected){
  const classSel = $('classSelect');
  if (!classSel) return;
  classSel.innerHTML = '';
  for (const c of CLASSES){
    const opt = document.createElement('option');
    opt.value = c.code;
    opt.textContent = c.name ? `${c.code} · ${c.name}` : c.code;
    classSel.appendChild(opt);
  }
  if (selected && selected.class_code) {
    classSel.value = selected.class_code;
  }
}

function populateLessonSelect(selected){
  const lessonSel = $('lessonSelect');
  if (!lessonSel) return;

  lessonSel.innerHTML = '';
  for (const l of LESSONS){
    const opt = document.createElement('option');
    opt.value = l.id;
    opt.textContent = `${l.title} (${l.id})`;
    lessonSel.appendChild(opt);
  }

  if (selected && selected.lesson_id){
    lessonSel.value = selected.lesson_id;
  }
  populateLevelSelect(selected);
}

function populateLevelSelect(selected){
  const lessonSel = $('lessonSelect');
  const levelSel = $('levelSelect');
  const levelWrap = $('levelWrap');
  const levelLabel = $('levelLabel');
  if (!lessonSel || !levelSel) return;

  const lessonId = lessonSel.value;
  levelSel.innerHTML = '';
  const lesson = LESSONS.find(x => x.id === lessonId);
  const levels = (lesson && lesson.levels) ? lesson.levels : [];
  const mode = levelUiMode(levels);
  const showSelector = !(levels.length <= 1 && mode !== 'level');

  if (levelWrap) levelWrap.classList.toggle('d-none', !showSelector);
  if (levelLabel) {
    levelLabel.textContent =
      mode === 'demo' ? 'Demo'
      : mode === 'mixed' ? 'Option'
      : mode === 'option' ? 'Option'
      : 'Level';
  }

  for (const lv of levels){
    const opt = document.createElement('option');
    opt.value = lv.id;
    opt.textContent = lv.title || lv.id;
    levelSel.appendChild(opt);
  }

  if (selected && selected.level_id && levels.find(x=>x.id===selected.level_id)){
    levelSel.value = selected.level_id;
  } else if (levels.length > 0) {
    levelSel.value = levels[0].id;
  }
}

async function refresh(robotType){
  const selectedRobotType = robotType || currentRobotType() || DEFAULT_ROBOT_TYPE;
  ACTIVE_ROBOT_TYPE = selectedRobotType;
  const res = await getJSON(`/api/lessons?robot_type=${encodeURIComponent(selectedRobotType)}`);
  if (!res.ok){
    const box = $('results');
    if (box) box.textContent = 'Failed to load lessons.';
    return;
  }

  LESSONS = res.data.lessons || [];
  CLASSES = res.data.classes || [];
  renderCurrent(res.data.selected || {});
  const chkOverwrite = $('chkOverwrite');
  if (chkOverwrite) chkOverwrite.checked = !!(res.data.selected && res.data.selected.overwrite_student_files);
  populateClassSelect(res.data.selected || {});
  populateLessonSelect(res.data.selected || {});
  syncRobotTypeSelect({});
  await refreshAllLessonSourceCards();
}

async function syncRepo(robotType){
  const out = $('results');
  if (out) out.textContent = 'Syncing repo...';

  const robot_type = robotType || currentRobotType();
  const res = await postJSON('/act/lessons/sync', { robot_type });
  if (!res.ok){
    if (out) out.textContent = `Sync failed: ${(res.data && res.data.error) ? res.data.error : res.status}`;
    return;
  }
  if (out) out.textContent = `Repo ${res.data.result?.action || res.data.action || 'synced'} for ${robot_type}.`;
  await refreshLessonSourceCard(robot_type);
  if (robot_type === ACTIVE_ROBOT_TYPE) {
    await refresh(robot_type);
  }
}

async function buildBundle(robotType) {
  const out = $('results');
  const robot_type = robotType || currentRobotType();
  if (out) out.textContent = `Building bundle for ${robot_type}...`;
  setBundleUI(robot_type, 'bundle-loading');

  const res = await postJSON(`/api/lessons/bundle/build?robot_type=${encodeURIComponent(robot_type)}`, {});
  if (!res.ok) {
    if (out) out.textContent = `Build failed: ${(res.data && res.data.error) ? res.data.error : res.status}`;
    await refreshLessonSourceCard(robot_type);
    return;
  }

  if (out) out.textContent = JSON.stringify(res.data, null, 2);
  await refreshLessonSourceCard(robot_type);
}

async function setCurrent(){
  const class_code = $('classSelect')?.value;
  const lesson_id = $('lessonSelect')?.value;
  const levelWrap = $('levelWrap');
  const level_id  = (levelWrap && !levelWrap.classList.contains('d-none')) ? $('levelSelect')?.value : '';
  const overwrite_student_files = !!$('chkOverwrite')?.checked;
  const preferred_robot_type = $('robotTypeSelect')?.value || DEFAULT_ROBOT_TYPE;

  const out = $('results');
  if (!class_code){
    if (out) out.textContent = 'Please choose a class first.';
    return;
  }
  if (out) out.textContent = 'Setting class lesson...';

  const res = await postJSON('/act/lessons/select', { class_code, lesson_id, level_id, overwrite_student_files, preferred_robot_type });
  if (!res.ok){
    if (out) out.textContent = `Set failed: ${(res.data && res.data.error) ? res.data.error : res.status}`;
    return;
  }
  const suffix = level_id ? `/${level_id}` : '';
  if (out) out.textContent = `Class ${class_code} lesson set: ${lesson_id}${suffix}`;
  await refresh(); // important: re-render Current lesson card from server
}

async function pushToRobots(){
  const class_code = $('classSelect')?.value;
  const lesson_id = $('lessonSelect')?.value;
  const levelWrap = $('levelWrap');
  const level_id  = (levelWrap && !levelWrap.classList.contains('d-none')) ? $('levelSelect')?.value : '';
  const start_jupyter = $('chkJupyter')?.checked;
  const overwrite_student_files = !!$('chkOverwrite')?.checked;
  const preferred_robot_type = $('robotTypeSelect')?.value || DEFAULT_ROBOT_TYPE;

  const out = $('results');
  if (out) out.textContent = 'Pushing to robots...';

  const res = await postJSON('/act/lessons/push', {
    class_code,
    lesson_id,
    level_id,
    start_jupyter,
    overwrite_student_files,
    preferred_robot_type,
  });
  if (!res.ok){
    if (out) out.textContent = `Push failed: ${(res.data && res.data.error) ? res.data.error : res.status}`;
    return;
  }

  const lines = [];
  for (const r of (res.data.results || [])){
    lines.push(`${r.robot_id}: ${r.ok ? 'OK' : 'FAIL'}  ${JSON.stringify(r.actions)}`);
  }
  if (out) out.textContent = lines.join('\n') || 'No online robots.';
  await refresh(); // keep page state consistent after actions
}

document.addEventListener('DOMContentLoaded', () => {
  $('btnSelect')?.addEventListener('click', setCurrent);
  $('btnPush')?.addEventListener('click', pushToRobots);
  $('lessonSelect')?.addEventListener('change', () => populateLevelSelect({}));
  document.querySelectorAll('.btnSyncType').forEach((btn) => {
    btn.addEventListener('click', () => syncRepo(btn.dataset.robotType || DEFAULT_ROBOT_TYPE));
  });
  document.querySelectorAll('.btnBuildBundleType').forEach((btn) => {
    btn.addEventListener('click', () => buildBundle(btn.dataset.robotType || DEFAULT_ROBOT_TYPE));
  });
  $('classSelect')?.addEventListener('change', () => {
    refresh(currentRobotType());
  });
  $('robotTypeSelect')?.addEventListener('change', () => {
    ACTIVE_ROBOT_TYPE = $('robotTypeSelect')?.value || DEFAULT_ROBOT_TYPE;
    refresh(ACTIVE_ROBOT_TYPE);
  });

  refresh();
});
